# qcadoo MES 接入 SpecFormula 可行性評估報告

**調查範圍**:`mes-plugins-orders` 為主,輔以全 repo 掃描
**調查日期**:2026-08-04
**目標**:評估「手寫 OpenAPI spec + 加一層 REST controller」的具體工作量,以及接入 SpecFormula 測試框架的可行性

---

## 一、結論摘要

**前一輪的結論需要修正。** 實際挖進程式碼後發現:**qcadoo 已經有一層真正的 REST API 雛形**,而且**業務邏輯(含狀態機)可以脫離 view 層呼叫**。

這把整件事的性質從「從零打造 API 層」改成「補完既有 API + 補寫 spec」,工作量差一個量級。

| 項目 | 原本假設 | 實際發現 |
|---|---|---|
| REST API | 沒有,只有 AJAX 輔助端點 | **有 `/rest/*` 命名空間、`*ApiController` 家族、乾淨的 Request/Response DTO** |
| 業務邏輯可否 headless 呼叫 | 疑似綁死 view 框架 | **可以**。狀態機透過 `StateChangeContext` 驅動,不需要 `ViewDefinitionState` |
| 狀態流轉 API | 不存在 | **已存在** `PUT /rest/dashboardKanban/updateOrderState/{orderId}` |
| 主要工作 | 建 API 層 | 補 endpoint + 手寫 OpenAPI spec + 認證改造 |

**但阻礙依然存在**,只是換了位置:不再是「有沒有 API」,而是「API 覆蓋率不足」+「認證模型不合」+「Spring 3.2 工具鏈受限」。

> **本文件寫於實際把 qcadoo 跑起來之前。** 後續實測驗證了部分推論、也推翻了部分,
> 並發現若干本文未涵蓋的限制。**請搭配 [FINDINGS.md](FINDINGS.md) 一起閱讀**,
> 該文件記錄的是實測結果而非分析推論。

---

## 〇、硬性環境限制(實測確認)

這些是無法用設定繞過的限制,會直接影響工期與可行性,列在最前面。

### 0.1 必須使用 JDK 8 —— 不是編譯設定能解決的

**現象**:用 JDK 21 或 25 建置,在第一個模組就失敗:

```
[ERROR] Failed to execute goal org.codehaus.mojo:aspectj-maven-plugin:1.7:compile on project mes:
[ERROR]   Could not find artifact com.sun:tools:jar:21.0.12
[ERROR]   at specified path .../openjdk@21/.../Home/../lib/tools.jar
```

**為什麼編譯設定救不了**:根 `pom.xml:127-129` 本來就已經設好了編譯層級 ——

```xml
<artifactId>aspectj-maven-plugin</artifactId>
<version>${aspectj.maven.plugin.version}</version>   <!-- 1.7 -->
<configuration>
    <source>1.8</source>
    <target>1.8</target>
    <complianceLevel>1.8</complianceLevel>
```

問題不在「告訴編譯器產出哪個版本的 bytecode」,而在 **`aspectj-maven-plugin:1.7`
硬性依賴 `tools.jar`,而該檔案在 JDK 9 就被移除了**。已實測確認 JDK 21 與 25
的安裝目錄下都沒有此檔。這是二元條件 —— 檔案存在或不存在,沒有旗標可以調。

`--release 8` 也無效:它控制的同樣是 bytecode 產出,不會讓 `tools.jar` 出現。

**執行期還有第二道牆**:AspectJ **1.8.13** 的 weaver 讀不懂 Java 9+ 的 class file,
而 qcadoo 使用 load-time weaving(`setenv.sh` 內有
`-javaagent:aspectjweaver-1.8.13.jar`)。支援 Java 17 需要 AspectJ 1.9.7+,
Java 21 需要約 1.9.20+。即使硬換掉 Maven plugin 通過建置,這道牆仍在。

**相依版本全貌**:

| 元件 | 版本 | 年份 |
|---|---|---|
| Spring Framework | 3.2.11.RELEASE | 2014 |
| Spring Security | 3.2.5.RELEASE | 2014 |
| Hibernate | 3.6.9.Final | 2011 |
| AspectJ | 1.8.13 | 2017 |
| Tomcat(qcadoo 自帶) | 8.5.12 | 2017 |

**取得 JDK 8 的建議做法**:Azul Zulu 的 **原生 arm64** tarball,解壓到
`~/Library/Java/JavaVirtualMachines/` 即可,免 sudo 且 `/usr/libexec/java_home`
會自動辨識。不建議 `brew install --cask temurin@8` —— Temurin 8 的 macOS 版
只有 x64(需經 Rosetta),且安裝需要 sudo 密碼。

實測結果:JDK 8 下 `mvn package` **BUILD SUCCESS**,產出 93MB WAR + 55 個 plugin jar。

### 0.2 `hbm2ddl.auto=update` 無法產生可用的資料庫

qcadoo 的 `db.properties` 預設 `hibernateHbm2ddlAuto=update`,首次啟動會建出
488 張表。**但這樣的資料庫是不完整的** —— qcadoo 的唯讀 DTO 模型
(`orderListDto` 等,宣告為 `insertable="false" updatable="false"`)在正式環境
是**資料庫 VIEW**,而 Hibernate 的 `hbm2ddl` 只會把它們建成空的資料表。

實測:完整的 `mes_db_en.sql` 內含 **137 個 `CREATE VIEW`**,包含
`orders_orderlistdto`。少了這些 view,所有看板/清單類查詢都只會回空陣列。

**正確做法**:從 `mes-application/src/main/resources/schema/mes_db_en.sql`
還原完整 schema(661 張表 + 137 個 view + seed 資料),再啟動 qcadoo
讓 `hbm2ddl.auto=update` 做增量調整。

---

## 二、重大發現

### 2.1 `/rest/*` 命名空間已經就緒

`mes-application/src/main/webapp/WEB-INF/web.xml` 的 DispatcherServlet 設定:

```xml
<servlet>
    <servlet-name>Qcadoo MES</servlet-name>
    <servlet-class>org.springframework.web.servlet.DispatcherServlet</servlet-class>
</servlet>
<servlet-mapping>
    <servlet-name>Qcadoo MES</servlet-name>
    <url-pattern>/rest/*</url-pattern>
</servlet-mapping>
```

且 `springSecurityFilterChain` 已掛在 `/rest/*` 上(web.xml:62-65)。

前端 JS 實際呼叫驗證:

```javascript
// dashboard.js:440
url: "/rest/dashboardKanban/updateOrderState/" + orderId

// operationalTasksDefinitionWizard.js:1135
url: 'rest/technologiesByPage'

// salesPlanOrders.js:82
url: "../../rest/masterOrders/generateOrdersSalePlan"
```

**這代表 URL 結構、servlet 路由、安全過濾器都已經到位,不需要動基礎建設。**

### 2.2 `*ApiController` 家族已存在

全 repo 有 4 個明確以 API 為名的 controller:

| 檔案 | Plugin |
|---|---|
| `OrdersApiController.java` | mes-plugins-orders |
| `TechnologyApiController.java` | mes-plugins-technologies |
| `BasicApiController.java` | mes-plugins-basic |
| `ProductionLinesApiController.java` | mes-plugins-production-lines |

`OrdersApiController` 的寫法已經是標準 REST:

```java
@Controller
public class OrdersApiController {
    @ResponseBody
    @RequestMapping(value = "/order", method = RequestMethod.POST,
                    produces = MediaType.APPLICATION_JSON_VALUE)
    public OrderCreationResponse saveOrder(@RequestBody OrderCreationRequest req) {
        return orderCreationService.createOrder(req);
    }
    // + POST /operationalTasks, POST /createTechnology
}
```

搭配專用的 DTO 套件:
- `controllers/requests/` — `OrderCreationRequest`、`TechnologyCreationRequest`
- `controllers/responses/` — `OrderCreationResponse`、`OrderResponse`、`OperationalTaskFinishResponse`、`TechnologyCreationResponse`
- `controllers/dto/` — `OrderHolder`、`OperationalTaskHolder`、`TechnologyOperationDto`

**這套 DTO 結構直接就是 OpenAPI schema 的來源,不用重新設計資料模型。**

### 2.3 業務邏輯可以 headless 執行(最關鍵的發現)

`OrderCreationService.java:204-207` —— 這是一個純 `@Service`,沒有 view 參與:

```java
final StateChangeContext orderStateChangeContext = stateChangeContextBuilder
        .build(orderStateChangeAspect.getChangeEntityDescriber(), order,
               OrderState.ACCEPTED.getStringValue());

orderStateChangeAspect.changeState(orderStateChangeContext);
```

`DashboardKanbanController.updateOrderState()` 用的是完全相同的模式,而且它是一個真正的 HTTP endpoint。

驗證邏輯也是 headless 的 —— `OrderStateValidationService` 全部接 `StateChangeContext`,不接 view:

```java
public void validationOnAccepted(final StateChangeContext stateChangeContext)
public void validationOnInProgress(final StateChangeContext stateChangeContext)
public void validationOnCompleted(final StateChangeContext stateChangeContext)
```

**結論:訂單狀態機這個最核心的業務邏輯,完全可以用 REST 包裝。** 這推翻了前一輪「業務邏輯綁死在 view 框架」的推測。

### 2.4 訂單狀態機全貌

`OrderState.java` 定義的合法轉換:

```
PENDING ──→ ACCEPTED, IN_PROGRESS, DECLINED
ACCEPTED ──→ IN_PROGRESS, DECLINED
IN_PROGRESS ──→ COMPLETED, INTERRUPTED, ABANDONED
INTERRUPTED ──→ ABANDONED, IN_PROGRESS
COMPLETED / DECLINED / ABANDONED ──→ (終態)
```

這是一個清楚、封閉、可測的狀態機 —— **非常適合寫成 BDD scenario**,而且每條轉換規則都有正例/反例可以列。

---

## 三、現況盤點(量化)

### 全 repo endpoint 統計

| 指標 | 數量 |
|---|---|
| `@Controller` | 79 |
| `@RestController` | 0 |
| `@RequestMapping` 總數 | 231 |
| `produces = APPLICATION_JSON_VALUE` | 92 |
| 使用 `@RequestBody` 的檔案 | 14 |
| `@ResponseBody` 的檔案 | 36 |
| 回傳 `ModelAndView`(JSP)的檔案 | 30 |

**可文件化的 API 表面約 92 個 endpoint**,其中真正的業務操作(非 grid/lookup 輔助)估計 20-30 個。

### mes-plugins-orders 規模

| 指標 | 數量 |
|---|---|
| Java 檔案 | 217 |
| 程式碼行數 | 22,475 |
| model XML | 41 |
| view XML | 35 |
| controller 檔案 | 9(+ dto/requests/responses/dataProvider 子套件) |
| `order.xml` 欄位定義 | 73 個具名項目(約 38 個 field) |

### orders plugin 現有 endpoint 清單

| Method | Path | 用途 | 業務價值 |
|---|---|---|---|
| POST | `/rest/order` | 建立訂單 | ⭐⭐⭐ 高 |
| POST | `/rest/operationalTasks` | 建立訂單+工序任務 | ⭐⭐⭐ 高 |
| POST | `/rest/createTechnology` | 建立技術規範 | ⭐⭐⭐ 高 |
| PUT | `/rest/dashboardKanban/updateOrderState/{orderId}` | 狀態流轉 | ⭐⭐⭐ 高 |
| GET | `/rest/dashboardKanban/ordersPending` | 待處理訂單 | ⭐⭐ 中 |
| GET | `/rest/dashboardKanban/ordersInProgress` | 進行中訂單 | ⭐⭐ 中 |
| GET | `/rest/dashboardKanban/ordersCompleted` | 已完成訂單 | ⭐⭐ 中 |
| GET | `/rest/dashboardKanban/operationalTasks*` | 工序任務(3 個) | ⭐⭐ 中 |
| POST | `/rest/orders/multiUploadFilesForOrder` | 附件上傳 | ⭐ 低 |
| GET | `/rest/orders/packsLabels.pdf` | 標籤列印 | ⭐ 低 |

**已有 4 個高價值業務 endpoint 可以立刻寫 spec 並測試。**

---

## 四、缺什麼 —— 需要補的部分

### 4.1 API 覆蓋率缺口

現有 API 能做的:建立訂單、推進狀態、查列表。
**做不到的**(需新增 endpoint):

| 缺少的操作 | 難度 | 說明 |
|---|---|---|
| `GET /rest/order/{id}` 查單筆訂單 | 低 | 現成 `OrderHolder` DTO 可用,`dashboardKanbanDataProvider.getOrder(id)` 已存在 |
| `PATCH /rest/order/{id}` 更新訂單欄位 | 中 | 需處理 73 個欄位的部分更新與驗證 |
| `DELETE /rest/order/{id}` 刪除訂單 | 中 | 需處理關聯的 hasMany(stateChanges、typeOfCorrectionCauses 等) |
| 指定目標狀態的流轉 | **低** | 現有 `updateOrderState` **寫死**在 IN_PROGRESS↔COMPLETED 之間切換,無法測 DECLINED/ABANDONED/INTERRUPTED 路徑 |
| 訂單查詢/篩選 | 中 | 有 `orderDto`、`orderListDto` model 可利用 |

**其中「指定目標狀態的流轉」是測試的關鍵缺口** —— 現有 endpoint 只能走 happy path,狀態機的 7 個狀態 × 多條轉換規則裡,大部分測不到。

補這個 endpoint 是**低難度、高價值**:底層 `stateChangeContextBuilder.build(describer, order, targetState)` 本來就吃任意 targetState,只是現有 controller 把它寫死了。

### 4.2 認證模型不合

| | qcadoo | SpecFormula 預期 |
|---|---|---|
| 機制 | Spring Security 3.2 form login | `Authenticator.getToken()` → `Authorization: Bearer {token}` |
| 憑證載體 | `JSESSIONID` cookie | HTTP header |

兩個解法:
- **A(推薦,低成本)**:自訂 `HttpClientAdapter`,登入後把 `JSESSIONID` 存在 cookie jar,每次請求帶上。介面只有一個方法,實作成本很低:
  ```java
  public interface HttpClientAdapter {
      TestResponse execute(TestRequest request) throws Exception;
  }
  ```
- **B(高成本)**:幫 qcadoo 加 token 認證。要動 Spring Security 3.2 設定,風險高、影響正式環境。

### 4.3 資料層 spec

SpecFormula 的 `entity_setup` / `entity_validate` 需要:
- `schema.sql`(DDL)
- `entity_to_table_mapping.yml`(實體名 → 資料表)

qcadoo 的狀況:`hibernateHbm2ddlAuto=update`,**schema 是 Hibernate 執行期從 model XML 自動產生的,沒有簽入版控的 DDL**。

解法:先跑起一個乾淨實例,`pg_dump -s` 匯出。但要注意這份 DDL 會跟 model XML 產生同步問題 —— model XML 一改,DDL 就過期。長期需要建立匯出流程(可放進 CI)。

---

## 五、Spring 3.2 的工具鏈限制

| 項目 | qcadoo | 說明 |
|---|---|---|
| Java | 1.8(source/target) | 見根 `pom.xml:127-128` |
| Spring Framework | **3.2.11.RELEASE**(2014) | 見 `qcadoo-super-pom-0.0.1.pom:14` |
| Spring Security | 3.2.5.RELEASE | 同上:15 |
| Servlet | `javax.servlet`(29 檔案,0 jakarta) | — |

**結論:自動產生 OpenAPI 的工具基本上都不能用。**

- `springdoc-openapi` 需要 Spring 5+ → ✗
- `springfox-swagger2` 2.x 官方需要 Spring 4.x → ✗(風險高,不建議賭)
- 舊版 `swagger-springmvc` 0.9.x 理論上支援 Spring 3.2,但已停止維護 10 年以上 → ✗

**因此手寫 OpenAPI YAML 不只是「一個選項」,而是這個情境下唯一務實的做法。**

好消息是:手寫反而規避了整個工具鏈相容性問題,而且既有的 Request/Response DTO 已經定義好資料結構,轉成 OpenAPI schema 是機械性工作。

---

## 六、工作量估算

> 假設:1 位熟悉 Java/Spring 的工程師;不含學習 qcadoo 框架的時間;不含正式環境部署。

### 階段一:最小可行驗證(Walking Skeleton)

**目標**:讓 1 條 BDD scenario 端到端跑通,證明整條鏈路可行。

| 工作項 | 人天 | 備註 |
|---|---|---|
| 建立獨立測試 module(JVM 17 / Spring Boot 3) | 0.5 | 與 qcadoo 完全隔離,不共用 classpath |
| 手寫 OpenAPI spec — 涵蓋 `POST /rest/order` | 0.5 | `OrderCreationRequest` 有 10 個欄位,DTO 現成 |
| 自訂 `HttpClientAdapter`(JSESSIONID cookie) | 1 | 含登入流程 |
| 匯出 `schema.sql` + 撰寫 `entity_to_table_mapping.yml` | 1 | orders 相關表為主 |
| 撰寫 `isa.yml` + 第一支 `.feature` | 0.5 | README 有完整範例可抄 |
| 除錯與整合 | 1.5 | 保守估計 |
| **小計** | **5 人天** | |

### 階段二:補齊訂單模組 API + spec

| 工作項 | 人天 | 備註 |
|---|---|---|
| 新增「指定目標狀態」的狀態流轉 endpoint | 1 | **高價值低成本**,底層現成 |
| 新增 `GET /rest/order/{id}` | 0.5 | DataProvider 現成 |
| 新增訂單查詢/篩選 endpoint | 1.5 | 利用現有 orderDto/orderListDto |
| 補 `PATCH` / `DELETE`(視需求) | 2-3 | 欄位多、關聯多,可延後 |
| 手寫 orders 模組完整 OpenAPI spec | 2 | 約 10-15 個 endpoint |
| 撰寫狀態機 BDD scenarios | 2 | 7 狀態 × 轉換規則,正例+反例 |
| **小計** | **9-11 人天** | |

### 階段三:擴展到其他模組(選配)

| 範圍 | 人天 | 備註 |
|---|---|---|
| technologies + basic + production-lines(3 個既有 ApiController) | 5-8 | 已有 API 基礎,主要是寫 spec |
| 其餘 52 個 plugin | — | **不建議全做**。多數沒有 API 層,成本不成比例 |

### 總計

| 情境 | 人天 | 產出 |
|---|---|---|
| **僅驗證可行性** | **5** | 1 條 scenario 跑通,決策依據 |
| **訂單模組可測** | **14-16** | orders 模組完整 BDD 覆蓋 |
| **四個 API 模組可測** | **19-24** | 涵蓋現有全部 ApiController |

---

## 七、風險與未知數

| 風險 | 影響 | 緩解 |
|---|---|---|
| **Spring 3.2 的 Jackson 版本可能過舊** | 中 | SpecFormula 要求 Jackson 2.13+,但那是**測試端**的要求,qcadoo 端只要能吐 JSON 即可。跨 JVM 架構下不衝突 |
| **`hbm2ddl.auto=update` 導致 DDL 漂移** | 中 | 建立 CI 步驟定期重新匯出 schema.sql 並比對 |
| **既有 endpoint 沒有測試保護** | 高 | orders 的 210 個既有單元測試多半測 hooks/listeners,不測 controller。改動 controller 時要小心 |
| **狀態流轉的副作用未知** | 中 | `orderStateChangeAspect` 是 AspectJ 切面,可能觸發跨 plugin 的 listener(如 production-counting)。測試需要真實 DB,不能只 mock |
| **多租戶/權限模型** | 低 | `SecurityService` 已被 `OrderCreationService` 注入,行為需實測確認 |

**最大的未知數**:`orderStateChangeAspect.changeState()` 的實際副作用範圍。它是 AspectJ 切面,可能觸發其他 plugin 的監聽器。這需要在階段一實際跑一次才會知道。

---

## 八、與 SpecFormula 對接的架構

因為 Spring 3.2 與 Spring 6 用同一個 `org.springframework.*` package namespace,**無法在同一 classpath 共存**。唯一可行架構是跨 process:

```
┌──────────────────────────┐              ┌───────────────────────────┐
│  測試專案(獨立 module)     │              │  qcadoo MES               │
│  JVM 17                  │   HTTP       │  JVM 8 / Tomcat           │
│  Spring Boot 3           │ ───────────→ │  Spring 3.2 / javax       │
│  SpecFormula             │  /rest/*     │                           │
│  + 自訂 HttpClientAdapter │              │                           │
└──────────────────────────┘              └───────────────────────────┘
              │                                        │
              │  JDBC(entity_setup / entity_validate)  │
              └────────────────────────────────────────┴──→ PostgreSQL
```

要點:
- 用 `TestRestTemplateHttpClientAdapter` 或自訂 adapter,**不要用 MockMvc**(MockMvc 需要同 JVM)
- `isa.yml` 設 `db_type: postgresql`(框架有支援,qcadoo 確認用 PostgreSQL)
- classpath 完全不共用 → Spring 版本衝突自然消失

### isa.yml 骨架(可直接使用)

```yaml
config:
  api:
    resource_path: specs/api
    project_path: src/test/resources/specs/api
  data:
    source:
      - name: default
        resource_path: specs/data
        project_path: src/test/resources/specs/data
        db_type: postgresql

instructions:
  - name: 準備資料
    format: ^準備一個(?P<entity>[一-鿿a-zA-Z0-9_]+), with table:$
    instruction_type: entity_setup

  - name: API 呼叫
    format: ^\((?:No Actor|UID="(?P<userId>\$[\w.]+)")\) (?P<summary>.+?), call table:$
    instruction_type: api_call

  - name: 回應驗證
    format: ^(?P<summary>.+?)\((?P<status_code>\d{3})\)回應,?\s*with table:$
    instruction_type: response_validate
    data_format: data_table

  - name: 資料庫驗證
    format: ^應該存在一個(?P<entity>[一-鿿a-zA-Z0-9_]+), with table:$
    instruction_type: entity_validate
```

### 第一個 scenario 建議(狀態機正例 + 反例)

```gherkin
Feature: 訂單狀態流轉

  Scenario: 待處理訂單可以被接受
    Given 準備一個訂單, with table:
      | number | state   | product |
      | ORD-01 | pending | $產品.id |
    When (UID="$使用者.id") 變更訂單狀態, call table:
      | orderId | targetState |
      | $訂單.id | accepted    |
    Then 變更訂單狀態(200)回應, with table:
      | state    |
      | accepted |
    And 應該存在一個訂單, with table:
      | number | state    |
      | ORD-01 | accepted |

  Scenario: 已完成訂單不能再變更狀態
    Given 準備一個訂單, with table:
      | number | state     |
      | ORD-02 | completed |
    When (UID="$使用者.id") 變更訂單狀態, call table:
      | orderId | targetState |
      | $訂單.id | inProgress  |
    Then 變更訂單狀態(400)回應, with table:
      | code  |
      | ERROR |
```

> 註:這需要階段二的「指定目標狀態」endpoint 才能跑。用現有的 `updateOrderState/{orderId}` 只能測 IN_PROGRESS↔COMPLETED 的固定切換。

---

## 九、建議執行順序

1. **先做階段一的 5 人天驗證。** 不要一開始就規劃全模組。這 5 天要回答三個問題:
   - `orderStateChangeAspect` 的副作用範圍有多大?
   - JSESSIONID 認證的 adapter 好不好寫?
   - schema.sql 匯出後,`entity_setup` 能不能正常塞資料?

2. **驗證通過後,優先補「指定目標狀態」的 endpoint。** 這是投報率最高的一項:1 人天,直接把狀態機從「只能測 1 條路徑」變成「7 個狀態全可測」。

3. **範圍克制在 orders + technologies + basic + production-lines 這 4 個已有 ApiController 的模組。** 其餘 52 個 plugin 沒有 API 層,硬做成本不成比例。

4. **不要嘗試在 qcadoo 端引入 springdoc/springfox。** Spring 3.2 擋死了,手寫 YAML 才是正解,而且既有 DTO 讓這件事比想像中機械化。

---

## 附錄:關鍵檔案索引

| 檔案 | 行號 | 內容 |
|---|---|---|
| `mes-plugins/mes-plugins-orders/src/main/java/com/qcadoo/mes/orders/controllers/OrdersApiController.java` | 全檔 | REST controller 範本 |
| `.../controllers/OrderCreationService.java` | 204-207 | headless 狀態流轉證據 |
| `.../controllers/DashboardKanbanController.java` | ~48-60 | 狀態流轉 endpoint(目標狀態寫死) |
| `.../states/constants/OrderState.java` | 全檔 | 狀態機定義 |
| `.../states/OrderStateValidationService.java` | 44-64 | headless 驗證邏輯 |
| `.../controllers/requests/OrderCreationRequest.java` | 全檔 | OpenAPI schema 來源 |
| `mes-application/src/main/webapp/WEB-INF/web.xml` | 62-65, 196-200 | `/rest/*` 路由與安全過濾 |
| `~/.m2/repository/com/qcadoo/maven/qcadoo-super-pom/0.0.1/qcadoo-super-pom-0.0.1.pom` | 14-15 | Spring 3.2.11 版本來源 |
| `pom.xml` | 127-128 | Java 1.8 設定 |
| `mes-application/conf/dev/db.properties` | — | PostgreSQL + hbm2ddl.auto=update |
