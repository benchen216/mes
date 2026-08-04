package com.qcadoo.mes.bdd;

import javax.sql.DataSource;

import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.context.annotation.Bean;
import org.springframework.jdbc.datasource.DriverManagerDataSource;

import ai.specformula.core.http.HttpClientAdapter;

/**
 * 測試用的 Spring Boot 應用程式。
 *
 * <p>這個 Spring context 「不是」 qcadoo —— 它只是承載 SpecFormula 所需的
 * bean(HttpClientAdapter、Testcontainer 建立的 DataSource)。受測的 qcadoo
 * 執行在完全獨立的 JVM 與 Tomcat 中。
 *
 * <p>{@code SpecFormulaSpringConfiguration} 會以下列優先序自動偵測 HTTP client:
 * <ol>
 *   <li>使用者自訂的 {@link HttpClientAdapter} bean ← 本類別提供的就是這個</li>
 *   <li>MockMvc(同 JVM,本情境不適用)</li>
 *   <li>TestRestTemplate</li>
 * </ol>
 */
@SpringBootApplication
public class BddTestConfiguration {

    @Bean
    public HttpClientAdapter qcadooHttpClientAdapter() {
        String baseUrl = System.getProperty("qcadoo.base.url", "http://localhost:8090");
        String username = System.getProperty("qcadoo.username", "superadmin");
        String password = System.getProperty("qcadoo.password", "superadmin");

        return new QcadooSessionHttpClientAdapter(baseUrl, username, password);
    }

    /**
     * 指向**執行中的 qcadoo 實際使用的資料庫**,而不是另起一個 Testcontainer。
     *
     * <p>這一點很關鍵:若用 Testcontainer 另開一個空白資料庫,{@code entity_setup}
     * 寫入的資料 qcadoo 根本看不到,{@code entity_validate} 也只是在驗證測試框架
     * 自己寫進去的東西 —— 那樣的綠燈不代表 qcadoo 有任何行為正確。
     *
     * <p>{@code SpecFormulaSpringConfiguration} 會自動把 Spring context 中所有
     * {@code DataSource} bean 註冊給 SpecFormula,因此這裡定義即生效。
     * 對應地,{@code specformula-testcontainer} 相依已從 pom 移除。
     */
    @Bean
    public DataSource dataSource() {
        String url = System.getProperty("qcadoo.db.url", "jdbc:postgresql://localhost:5433/mes");
        String user = System.getProperty("qcadoo.db.username", "postgres");
        String password = System.getProperty("qcadoo.db.password", "postgres123");

        DriverManagerDataSource ds = new DriverManagerDataSource();
        ds.setDriverClassName("org.postgresql.Driver");
        ds.setUrl(url);
        ds.setUsername(user);
        ds.setPassword(password);
        return ds;
    }
}
