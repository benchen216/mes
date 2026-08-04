@datalayer
Feature: 資料層接線驗證

  這支 feature 刻意「不呼叫 API」,只驗證 SpecFormula 的資料層與 qcadoo schema
  是否正確接上。用途是把「資料層問題」與「API 層問題」隔離開來 ——
  在 qcadoo 實例尚未部署的階段,這是唯一能跑出綠燈的部分。

  驗證項目:
    1. 從 mes_db_en.sql 抽取的 DDL 能在 PostgreSQL testcontainer 正確建立
    2. entity_to_table_mapping.yml 的中文實體名能對應到 qcadoo 資料表
    3. entity_setup 能寫入資料
    4. entity_validate 能讀回並比對
    5. entity_non_existence_validate 能正確判定不存在

  Scenario: 可以建立並讀回一筆產品
    Given 準備一個產品, with table:
      | id   | number  | name   | unit |
      | 8001 | PRD-100 | 藍色馬克杯 | szt  |
    Then 應該存在一個產品, with table:
      | number  | name   | unit |
      | PRD-100 | 藍色馬克杯 | szt  |

  Scenario: 可以建立一筆帶外鍵關聯的訂單
    Given 準備一個產品, with table:
      | id   | number  | name  | unit |
      | 8002 | PRD-200 | 紅色馬克杯 | szt  |
    And 準備一個訂單, with table:
      | id   | number  | name  | state     | plannedquantity | product_id |
      | 8102 | ORD-200 | 訂單 200 | 01pending | 25.00000        | 8002       |
    Then 應該存在一個訂單, with table:
      | number  | state     | plannedquantity | product_id |
      | ORD-200 | 01pending | 25.00000        | 8002       |

  Scenario: 未建立的訂單不應存在
    Then 應該不存在一個訂單, with table:
      | number        |
      | ORD-NOT-EXIST |

  Scenario: 訂單狀態變更記錄可以獨立寫入
    Given 準備一個訂單, with table:
      | id   | number  | name     | state        | plannedquantity |
      | 8103 | ORD-300 | 訂單 300 | 03inProgress | 5.00000         |
    And 準備一個訂單狀態變更, with table:
      | id   | order_id | sourcestate | targetstate  | status       |
      | 8203 | 8103     | 01pending   | 03inProgress | 03successful |
    Then 應該存在一個訂單狀態變更, with table:
      | order_id | sourcestate | targetstate  |
      | 8103     | 01pending   | 03inProgress |
