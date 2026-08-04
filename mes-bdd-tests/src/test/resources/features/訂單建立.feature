@api @mvp
Feature: 訂單建立

  對「執行中的 qcadoo 實例」測試**寫入**操作 —— 前面的 feature 都是查詢。

  受測端點: POST /rest/order
  受測程式碼路徑:
    OrdersApiController.saveOrder()
      → OrderCreationService.createOrder()
        → NumberGeneratorService(自動產生訂單編號)
        → OrderHooksPC.onCreate()(用 technology 的值覆寫生產記錄設定)
        → DataDefinition.save() → 全部 model 層 validatesWith / onCreate hook
        → BasicProductionCountingService(建立生產計數量)
        → stateChangeContextBuilder + orderStateChangeAspect.changeState()
          → 嘗試把訂單推進到 02accepted

  這條路徑觸發的 qcadoo 程式碼遠多於查詢類端點,包含狀態機本身。

  前置資料由 specs/data/seed-reference-data.sql 建立 —— technology 9501
  必須具備完整結構(生產記錄設定、作業元件、產出元件、倉庫),
  否則會踩到 hook 覆寫與 NPE。細節見該檔註解。

  註:訂單編號由 NumberGeneratorService 自動產生且跨測試遞增,
  因此**刻意不斷言 number 的具體值**,只驗證業務結果。

  Scenario: 帶完整資料建立訂單會自動推進到已接受
    # 01pending → 02accepted 的轉換要求 dateFrom / dateTo,
    # OrderCreationService 會把 request 的 startDate / finishDate 寫進去。
    When (No Actor) 建立訂單, call table:
      | productId | quantity | technologyId | description | typeOfProductionRecording | startDate                    | finishDate                   |
      | 9001      | 7        | 9501         | BDD 建立測試 | 02cumulated               | 2026-08-05T08:00:00.000+0000 | 2026-08-06T17:00:00.000+0000 |
    Then 建立訂單(200)回應, with table:
      | code | order.state | order.plannedQuantity | order.typeOfProductionRecording |
      | OK   | 02accepted  | 7.00000               | 02cumulated                     |

  Scenario: 沒有起訖日的訂單建得起來但停在待處理
    # 這是真實的業務規則:訂單本身可以沒有日期,
    # 但狀態機的 01pending → 02accepted 驗證要求 dateFrom / dateTo
    # (證據:states_message 的 orders.order.orderStates.fieldRequired)。
    # 因此 API 回的是「建立成功但無法接受」而非「建立失敗」。
    When (No Actor) 建立訂單, call table:
      | productId | quantity | technologyId | description   | typeOfProductionRecording |
      | 9001      | 3        | 9501         | 無日期測試    | 02cumulated               |
    Then 建立訂單(200)回應, with table:
      | code    |
      | &isNull |
