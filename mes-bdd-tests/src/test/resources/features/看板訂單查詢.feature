@api @mvp
Feature: 看板訂單查詢

  對「執行中的 qcadoo 實例」測試。

  受測端點:
    GET /rest/dashboardKanban/ordersPending
    GET /rest/dashboardKanban/ordersInProgress
    GET /rest/dashboardKanban/ordersCompleted

  受測程式碼路徑:
    DashboardKanbanController.getOrders*()
      → DashboardKanbanDataProvider.getOrders*()
        → UserService.getCurrentUserEntity() / ParameterService.getParameter()
        → NamedParameterJdbcTemplate 查詢 orders_orderlistdto view

  篩選規則取自 DashboardKanbanDataProvider.getOrdersQuery():

    WHERE orderlistdto.active = true
      AND orderlistdto.state IN (:states)
      AND date_trunc('day', orderlistdto.startdate) <= current_date
      AND current_date <= date_trunc('day', orderlistdto.finishdate)

  各端點的 :states 參數:
    ordersPending    → 02accepted, 06interrupted   ← 注意不含 01pending
    ordersInProgress → 03inProgress
    ordersCompleted  → 04completed

  Rule: 狀態決定訂單出現在哪一個看板欄位

    Scenario: 已接受的訂單出現在待處理欄位
      Given 準備一個訂單, with table:
        | id   | number      | name         | state      | plannedquantity | product_id | technology_id | startdate       | finishdate      | active | entityversion |
        | 9301 | ORD-BDD-301 | 已接受訂單   | 02accepted | 10.00000        | 9001       | 9501          | @time("now-1d") | @time("now+1d") | true   | 0             |
      When (No Actor) 查詢待處理訂單, call table:
        |  |
        |  |
      Then 查詢待處理訂單(200)回應, with table:
        | [0].number  | [0].state  |
        | ORD-BDD-301 | 02accepted |

    Scenario: 中斷的訂單也出現在待處理欄位
      Given 準備一個訂單, with table:
        | id   | number      | name         | state         | plannedquantity | product_id | technology_id | startdate       | finishdate      | active | entityversion |
        | 9302 | ORD-BDD-302 | 中斷訂單     | 06interrupted | 10.00000        | 9001       | 9501          | @time("now-1d") | @time("now+1d") | true   | 0             |
      When (No Actor) 查詢待處理訂單, call table:
        |  |
        |  |
      Then 查詢待處理訂單(200)回應, with table:
        | [0].number  | [0].state     |
        | ORD-BDD-302 | 06interrupted |

    Scenario: 待處理狀態的訂單不會出現在待處理欄位
      # 端點名稱有誤導性 —— ordersPending 篩的是 02accepted / 06interrupted,
      # 01pending 反而不在其中。這條 scenario 把這個反直覺的行為固定下來。
      Given 準備一個訂單, with table:
        | id   | number      | name         | state     | plannedquantity | product_id | technology_id | startdate       | finishdate      | active | entityversion |
        | 9303 | ORD-BDD-303 | 待處理訂單   | 01pending | 10.00000        | 9001       | 9501          | @time("now-1d") | @time("now+1d") | true   | 0             |
      When (No Actor) 查詢待處理訂單, call table:
        |  |
        |  |
      Then 查詢待處理訂單(200)回應, with table:
        | [0].number |
        | &isNull    |

    Scenario: 進行中的訂單出現在進行中欄位
      Given 準備一個訂單, with table:
        | id   | number      | name         | state        | plannedquantity | product_id | technology_id | startdate       | finishdate      | active | entityversion |
        | 9304 | ORD-BDD-304 | 進行中訂單   | 03inProgress | 10.00000        | 9001       | 9501          | @time("now-1d") | @time("now+1d") | true   | 0             |
      When (No Actor) 查詢進行中訂單, call table:
        |  |
        |  |
      Then 查詢進行中訂單(200)回應, with table:
        | [0].number  | [0].state    |
        | ORD-BDD-304 | 03inProgress |

    Scenario: 進行中的訂單不會同時出現在待處理欄位
      # 各欄位的 states 集合互斥,同一筆訂單只會出現在一個欄位。
      Given 準備一個訂單, with table:
        | id   | number      | name         | state        | plannedquantity | product_id | technology_id | startdate       | finishdate      | active | entityversion |
        | 9305 | ORD-BDD-305 | 進行中訂單   | 03inProgress | 10.00000        | 9001       | 9501          | @time("now-1d") | @time("now+1d") | true   | 0             |
      When (No Actor) 查詢待處理訂單, call table:
        |  |
        |  |
      Then 查詢待處理訂單(200)回應, with table:
        | [0].number |
        | &isNull    |

    Scenario: 已完成的訂單出現在已完成欄位
      Given 準備一個訂單, with table:
        | id   | number      | name         | state       | plannedquantity | product_id | technology_id | startdate       | finishdate      | active | entityversion |
        | 9306 | ORD-BDD-306 | 已完成訂單   | 04completed | 10.00000        | 9001       | 9501          | @time("now-1d") | @time("now+1d") | true   | 0             |
      When (No Actor) 查詢已完成訂單, call table:
        |  |
        |  |
      Then 查詢已完成訂單(200)回應, with table:
        | [0].number  | [0].state   |
        | ORD-BDD-306 | 04completed |

  Rule: 停用的訂單不會出現在任何看板欄位

    Scenario: 已停用的訂單被排除
      Given 準備一個訂單, with table:
        | id   | number      | name         | state      | plannedquantity | product_id | technology_id | startdate       | finishdate      | active | entityversion |
        | 9311 | ORD-BDD-311 | 停用訂單     | 02accepted | 10.00000        | 9001       | 9501          | @time("now-1d") | @time("now+1d") | false  | 0             |
      When (No Actor) 查詢待處理訂單, call table:
        |  |
        |  |
      Then 查詢待處理訂單(200)回應, with table:
        | [0].number |
        | &isNull    |

  Rule: 只有執行期間涵蓋今天的訂單才會出現

    # 條件是 startdate <= 今天 <= finishdate,且比較前先 date_trunc 到「天」。

    Scenario: 執行期間已結束的訂單被排除
      Given 準備一個訂單, with table:
        | id   | number      | name         | state      | plannedquantity | product_id | technology_id | startdate        | finishdate      | active | entityversion |
        | 9321 | ORD-BDD-321 | 已過期訂單   | 02accepted | 10.00000        | 9001       | 9501          | @time("now-10d") | @time("now-1d") | true   | 0             |
      When (No Actor) 查詢待處理訂單, call table:
        |  |
        |  |
      Then 查詢待處理訂單(200)回應, with table:
        | [0].number |
        | &isNull    |

    Scenario: 尚未開始的訂單被排除
      Given 準備一個訂單, with table:
        | id   | number      | name         | state      | plannedquantity | product_id | technology_id | startdate       | finishdate       | active | entityversion |
        | 9322 | ORD-BDD-322 | 未開始訂單   | 02accepted | 10.00000        | 9001       | 9501          | @time("now+1d") | @time("now+10d") | true   | 0             |
      When (No Actor) 查詢待處理訂單, call table:
        |  |
        |  |
      Then 查詢待處理訂單(200)回應, with table:
        | [0].number |
        | &isNull    |

    Scenario: 起訖日皆為今天的訂單會出現(邊界)
      # date_trunc('day', ...) 讓比較落在「天」的粒度,
      # 因此 startdate 與 finishdate 都是今天時兩個條件都成立。
      Given 準備一個訂單, with table:
        | id   | number      | name         | state      | plannedquantity | product_id | technology_id | startdate    | finishdate   | active | entityversion |
        | 9323 | ORD-BDD-323 | 當日訂單     | 02accepted | 10.00000        | 9001       | 9501          | @time("now") | @time("now") | true   | 0             |
      When (No Actor) 查詢待處理訂單, call table:
        |  |
        |  |
      Then 查詢待處理訂單(200)回應, with table:
        | [0].number  | [0].state  |
        | ORD-BDD-323 | 02accepted |
