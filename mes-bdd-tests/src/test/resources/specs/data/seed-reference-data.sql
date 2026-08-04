-- 測試用的參考資料。
--
-- 刻意「不」放進 schema.sql —— 那份檔案的資料表會被 SpecFormula 在每個
-- scenario 結束後 TRUNCATE ... CASCADE。參考資料需要跨 scenario 存活,
-- 因此在測試流程外部以本腳本建立一次即可。
--
-- 用法:
--   docker exec -i qcadoo-pg psql -U postgres -d mes < seed-reference-data.sql
--
-- ⚠️ technology 的結構必須完整,不能只建一列
--
-- qcadoo 的 model hook 會在儲存訂單時,**無條件**用 technology 的值覆寫
-- 訂單的生產記錄設定:
--
--   OrderHooksPC.onCreate() → setOrderWithTechnologyProductionCountingValues()
--   (mes-plugins-production-counting/.../hooks/OrderHooksPC.java:73-88)
--
-- 若 technology 的 typeofproductionrecording 是 NULL,訂單那個欄位就會被
-- 覆寫成 NULL,接著 validatesWith 報 orders.order.typeOfProductionRecording.error.empty
-- —— 即使 REST payload 有帶正確的值也一樣(該值會被靜默忽略)。
--
-- 此外 POST /rest/order 會連帶建立 production counting quantity,需要:
--   * 至少一個 operation component,否則 BasicProductionCountingServiceImpl.java:163
--     會在 product.getBelongsToField(PARENT).getId() 上 NPE(回 HTTP 500)
--   * 產出元件(role=02produced / typeOfMaterial=03finalProduct)需要
--     productsInputLocation,否則 ProductionCountingQuantityValidatorsPFTD 擋下
--
-- 透過 UI 或 OrderCreationService.createTechnology() 建立的 technology 天生具備
-- 這些結構;直接用 SQL 塞的 fixture 會繞過所有 hook,必須自己補齊。

-- 產品
INSERT INTO basic_product (id, number, name, unit, entitytype, active, entityversion)
VALUES (9001, 'PRD-BDD-001', 'BDD Blue Mug', 'szt', '01particularProduct', true, 0)
ON CONFLICT (id) DO NOTHING;

-- 倉庫(產出元件的 productsInputLocation 必填)
INSERT INTO materialflow_location (id, number, name, algorithm, active, entityversion)
VALUES (9901, 'WH-BDD-001', 'BDD Warehouse', '01fifo', true, 0)
ON CONFLICT (id) DO NOTHING;

-- 技術規範
INSERT INTO technologies_technology
    (id, number, name, product_id, state, master,
     typeofproductionrecording, registerproductiontime,
     registerquantityinproduct, registerquantityoutproduct,
     productsinputlocation_id)
VALUES (9501, 'TECH-BDD-001', 'BDD Technology', 9001, '02accepted', true,
        '02cumulated', true, true, true, 9901)
ON CONFLICT (id) DO NOTHING;

-- 作業
INSERT INTO technologies_operation (id, number, name)
VALUES (9601, 'OP-BDD-001', 'BDD Operation')
ON CONFLICT (id) DO NOTHING;

-- 作業元件(掛在 technology 上;缺這個會 NPE)
INSERT INTO technologies_technologyoperationcomponent
    (id, technology_id, operation_id, entitytype, priority)
VALUES (9701, 9501, 9601, 'operation', 1)
ON CONFLICT (id) DO NOTHING;

-- 產出元件(最終產品)
INSERT INTO technologies_operationproductoutcomponent
    (id, operationcomponent_id, product_id, quantity, productsinputlocation_id)
VALUES (9801, 9701, 9001, 1.00000, 9901)
ON CONFLICT (id) DO NOTHING;
