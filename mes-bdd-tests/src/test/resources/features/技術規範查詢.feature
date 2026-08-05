@api @mvp
Feature: 技術規範查詢

  這支 feature 的存在意義:證明**切換到生成的 OpenAPI spec 之後,
  可測範圍真的擴大了**。

  底下用到的兩個端點,在先前手寫的 spec 裡都不存在 ——
  手寫版只涵蓋 6 個端點,生成版涵蓋 130 個。要測這些端點,
  在手寫時代必須先自己補 spec;現在直接可用。

  受測端點:
    GET /rest/technologies   (TechnologyApiController.getTechnologies)
    GET /rest/units          (BasicApiController.getUnits)

  兩者分屬不同 plugin(technologies / basic),都不需要 entity_setup ——
  查的是 seed-reference-data.sql 與 qcadoo 內建的資料。

  Scenario: 可以依關鍵字與產品查到技術規範
    # 查的是 seed-reference-data.sql 建立的 TECH-BDD-001(product 9001)
    When (No Actor) 查詢技術規範, call table:
      | query | productId |
      | TECH  | 9001      |
    Then 查詢技術規範(200)回應, with table:
      | technologies[0].number | technologies[0].master |
      | TECH-BDD-001           | true                   |

  Scenario: 不存在的關鍵字查不到技術規範
    When (No Actor) 查詢技術規範, call table:
      | query        | productId |
      | NOT-EXIST-XX | 9001      |
    Then 查詢技術規範(200)回應, with table:
      | technologies[0].number |
      | &isNull                |

  Scenario: 可以取得系統內建的單位清單
    # qcadoo 內建的計量單位,由 mes_db_en.sql 還原時一併帶入。
    # 這條同時是環境健檢:回空清單通常代表資料庫沒還原完整。
    When (No Actor) 查詢單位, call table:
      |  |
      |  |
    Then 查詢單位(200)回應, with table:
      | [0].key | [0].value |
      | cm      | cm        |
