# qcadoo MES × SpecFormula 實測發現

本文件記錄**實際把 qcadoo 跑起來、並讓 SpecFormula 打到它**之後驗證出來的事實。
與推測明確區分:每一條都標註了證據來源。

最後更新:2026-08-04

---

## ⚠️ 最高優先警告:不要讓 SpecFormula 指向有價值的資料庫

**已實測發生資料全毀。**

機制(已追到原始碼):SpecFormula 在**每個 scenario 結束後都會清理資料表**,
由 `BuiltinRdbLifecyclePlugin` 呼叫 `SpecFormulaBridge.truncateAllTables()`
(`specformula-spring/.../SpecFormulaBridge.java:408`)。清理範圍取自
`schema.sql` 列出的資料表,PostgreSQL 方言產生的語句是:

```java
// specformula-core/src/main/java/ai/specformula/core/dialect/PostgresDialect.java:17-19
// TRUNCATE CASCADE 自動處理 FK 約束，不需要額外 disable/enable FK
"TRUNCATE TABLE " + quotedName + " CASCADE"
```

**兩個關鍵事實**:

1. 這個清理**與 `permission: isolated` 無關**,也**不在 `specformula-testcontainer`
   模組內** —— 它在 core/spring,移除 testcontainer 相依並不會阻止它。
   (有 dirty-table tracking,只清理曾被 INSERT 的表,但這無助於降低風險。)
2. **`CASCADE` 會連鎖清空所有以外鍵參照過來的資料表。** 即使 `schema.sql`
   只列了 6 張表,破壞會擴散到整個資料庫。

實測結果:整個 qcadoo 資料庫被清空,連 `qcadoosecurity_user`(登入帳號)與
`basic_parameter`(系統參數)都歸零,qcadoo 直接無法登入。

實測數據(一次測試執行後):

```
basic_product:            0 筆
technologies_technology:  0 筆
orders_order:             0 筆
orders_orderstatechange:  0 筆
qcadoosecurity_user:      0 筆   ← 連使用者都沒了
basic_parameter:          0 筆
```

**正確做法**:qcadoo 與測試共用一個**可拋棄的**資料庫實例,且該實例永遠不放有價值的資料。
每輪測試前用固定腳本重建 schema 與 seed 資料。絕對不要指向開發或正式資料庫。

---

## 環境需求(已驗證)

| 項目 | 結論 | 證據 |
|---|---|---|
| **Java 8 是硬需求** | JDK 21/25 建置直接失敗 | `aspectj-maven-plugin:1.7` 需要 `com.sun:tools:jar`,而 `tools.jar` 在 JDK 9 已移除。已確認 JDK 21 與 25 都沒有此檔 |
| JDK 8 取得方式 | Azul Zulu 8.0.502 **原生 arm64** | Temurin 8 的 macOS 版只有 x64(需 Rosetta)。Zulu tarball 解壓到 `~/Library/Java/JavaVirtualMachines/` 即可,免 sudo,`/usr/libexec/java_home` 自動辨識 |
| 建置結果 | ✅ BUILD SUCCESS | 93MB WAR + 55 個 plugin jar;AspectJ weave 正常 |
| Tomcat | 8.5.12(qcadoo 自帶) | `-Ptomcat` 由 `qcadoo-maven-plugin` 產出完整套件 |
| 資料庫 | PostgreSQL,Hibernate 建 **488 張表** | `hbm2ddl.auto=update`,`orders_order` 有 80 個欄位 |

編譯設定救不了這件事 —— 根 `pom.xml:127-129` 本來就已經設了 `<source>1.8</source>`、
`<complianceLevel>1.8</complianceLevel>`。問題不是產出什麼 bytecode,
而是 plugin 依賴一個 JDK 9 以後不存在的檔案。

執行期還有第二道牆:AspectJ **1.8.13** 的 weaver 讀不懂 Java 9+ class file,
而 qcadoo 用 load-time weaving(`setenv.sh` 有 `-javaagent:aspectjweaver-1.8.13.jar`)。

---

## qcadoo 登入是三步流程(不是單純 POST 帳密)

這是最容易踩的坑,三步缺一不可:

```
1. GET /login.html
   → 建立 session。SessionExpirationFilter 會擋掉沒有既存 session 的登入請求,
     直接導向 /login.html?timeout=true

2. 從回應抽出 CSRF token
   → <meta name="_csrf" content="...">
     沒帶 token 會得到 HTTP 403

3. POST /j_spring_security_check
   → j_username + j_password + _csrf
     成功回 200(不是 302)
```

證據:`qcadoo-security-context.xml` 使用 `CustomAuthenticationFilter`、
`SessionExpirationFilter`、`CustomCsrfRequestMatcher`,以及登入頁實際 HTML。

### CSRF 豁免白名單

`CustomCsrfRequestMatcher` 只豁免 `^GET$` 與特定路徑,包含:

```
/integration/rest/**   /rest/warehouse/**   /rest/product/**
/rest/order/**         /rest/masterorder/** /rest/delivery/**
/rest/document/**      /rest/cmms/**        /wms/**  ...
```

**`/rest/dashboardKanban/**` 不在白名單內**,因此對它的 PUT 必須帶
`X-CSRF-TOKEN` header。

### 密碼

qcadoo 用 **BCrypt**(`$2a$11$`,60 字元)。測試環境可直接產 hash 寫入
`qcadoosecurity_user.password`。內建帳號:`superadmin`、`admin`、`qcadoo_bot`。

---

## 業務邏輯確實可透過 REST 觸發(核心結論)

**完整鏈路已實測打通**:

```
HTTP PUT → 認證 → CSRF → DispatcherServlet → DashboardKanbanController
  → stateChangeContextBuilder.build(...)
  → orderStateChangeAspect.changeState(...)
  → 狀態機驗證
  → 寫入 orders_orderstatechange 稽核記錄
```

證據 —— qcadoo 自己寫下的稽核記錄:

```
 sourcestate | targetstate  |  status   |   worker
-------------+--------------+-----------+------------
 01pending   | 03inProgress | 04failure | superadmin
```

以及對應的驗證訊息(`states_message`):

```
04validationError | orders.order.orderStates.fieldRequired  | dateTo
04validationError | orders.order.orderStates.fieldRequired  | dateFrom
04validationError | orders.order.orderStates.fieldRequired  | technology
04validationError | qcadooView.validate.field.error.missing | commissionedPlannedQuantity
```

這些是 `OrderStateValidationService.validationOnInProgress` 執行真實業務規則的結果。

### REST 路徑與 view 路徑收斂在同一行程式碼

| 路徑 | 呼叫 |
|---|---|
| View(`AbstractStateChangeViewClient`) | `stateChangeContextBuilder.build(...)` → `getStateChangeService().changeState(ctx)` → 再加 `refreshComponent` / `showMessages` |
| REST(`DashboardKanbanController`) | `stateChangeContextBuilder.build(...)` → `orderStateChangeAspect.changeState(ctx)` |

前兩行完全相同,view 只多做 UI 刷新。

model 層 hooks 兩條路徑都會觸發(`order.xml:212-223` 宣告
`validatesWith` / `onCreate` / `onSave` / `onCopy` / `onDelete` → `OrderHooks`,
而 `OrderHooks` 對 `ViewDefinitionState` 的引用次數為 **0**)。

測不到的只有 view 層 hooks(`OrderDetailsHooks` 有 **27** 處 `ViewDefinitionState`),
那些是欄位啟用/隱藏之類的 UI 行為。

**結論:走 REST 測業務邏輯是準確的,限制在覆蓋範圍而非準確度。**

---

## 已排除的疑慮

| 疑慮 | 結論 | 驗證方式 |
|---|---|---|
| Hibernate 二階層快取會讓外部寫入的資料對 qcadoo 不可見 | ❌ **不成立** | 直接 SQL 寫入的訂單,qcadoo 讀得到、驗證得到、也寫得出參照它的稽核記錄。重啟 Tomcat 清快取後行為不變 |

---

## 已解決:enum 欄位驗證失敗的根因

**先前推測「qcadoo 框架的 enum 讀取有問題」——這個推測是錯的,已推翻。**

`Entity.getStringField()` 沒有壞。反組譯 `qcadoo-model-1.5-SNAPSHOT.jar` 的
`DefaultEntity` 確認它就是單純的 map lookup,沒有 DataDefinition 介入或 enum 轉換:

```
public Object getField(String):        return fields.get(fieldName);
public String getStringField(String):  return (String) getField(fieldName);
```

### 真正的原因:hook 無條件覆寫

`OrderHooksPC.onCreate()`
(`mes-plugins-production-counting/.../hooks/OrderHooksPC.java:73-88`):

```java
Entity technology = order.getBelongsToField(OrderFields.TECHNOLOGY);
if (Objects.nonNull(technology)) {
    setOrderWithTechnologyProductionCountingValues(orderDD, order, technology);  // 無條件覆寫
} else {
    setOrderWithDefaultProductionCountingValues(orderDD, order);                 // 這條才檢查 isEmpty
}
```

fixture technology 9501 的 `typeofproductionrecording` 是 NULL,於是訂單那個欄位
被覆寫成 NULL,`validatesWith` 就報 `error.empty`。

**這同時是一個真實產品缺陷:只要 payload 帶了 `technologyId`,
REST 傳入的 `typeOfProductionRecording` 就會被靜默忽略。**

### 為什麼 log 看起來自相矛盾

`DataAccessServiceImpl.performSave()` 會先 `entity.copy()`,**hook 與驗證跑在副本上**,
失敗後才把錯誤回填到原始 entity —— 而 log 印的是**原始 entity**。所以 dump 顯示
`typeOfProductionRecording=02cumulated`,驗證器看到的副本卻是 null。

佐證:錯誤訊息裡的 entity dump **完全沒有** `registerProductionTime` /
`inputProductsRequiredForType` 等欄位,而那些正是 `onCreate` 會設定的 ——
證明印出來的是 hook 執行前的狀態。

### `inputProductsRequiredForType` 不是同一個根因

對照實驗(同一張單、只改 DB 欄位):

| 設定 | 結果 |
|---|---|
| `NULL` | PUT 失敗,state 停在 02accepted |
| `01startOrder` | PUT 成功,state → 03inProgress |

**純 SQL 設定的 enum 值可以被 `getStringField()` 正確讀到。** 當時該欄位在驗證當下就是 NULL。

### 連帶挖出的兩個程式碼缺陷

| 位置 | 問題 |
|---|---|
| `BasicProductionCountingServiceImpl.java:163-164` | `product.getBelongsToField(PARENT).getId()` 沒有 null 檢查,technology 無 operation component 時直接 NPE(回 HTTP 500) |
| `ProductionCountingQuantityValidatorsPFTD.java:89-97` | `role=02produced` + `typeOfMaterial=03finalProduct` 時 `productsInputLocation` 必填,來源是 technology 的 out-component |

### 建議的程式碼層修法(尚未套用,會改變產品行為)

- `setOrderWithTechnologyProductionCountingValues()` 應比照 default 版本,
  在 technology 該欄位為空時回退到 `parameterService.getParameter()`,
  而不是寫入 null。這同時修掉「REST 參數被靜默忽略」。
- `BasicProductionCountingServiceImpl.java:163` 應對 `getBelongsToField(PARENT)` 做 null 檢查,
  把 500 換成正常的驗證錯誤。

### Workaround(已採用)

要被下單引用的 technology 必須具備完整結構:生產記錄設定、至少一個 operation component、
產出元件、以及 `productsInputLocation`。透過 UI 或 `OrderCreationService.createTechnology()`
建立的 technology 天生滿足;**直接用 SQL 塞的 fixture 會繞過所有 hook**,必須自己補齊。

已固化在 `specs/data/seed-reference-data.sql`(含完整註解說明為什麼每一列都必要)。

---

## 尚未解決的問題

### 1. 狀態變更失敗時 REST 回應是 405 而非錯誤 JSON

`DashboardKanbanController.updateOrderState` 在狀態機拒絕後,
仍呼叫 `DashboardKanbanDataProvider.getOrder(orderId)` 組回應,
該查詢找不到資料而拋 `EmptyResultDataAccessException`,
最終被導向 JSP 錯誤頁 → `HTTP 405 JSPs only permit GET POST or HEAD`。

```
DashboardKanbanController.updateOrderState(DashboardKanbanController.java:104)
  → DashboardKanbanDataProvider.getOrder(DashboardKanbanDataProvider.java:98)
     → EmptyResultDataAccessException: Incorrect result size: expected 1, actual 0
```

這是 qcadoo 的缺陷 —— 業務失敗時無法回傳結構化錯誤給呼叫端。
BDD 測試若要驗證失敗路徑,只能透過資料庫斷言,不能靠 HTTP 回應。

### 3. 抽取的 DDL 與執行期 schema 不一致

從 `mes_db_en.sql` 抽取的 `orders_order` 定義中,`entityversion` 標了
`DEFAULT 0`;但 Hibernate 執行期產生的 schema 是 **NOT NULL 且無預設值**,
導致 `entity_setup` 未顯式給值時插入失敗。

`hbm2ddl.auto=update` 意味著 schema 由 model XML 在執行期產生,
靜態抽取的 DDL 必然會漂移。

---

## SpecFormula 框架相關踩坑

1. **`entity_setup` 需顯式宣告 `data_format`**
   README 說只有 `response_validate` 需要,但已安裝版本(0.0.0-SNAPSHOT)
   對所有 instruction 都要求(ADR-0027 §3.1)。

2. **Testcontainers 版本要覆蓋 Spring Boot 預設**
   Boot 3.2.0 綁 1.19.3,其 docker-java client API 1.32 對不上現代 Docker Engine
   要求的 1.40。`testcontainers-bom` 的 import 必須排在 `spring-boot-dependencies`
   **之前**;2.x artifact 已改名為 `testcontainers-postgresql`。

3. **`>contextKey` 必須與 `<executionKey` 成對**
   屬 `specformula-dsl` 的兩列 table expansion 語法,不能單獨使用。

4. **不能用 `-Dcucumber.features` 過濾**
   會與 suite 的 `@SelectClasspathResource` 衝突導致重複載入,
   第二輪 DataSource 已釋放。要用 `-Dcucumber.filter.tags`。

---

## 目前狀態

| 項目 | 狀態 |
|---|---|
| qcadoo 可建置、可啟動、可登入 | ✅ 已驗證 |
| REST 端點可透過 HTTP 呼叫並觸發真實業務邏輯 | ✅ 已驗證 |
| SpecFormula 能連上 qcadoo 的資料庫並讀寫 | ✅ 已驗證 |
| **有 BDD scenario 對真實 qcadoo 跑出綠燈** | ✅ **已達成** |

**注意**:先前 README 記載的「4 個資料層場景全綠」是對 Testcontainer 的空白資料庫
測試,**不涉及任何 qcadoo 程式碼**,不應計入覆蓋率。下面的 MVP 才是真的。

---

## ✅ 綠燈測試套件(已達成)

```
mvn -f mes-bdd-tests/pom.xml test -Dcucumber.filter.tags="@mvp"
→ Tests run: 20, Failures: 0, Errors: 0, Skipped: 7
→ BUILD SUCCESS
```

**13 個 scenario 全部對執行中的 qcadoo 實例跑綠**:

| Feature | Scenario | 類型 |
|---|---|---|
| 生產線查詢 | 可以取得系統預設的生產線 | 跨 plugin + 環境健檢 |
| 看板訂單查詢 | 已接受的訂單出現在待處理欄位 | 正例 |
| 看板訂單查詢 | 中斷的訂單也出現在待處理欄位 | 正例 |
| 看板訂單查詢 | 待處理狀態的訂單不會出現在待處理欄位 | 反例(記錄反直覺行為) |
| 看板訂單查詢 | 進行中的訂單出現在進行中欄位 | 正例 |
| 看板訂單查詢 | 進行中的訂單不會同時出現在待處理欄位 | 反例(集合互斥) |
| 看板訂單查詢 | 已完成的訂單出現在已完成欄位 | 正例 |
| 看板訂單查詢 | 已停用的訂單被排除 | 反例(active 篩選) |
| 看板訂單查詢 | 執行期間已結束的訂單被排除 | 反例(日期上界) |
| 看板訂單查詢 | 尚未開始的訂單被排除 | 反例(日期下界) |
| 看板訂單查詢 | 起訖日皆為今天的訂單會出現 | **邊界**(date_trunc 到天) |
| 訂單建立 | 帶完整資料建立訂單會自動推進到已接受 | **寫入**正例 |
| 訂單建立 | 沒有起訖日的訂單建得起來但停在待處理 | **寫入**反例 |

涵蓋 `getOrdersQuery()` 的三條規則,每條都有正例 + 反例,日期規則另有邊界例
—— 符合 Spec-by-Example 要求。

跨越的 plugin:`mes-plugins-orders`、`mes-plugins-production-lines`、
`mes-plugins-production-counting`(經由 hook)。

**訂單建立那兩條是寫入操作**,觸發的程式碼遠多於查詢類:
NumberGeneratorService → OrderHooksPC.onCreate → 全部 model 層 hook →
BasicProductionCountingService → 狀態機 changeState。

### 這條 scenario 實際執行到的 qcadoo 程式碼

```
QcadooSessionHttpClientAdapter(三步登入 + JSESSIONID)
  → Tomcat 8.5.12
  → springSecurityFilterChain(CustomAuthenticationFilter / CustomCsrfRequestMatcher)
  → DispatcherServlet(/rest/*)
  → DashboardKanbanController.getOrdersPending()
  → DashboardKanbanDataProvider.getOrdersPending()
      → UserService.getCurrentUserEntity()
      → ParameterService.getParameter()
      → NamedParameterJdbcTemplate 查詢 orders_orderlistdto view
  → BeanPropertyRowMapper → OrderHolder → JSON
```

### 已用負向對照驗證,不是空殼綠燈

把期望值改成不存在的編號後,測試**如預期失敗**,且錯誤訊息證明資料真的來自 qcadoo:

```
[ASSERT_RESPONSE_FIELD_MISMATCH] 回應欄位 '[0].number'
  不符預期值 'ORD-WRONG-999'，實際為 'ORD-BDD-301'
```

「實際為 `ORD-BDD-301`」—— 這正是 `entity_setup` 寫入、再由 qcadoo 的 API
查出來回傳的那筆訂單。資料確實走完了整條鏈路。

### 讓它變綠的三個關鍵修正

1. **資料庫必須從 `mes_db_en.sql` 還原,不能只靠 `hbm2ddl.auto=update`**
   —— 少了 137 個 view,看板查詢永遠回空陣列(見下方「環境需求」)。

2. **`schema.sql` 只能列 `orders_order` + `orders_orderstatechange`**
   —— 之前納入 `productionlines_productionline`,TRUNCATE CASCADE 連鎖清空了
   `qcadoosecurity_user`。參考資料改由 `seed-reference-data.sql` 在測試外部建立。

3. **OpenAPI 的 path 必須含 `/rest` 前綴**
   —— SpecFormula 不套用 `servers[].url`,path 要寫成 `/rest/dashboardKanban/...`。

### ⚠️ 空陣列不能用 `with JSON: []` 斷言 —— 那是 hollow assertion

實測踩到的坑,而且**負向對照才抓得出來**:

```gherkin
# ❌ 錯誤:這條永遠會過,即使清單其實有資料
Then 查詢待處理訂單(200)回應為, with JSON:
  """
  []
  """
```

原因:`JsonResponseValidate` 會把期望的 JSON 扁平化成 headers + values 再委派給
`DataTableResponseValidate`。`[]` 扁平化後是**零個欄位**,等於沒有任何內容斷言,
只剩 HTTP 狀態碼被檢查。

也試過空 header 搭 `&size(0)`,同樣無效(路徑解析不到根節點)。

```gherkin
# ✅ 正確:用 &isNull 斷言第一筆不存在
Then 查詢待處理訂單(200)回應, with table:
  | [0].number |
  | &isNull    |
```

雙向驗證過:清單為空時通過,清單有資料時失敗並明確報出

```
[ASSERT_CAS_CONSTRAINT_FAILED] JSON Path '[0].number' 之約束 '&isNull' 驗證失敗（實際值：ORD-BDD-303）
```

順帶一提,字串字面值 `null` 也**不等於**實際的 null —— 會得到
`不符預期值 'null'，實際為 'null'` 這種看起來很荒謬的錯誤。必須用 `&isNull`。

**教訓**:每一條斷言「不存在/為空」的 scenario,都要跑一次負向對照
(把資料改成應該出現),確認它真的會紅。否則很容易寫出一整組永遠綠的假測試。

### 端點命名的陷阱

`ordersPending` 這個名字有誤導性 —— `DashboardKanbanDataProvider.getOrdersPending()`
實際篩選的是 `02accepted` 與 `06interrupted`,**不含 `01pending`**,
且要求 `startdate <= 今天 <= finishdate`。光看名字會猜錯,必須讀原始碼。
