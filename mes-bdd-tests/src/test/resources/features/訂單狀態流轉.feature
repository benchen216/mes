@api
Feature: 訂單狀態流轉

  對「執行中的 qcadoo 實例」測試,資料庫直接指向 qcadoo 使用的那一個
  (localhost:5433/mes),因此 entity_setup 寫入的資料 qcadoo 讀得到。

  受測端點: PUT /rest/dashboardKanban/updateOrderState/{orderId}

  已實測確認的行為(qcadoo 3.1.17):
  - 狀態機由 orderStateChangeAspect 驅動,每次嘗試都會在
    orders_orderstatechange 留下稽核記錄
  - 驗證未通過時 status = 04failure,訂單 state 保持不變
  - 驗證細節寫入 states_message

  Scenario: 缺少必要欄位的訂單無法推進狀態
    # 這是真實的業務規則:OrderStateValidationService.validationOnInProgress
    # 要求 dateFrom / dateTo / technology 等欄位齊備才允許進入 03inProgress。
    # entityversion 是 qcadoo 樂觀鎖欄位,執行期 schema 為 NOT NULL 且無預設值,
    # 必須顯式給值(從 mes_db_en.sql 抽取的 DDL 標了 DEFAULT 0,與執行期不一致)。
    Given 準備一個訂單, with table:
      | id   | number      | name         | state     | plannedquantity | product_id | active | entityversion |
      | 9201 | ORD-BDD-201 | 驗證測試訂單 | 01pending | 10.00000        | 9001       | true   | 0             |
    When (No Actor) 推進訂單狀態, call table:
      | orderId |
      | 9201    |
    Then 應該存在一個訂單, with table:
      | number      | state     |
      | ORD-BDD-201 | 01pending |
    And 應該存在一個訂單狀態變更, with table:
      | sourcestate | targetstate  | status    |
      | 01pending   | 03inProgress | 04failure |
