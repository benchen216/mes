package com.qcadoo.mes.bdd;

import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import org.apache.hc.client5.http.classic.methods.HttpUriRequestBase;
import org.apache.hc.client5.http.cookie.BasicCookieStore;
import org.apache.hc.client5.http.impl.classic.CloseableHttpClient;
import org.apache.hc.client5.http.impl.classic.HttpClients;
import org.apache.hc.core5.http.ContentType;
import org.apache.hc.core5.http.Header;
import org.apache.hc.core5.http.io.entity.EntityUtils;
import org.apache.hc.core5.http.io.entity.StringEntity;
import org.apache.hc.core5.net.URIBuilder;

import ai.specformula.core.http.HttpClientAdapter;
import ai.specformula.core.http.TestRequest;
import ai.specformula.core.http.TestResponse;

/**
 * 針對 qcadoo MES 的 HttpClientAdapter。
 *
 * <p>存在的理由:SpecFormula 內建的 {@code Authenticator} 合約是回傳 token 並以
 * {@code Authorization: Bearer {token}} 送出,但 qcadoo 使用 Spring Security 3.2 的
 * form login + {@code JSESSIONID} cookie。兩者不相容,因此以自訂 adapter 承接
 * cookie-based session。
 *
 * <p>運作方式:
 * <ol>
 *   <li>首次請求時 lazy 登入 —— POST {@code /j_spring_security_check}
 *       帶 {@code j_username} / {@code j_password}(form-urlencoded)</li>
 *   <li>登入成功後 {@link BasicCookieStore} 持有 JSESSIONID</li>
 *   <li>後續所有請求自動帶上該 cookie</li>
 * </ol>
 *
 * <p>注意:此 adapter 走真實 HTTP,受測的 qcadoo 執行在獨立的 JVM(Java 8 / Spring 3.2)。
 * 這是刻意的架構選擇 —— Spring 3.2 與 Spring 6 共用
 * {@code org.springframework.*} namespace,無法在同一 classpath 共存。
 */
public class QcadooSessionHttpClientAdapter implements HttpClientAdapter {

    private static final String LOGIN_PATH = "/j_spring_security_check";
    private static final String LOGIN_PAGE = "/login.html";
    private static final String CSRF_HEADER = "X-CSRF-TOKEN";

    /** 抓登入頁的 {@code <meta name="_csrf" content="...">}。 */
    private static final Pattern CSRF_META =
            Pattern.compile("<meta\\s+name=\"_csrf\"\\s+content=\"([^\"]+)\"");

    private final String baseUrl;
    private final String username;
    private final String password;

    private final BasicCookieStore cookieStore = new BasicCookieStore();
    private final CloseableHttpClient httpClient;

    private volatile boolean loggedIn = false;
    private volatile String csrfToken;

    public QcadooSessionHttpClientAdapter(String baseUrl, String username, String password) {
        this.baseUrl = stripTrailingSlash(baseUrl);
        this.username = username;
        this.password = password;
        this.httpClient = HttpClients.custom()
                .setDefaultCookieStore(cookieStore)
                .build();
    }

    @Override
    public TestResponse execute(TestRequest request) throws Exception {
        ensureLoggedIn();

        URIBuilder uriBuilder = new URIBuilder(baseUrl + request.getUrl());
        for (Map.Entry<String, String> param : request.getQueryParams().entrySet()) {
            uriBuilder.addParameter(param.getKey(), param.getValue());
        }
        URI uri = uriBuilder.build();

        HttpUriRequestBase httpRequest = new HttpUriRequestBase(request.getMethod(), uri);

        for (Map.Entry<String, List<String>> header : request.getHeaders().entrySet()) {
            for (String value : header.getValue()) {
                httpRequest.addHeader(header.getKey(), value);
            }
        }

        // CustomCsrfRequestMatcher 只豁免 GET 與少數路徑白名單,
        // 因此所有非 GET 請求都補上 CSRF token(多送不會有副作用)。
        if (!"GET".equalsIgnoreCase(request.getMethod()) && csrfToken != null) {
            httpRequest.addHeader(CSRF_HEADER, csrfToken);
        }

        if (request.getBody() != null) {
            httpRequest.setEntity(new StringEntity(request.getBody(), ContentType.APPLICATION_JSON));
        }

        return httpClient.execute(httpRequest, response -> {
            String body = response.getEntity() == null
                    ? ""
                    : safeEntityToString(response);

            Map<String, List<String>> responseHeaders = new LinkedHashMap<>();
            for (Header h : response.getHeaders()) {
                responseHeaders
                        .computeIfAbsent(h.getName(), k -> new ArrayList<>())
                        .add(h.getValue());
            }
            return new TestResponse(response.getCode(), body, responseHeaders);
        });
    }

    /**
     * qcadoo 的登入是三步流程,不是單純 POST 帳密。這是實測 qcadoo 3.1.17
     * 得出的結果,三步缺一不可:
     *
     * <ol>
     *   <li><b>GET {@code /login.html}</b> —— 建立 session。qcadoo 的
     *       {@code SessionExpirationFilter} 會擋掉沒有既存 session 的登入請求,
     *       直接把它導向 {@code /login.html?timeout=true}。</li>
     *   <li><b>抽出 CSRF token</b> —— 登入頁的 meta 標籤帶著
     *       {@code <meta name="_csrf" content="...">}。qcadoo 啟用了 CSRF
     *       ({@code CustomCsrfRequestMatcher}),沒帶 token 會得到 403。</li>
     *   <li><b>POST 登入</b> —— 帳密加上 {@code _csrf} 參數。</li>
     * </ol>
     *
     * <p>token 會保留下來,供後續非 GET 請求以 {@code X-CSRF-TOKEN} header 送出
     * ——{@code CustomCsrfRequestMatcher} 只豁免 GET 與少數路徑白名單,
     * {@code /rest/dashboardKanban/**} 不在其中。
     */
    private void ensureLoggedIn() throws Exception {
        if (loggedIn) {
            return;
        }
        synchronized (this) {
            if (loggedIn) {
                return;
            }

            // 步驟 1 + 2:GET 登入頁,建立 session 並取得 CSRF token。
            // 連線層失敗會在這裡拋 HttpHostConnectException,訊息已足夠清楚。
            HttpUriRequestBase loginPage = new HttpUriRequestBase("GET", URI.create(baseUrl + LOGIN_PAGE));
            String loginHtml = httpClient.execute(loginPage, response -> {
                if (response.getCode() == 404) {
                    throw new IllegalStateException(
                            "qcadoo 登入失敗:" + LOGIN_PAGE + " 回應 404 —— " + baseUrl
                                    + " 上跑的很可能不是 qcadoo,而是別的應用佔用了這個 port。"
                                    + "請用 `lsof -nP -iTCP:<port> -sTCP:LISTEN` 確認。");
                }
                return safeEntityToString(response);
            });

            csrfToken = extractCsrfToken(loginHtml);
            if (csrfToken == null) {
                throw new IllegalStateException(
                        "qcadoo 登入失敗:在 " + LOGIN_PAGE + " 找不到 <meta name=\"_csrf\"> —— "
                                + "頁面內容不像 qcadoo 的登入頁,請確認 " + baseUrl + " 指向正確的實例。");
            }

            // 步驟 3:帶著 session 與 CSRF token 送出帳密。
            HttpUriRequestBase login = new HttpUriRequestBase("POST", URI.create(baseUrl + LOGIN_PATH));
            String form = "j_username=" + urlEncode(username)
                    + "&j_password=" + urlEncode(password)
                    + "&_csrf=" + urlEncode(csrfToken);
            login.setEntity(new StringEntity(form, ContentType.APPLICATION_FORM_URLENCODED));

            int statusCode = httpClient.execute(login, response -> {
                EntityUtils.consumeQuietly(response.getEntity());
                return response.getCode();
            });

            // 成功時 qcadoo 回 200;失敗會 302 導回 /login.html(帶 timeout 或錯誤參數),
            // 或在 CSRF 不符時回 403。
            if (statusCode == 403) {
                throw new IllegalStateException(
                        "qcadoo 登入失敗:HTTP 403 —— CSRF token 被拒。token 可能已過期,"
                                + "或 session cookie 未正確帶上。");
            }
            if (statusCode != 200) {
                throw new IllegalStateException(
                        "qcadoo 登入失敗:HTTP " + statusCode + " —— 預期 200。"
                                + "請確認帳密正確(目前使用者:" + username + ")。"
                                + "qcadoo 登入失敗時會 302 導回 " + LOGIN_PAGE + "。");
            }

            loggedIn = true;
        }
    }

    private static String extractCsrfToken(String html) {
        if (html == null) {
            return null;
        }
        Matcher m = CSRF_META.matcher(html);
        return m.find() ? m.group(1) : null;
    }

    private static String safeEntityToString(org.apache.hc.core5.http.ClassicHttpResponse response) {
        try {
            return EntityUtils.toString(response.getEntity(), StandardCharsets.UTF_8);
        } catch (Exception e) {
            return "";
        }
    }

    private static String urlEncode(String value) {
        return java.net.URLEncoder.encode(value, StandardCharsets.UTF_8);
    }

    private static String stripTrailingSlash(String url) {
        return url.endsWith("/") ? url.substring(0, url.length() - 1) : url;
    }
}
