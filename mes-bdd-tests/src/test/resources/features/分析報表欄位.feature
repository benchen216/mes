@api @mvp
Feature: 分析報表欄位設定

  驗證**新補的 OpenAPI summary 真的可用** —— 底下三個端點在
  summaries.yml 補完之前是英文佔位符,feature step 引用不到。

  這三支分屬不同 plugin,共通點是它們都遵循 qcadoo 分析報表的
  「四件組」慣例(columns / records / validate / exportToCsv),
  但彼此**沒有共同基底類別** —— 各自獨立宣告了同名方法。
  這也是為什麼這 79 筆 summary 無法用繼承槓桿,只能逐筆補。

  受測端點:
    GET /rest/prodAttributes/columns        (ProductsAttributesController)
    GET /rest/resAttributes/columns         (ResourcesAttributesController)
    GET /rest/operDurationAnalysis/columns  (OperationDurationAnalysisController)

  這些端點回傳報表的欄位定義,是前端動態產生表格的依據。
  不需要 entity_setup —— 欄位定義來自程式碼與 seed 資料。

  Scenario: 產品屬性報表的第一個欄位是產品編號
    When (No Actor) 查詢產品屬性欄位設定, call table:
      |  |
      |  |
    Then 查詢產品屬性欄位設定(200)回應, with table:
      | [0].id        | [0].name |
      | productNumber | Number   |

  Scenario: 資源屬性報表的第一個欄位是編號
    When (No Actor) 查詢資源屬性欄位設定, call table:
      |  |
      |  |
    Then 查詢資源屬性欄位設定(200)回應, with table:
      | [0].id | [0].name |
      | number | Number   |

  Scenario: 作業工時分析報表的第一個欄位是作業編號
    When (No Actor) 查詢作業工時分析欄位設定, call table:
      |  |
      |  |
    Then 查詢作業工時分析欄位設定(200)回應, with table:
      | [0].id          | [0].name  |
      | operationNumber | Operation |
