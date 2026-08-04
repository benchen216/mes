# qcadoo MES — BDD Acceptance Tests (SpecFormula)

以 [SpecFormula](https://github.com/SpecFormula/specformula-dev-framework) 對 qcadoo MES
進行 BDD 驗收測試的 walking skeleton。

> ## ⚠️ 執行前必讀
>
> **SpecFormula 會在每個 scenario 結束後對 `schema.sql` 列出的資料表執行
> `TRUNCATE ... CASCADE`,`CASCADE` 會擴散清空整個資料庫。**
>
> 已實測把 qcadoo 的資料庫清空(含使用者帳號),導致無法登入。
> 詳見 [FINDINGS.md](FINDINGS.md) 的最高優先警告。
>
> **只能指向可拋棄的資料庫實例。** 絕不可指向開發或正式環境。

**完整實測發現請看 [FINDINGS.md](FINDINGS.md)** —— 包含環境需求、
qcadoo 的三步登入流程、CSRF 機制、以及已驗證/已排除的技術問題。

## 現況

| 項目 | 狀態 |
|---|---|
| qcadoo 可建置、可啟動、可登入 | ✅ 已驗證 |
| REST 端點可透過 HTTP 觸發真實業務邏輯 | ✅ 已驗證(狀態機留下稽核記錄) |
| SpecFormula 能連上 qcadoo 資料庫讀寫 | ✅ 已驗證 |
| **有 BDD scenario 對真實 qcadoo 跑出綠燈** | ✅ **13 個 scenario 全綠**(`@mvp`) |

```
mvn -f mes-bdd-tests/pom.xml test -Dcucumber.filter.tags="@mvp"
→ Tests run: 20, Failures: 0, Errors: 0, Skipped: 7
→ BUILD SUCCESS
```

涵蓋 `mes-plugins-orders` 與 `mes-plugins-production-lines` 兩個 plugin,
每條業務規則都有正例 + 反例,日期規則另有邊界例。
已用負向對照驗證不是空殼綠燈,詳見 [FINDINGS.md](FINDINGS.md)。

> **寫反例 scenario 時注意**:`with JSON: []` 是 hollow assertion,永遠會過。
> 要用 `| [0].number | &isNull |`。詳見 FINDINGS.md 對應章節。

## 環境建置(照順序做)

```bash
# 1. JDK 8(硬需求,見 EVALUATION.md §0.1)
curl -sL -o /tmp/zulu8.tar.gz \
  "https://cdn.azul.com/zulu/bin/zulu8.96.0.19-ca-jdk8.0.502-macosx_aarch64.tar.gz"
mkdir -p ~/Library/Java/JavaVirtualMachines
tar -xzf /tmp/zulu8.tar.gz -C ~/Library/Java/JavaVirtualMachines/

# 2. 建置 qcadoo
cd /path/to/mes
JAVA_HOME=$(/usr/libexec/java_home -v 1.8) mvn -pl mes-application -Ptomcat package -DskipTests

# 3. 可拋棄的 PostgreSQL(避開 5432,常被其他專案佔用)
docker run -d --name qcadoo-pg \
  -e POSTGRES_PASSWORD=postgres123 -e POSTGRES_USER=postgres -e POSTGRES_DB=mes \
  -p 5433:5432 postgres:13

# 4. ⚠️ 還原完整 schema —— 不能只靠 hbm2ddl.auto=update
#    該檔含 137 個 VIEW,少了它們看板類查詢永遠回空陣列
docker exec -i qcadoo-pg psql -U postgres -d mes \
  < mes-application/src/main/resources/schema/mes_db_en.sql

# 5. 設定並啟動 Tomcat(port 改 8090 避開 8080)
TC=mes-application/target/tomcat-archiver/mes-application
sed -i '' 's|^dbJdbcUrl=.*|dbJdbcUrl=jdbc:postgresql://localhost:5433/mes|' "$TC/qcadoo/db.properties"
sed -i '' 's|<Connector port="8080"|<Connector port="8090"|' "$TC/conf/server.xml"
JAVA_HOME=$(/usr/libexec/java_home -v 1.8) CATALINA_HOME="$TC" CATALINA_BASE="$TC" "$TC/bin/startup.sh"

# 6. 設定測試帳號密碼為 qcadoo123(BCrypt)
docker exec qcadoo-pg psql -U postgres -d mes -c \
  "UPDATE qcadoosecurity_user SET password='\$2a\$11\$V8GpDMcD0owqrDQxzMC.fu24GTIXmOpz8lZRbHHZl2tNW0fJ8A4z.', \
   enabled=true, isblocked=false, afterfirstpswdchange=true WHERE username='superadmin';"

# 7. 灌入測試參考資料
docker exec -i qcadoo-pg psql -U postgres -d mes \
  < mes-bdd-tests/src/test/resources/specs/data/seed-reference-data.sql

# 8. 跑測試
mvn -f mes-bdd-tests/pom.xml test -Dcucumber.filter.tags="@mvp"
```

## 架構

Spring 3.2 與 Spring 6 共用 `org.springframework.*` package namespace,無法在同一
classpath 共存。因此本模組**刻意獨立於 mes 主專案之外**,在獨立 JVM 中透過真實
HTTP 與 qcadoo 溝通。

```
┌──────────────────────────┐              ┌───────────────────────────┐
│  mes-bdd-tests           │              │  qcadoo MES               │
│  JVM 17+ / Spring Boot 3 │   HTTP       │  JVM 8 / Tomcat           │
│  SpecFormula             │ ───────────→ │  Spring 3.2 / javax       │
│  QcadooSessionHttpClient │  /rest/*     │                           │
└──────────────────────────┘              └───────────────────────────┘
              │                                        │
              │  JDBC(entity_setup / entity_validate)  │
              └────────────────────────────────────────┴──→ PostgreSQL
                                                     (qcadoo 實際使用的那一個)
```

本模組**沒有**註冊進 mes 根 `pom.xml` 的 `<modules>`,也**沒有**繼承
`qcadoo-super-pom`,避免被 qcadoo 的 Java 8 建置流程拉進去。

## 執行

前置需求:JDK 17+、執行中的 qcadoo 實例(見上方環境建置)。

```bash
# 全部 @mvp scenario(13 條,需要 qcadoo)
mvn -f mes-bdd-tests/pom.xml test -Dcucumber.filter.tags="@mvp"

# 指定位址與帳密(預設 8090 / superadmin / qcadoo123)
mvn -f mes-bdd-tests/pom.xml test \
    -Dqcadoo.base.url=http://localhost:8090 \
    -Dqcadoo.db.url=jdbc:postgresql://localhost:5433/mes
```

不要用 `-Dcucumber.features` 過濾 —— 會與 suite 的 `@SelectClasspathResource`
衝突導致 feature 載入兩次,第二次的 DataSource 已釋放。要用 tag。

## 檔案說明

```
mes-bdd-tests/
├── pom.xml                  獨立 pom,不繼承 qcadoo-super-pom
├── README.md                本檔:環境建置與執行
├── FINDINGS.md              實測發現(踩坑、根因、已驗證/已排除)
├── EVALUATION.md            工作量評估(寫於實測之前,需搭配 FINDINGS 讀)
└── src/test/
    ├── java/com/qcadoo/mes/bdd/
    │   ├── BddTestConfiguration.java            Spring context:HttpClientAdapter + DataSource
    │   ├── CucumberSpringContext.java           Cucumber ↔ Spring 綁定
    │   ├── QcadooSessionHttpClientAdapter.java  三步登入 + JSESSIONID + CSRF
    │   └── RunCucumberTest.java                 JUnit Platform Suite 入口
    └── resources/
        ├── isa.yml                              SpecFormula 主設定
        ├── specs/api/qcadoo-orders.openapi.yml  手寫 OpenAPI spec(path 需含 /rest)
        ├── specs/data/schema.sql                ⚠️ 決定 TRUNCATE CASCADE 範圍
        ├── specs/data/seed-reference-data.sql   參考資料(刻意不受 TRUNCATE 影響)
        ├── specs/data/entity_to_table_mapping.yml
        └── features/
            ├── 看板訂單查詢.feature              11 條:狀態/active/日期區間
            ├── 訂單建立.feature                  2 條:寫入操作
            └── 生產線查詢.feature                1 條:跨 plugin + 環境健檢
```

## 建置過程中踩到的坑

記錄下來供後續參考,這些都不在框架 README 裡:

1. **`entity_setup` 需要顯式宣告 `data_format`**
   README 說只有 `response_validate` 需要,但已安裝的版本(0.0.0-SNAPSHOT)
   對所有 instruction 都要求(ADR-0027 §3.1)。錯誤訊息:
   `effective data_format 'none' is not supported by Instruction 'entity_setup'`

2. **本專案刻意不用 Testcontainers**
   早期版本用 Testcontainer 另起空白資料庫,但那樣 `entity_setup` 寫入的資料
   qcadoo 根本看不到,綠燈只證明測試框架能讀寫自己寫的東西。
   現在直接指向 qcadoo 使用的資料庫(見 `BddTestConfiguration.dataSource()`)。

   (若日後要改回 Testcontainers:Spring Boot 3.2.0 綁的 1.19.3 其 docker-java
   client API 是 1.32,對不上現代 Docker Engine 要求的 1.40。需把
   `testcontainers-bom` 2.0.5 的 import 排在 `spring-boot-dependencies` **之前**,
   且 2.x artifact 已改名為 `testcontainers-postgresql`。)

3. **`>contextKey` 必須與 `<executionKey` 成對出現**
   這是 `specformula-dsl` 的 table expansion 語法(兩列 header 形式),
   不能單獨使用 `>`。walking skeleton 階段先用明確 id 迴避。
   錯誤訊息:`[SYMBOL_VAR_BINDINGS_MISMATCH]`

4. **不要用 `-Dcucumber.features` 過濾,要用 tag**
   `RunCucumberTest` 上的 `@SelectClasspathResource("features")` 與
   `cucumber.features` 系統屬性會衝突,導致 feature 被載入兩次,
   第二次的 DataSource 已釋放:
   `[SpecFormulaBridge] ConnectionSupplier 未初始化。可用的 DataSource: []`

   改用 `-Dcucumber.filter.tags="@mvp"`。

5. **schema.sql 不需要手寫也不需要跑起 qcadoo**
   `mes-application/src/main/resources/schema/mes_db_en.sql` 已經是完整的
   PostgreSQL dump(661 個 CREATE TABLE)。直接抽取需要的表即可。
   但原始 dump 把 sequence 與 PK 拆散在檔案各處,需另外補上。

## 已知限制與後續工作

### 端點覆蓋不足(最高優先)

`PUT /rest/dashboardKanban/updateOrderState/{orderId}` 的目標狀態**寫死在程式碼中**:

```java
// DashboardKanbanController.java
String targetState = OrderState.IN_PROGRESS.getStringValue();
if (OrderState.IN_PROGRESS.getStringValue().equals(order.getStringField(OrderFields.STATE))) {
    targetState = OrderState.COMPLETED.getStringValue();
}
```

因此只能測 `01pending → 03inProgress` 與 `03inProgress → 04completed` 兩條路徑。
狀態機的 7 個狀態與其餘轉換規則(DECLINED、INTERRUPTED、ABANDONED)全部測不到。

**建議**:新增可指定 `targetState` 的端點。底層
`stateChangeContextBuilder.build(describer, order, targetState)` 本來就吃任意目標狀態,
只是現有 controller 沒有把它開放出來。估計 1 人天,是投報率最高的一項。

### 其他

- **Hibernate 二階層快取**:qcadoo 設定 `hibernateUseSecondLevelCache=true`。
  透過 JDBC 直接寫入的資料,執行中的 qcadoo 未必看得見。需實測確認,
  必要時改以 API 準備資料而非 `entity_setup`。
- **schema.sql 漂移**:qcadoo 用 `hibernateHbm2ddlAuto=update`,schema 由
  model XML 在執行期產生。抽取出來的 DDL 會隨 model XML 變更而過期,
  長期需要建立重新匯出的 CI 流程。
- **變數綁定**:目前用明確 id,可讀性與隔離性都不理想。後續應改用
  `>contextKey` / `<executionKey` 的成對語法。
