@api @mvp
Feature: 生產線查詢

  跨 plugin 的覆蓋 —— 前面的看板測試都打在 mes-plugins-orders,
  這支打的是 mes-plugins-production-lines,驗證 qcadoo 的 plugin 架構下
  不同模組的 controller 都能透過同一套測試機制觸達。

  受測端點: GET /rest/productionLines/default
  受測程式碼: ProductionLinesApiController.getDefaultProductionLine()

  這支 feature 刻意「不」使用 entity_setup —— 驗證的是 qcadoo 隨
  mes_db_en.sql 一起還原的 seed 資料。因此它同時也是一個環境健檢:
  若這條 scenario 紅了,通常代表資料庫沒有正確還原,而不是程式有問題。

  Scenario: 可以取得系統預設的生產線
    When (No Actor) 查詢預設生產線, call table:
      |  |
      |  |
    Then 查詢預設生產線(200)回應, with table:
      | number | name      |
      | Line   | Main line |
