@api @mvp
Feature: 訂單狀態流轉

  測試 qcadoo 的**訂單狀態機** —— MES 最核心的業務邏輯。

  受測端點: PUT /rest/dashboardKanban/updateOrderState/{orderId}
  受測程式碼路徑:
    DashboardKanbanController.updateOrderState()
      → stateChangeContextBuilder.build(describer, order, targetState)
      → orderStateChangeAspect.changeState(ctx)        ← AspectJ 切面驅動的狀態機
        → OrderStateValidationService.validationOnInProgress / onCompleted
        → 各 plugin 註冊的 state change listener
      → 寫入 orders_orderstatechange 稽核記錄 + states_message
      → DashboardKanbanDataProvider.getOrder() 組回應

  狀態機定義(OrderState.java):

    01pending    ──→ 02accepted, 03inProgress, 05declined
    02accepted   ──→ 03inProgress, 05declined
    03inProgress ──→ 04completed, 06interrupted, 07abandoned
    06interrupted──→ 07abandoned, 03inProgress
    04completed / 05declined / 07abandoned ──→ (終態)

  ⚠️ 端點的目標狀態寫死在程式碼中:
      當前為 03inProgress → 轉 04completed
      否則                 → 一律轉 03inProgress
  因此只能測到這兩條路徑。要覆蓋完整狀態機,需新增可指定 targetState 的端點。

  註:訂單必須具備完整欄位才能通過狀態機驗證 ——
  technology / dateFrom / dateTo / commissionedPlannedQuantity /
  typeOfProductionRecording / inputProductsRequiredForType 缺一不可。
  這些是實測逐一撞出來的,每個都對應 states_message 裡的一條驗證訊息。

  Scenario: 已接受的訂單可以推進到進行中
    Given 準備一個訂單, with table:
      | id   | number     | name       | state      | plannedquantity | commissionedplannedquantity | product_id | technology_id | productionline_id | typeofproductionrecording | inputproductsrequiredfortype | datefrom        | dateto          | startdate       | finishdate      | active | entityversion |
      | 9401 | ORD-SM-401 | 狀態機測試 | 02accepted | 5.00000         | 5.00000                     | 9001       | 9501          | 1                 | 02cumulated               | 01startOrder                 | @time("now-1d") | @time("now+2d") | @time("now-1d") | @time("now+2d") | true   | 0             |
    When (No Actor) 推進訂單狀態, call table:
      | orderId |
      | 9401    |
    Then 推進訂單狀態(200)回應, with table:
      | order.state  |
      | 03inProgress |
    And 應該存在一個訂單狀態變更, with table:
      | sourcestate | targetstate  | status       |
      | 02accepted  | 03inProgress | 03successful |

  Scenario: 沒有產出的進行中訂單不能完成
    # 真實業務規則:已開始生產但沒有任何產出的訂單不允許結案,
    # 必須改為「已放棄」。qcadoo 會把理由放進回應的 message 欄位。
    Given 準備一個訂單, with table:
      | id   | number     | name       | state        | plannedquantity | commissionedplannedquantity | donequantity | product_id | technology_id | productionline_id | typeofproductionrecording | inputproductsrequiredfortype | datefrom        | dateto          | startdate       | finishdate      | active | entityversion |
      | 9402 | ORD-SM-402 | 無產出訂單 | 03inProgress | 5.00000         | 5.00000                     | 0.00000      | 9001       | 9501          | 1                 | 02cumulated               | 01startOrder                 | @time("now-1d") | @time("now+2d") | @time("now-1d") | @time("now+2d") | true   | 0             |
    When (No Actor) 推進訂單狀態, call table:
      | orderId |
      | 9402    |
    Then 推進訂單狀態(200)回應, with table:
      | order.state  | message                                                                                                                                     |
      | 03inProgress | Problem with order: A started production order without produced products cannot be completed. If you want to cancel the order, set it to Abandoned. |
    And 應該存在一個訂單狀態變更, with table:
      | sourcestate  | targetstate | status    |
      | 03inProgress | 04completed | 04failure |

  Scenario: 有產出的進行中訂單可以完成
    # 與上一條互為對照:唯一差別是 donequantity。
    # 這兩條合起來把「已產出數量 > 0」這條規則的正反兩面都固定住。
    Given 準備一個訂單, with table:
      | id   | number     | name       | state        | plannedquantity | commissionedplannedquantity | donequantity | product_id | technology_id | productionline_id | typeofproductionrecording | inputproductsrequiredfortype | datefrom        | dateto          | startdate       | finishdate      | active | entityversion |
      | 9403 | ORD-SM-403 | 有產出訂單 | 03inProgress | 5.00000         | 5.00000                     | 5.00000      | 9001       | 9501          | 1                 | 02cumulated               | 01startOrder                 | @time("now-1d") | @time("now+2d") | @time("now-1d") | @time("now+2d") | true   | 0             |
    When (No Actor) 推進訂單狀態, call table:
      | orderId |
      | 9403    |
    Then 推進訂單狀態(200)回應, with table:
      | order.state |
      | 04completed |
    And 應該存在一個訂單狀態變更, with table:
      | sourcestate  | targetstate | status       |
      | 03inProgress | 04completed | 03successful |
