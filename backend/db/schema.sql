-- TradeQuery 数据库 Schema
-- 场景：外资银行（渣打等）贸易金融 — 信用证 / 发运 / 单据 / 付款 全链路查询
-- 方言：SQLite（演示用）；生产环境映射到 Oracle，见 README

-- ============================================================
-- 1. corporates 企业客户
-- ============================================================
CREATE TABLE IF NOT EXISTS corporates (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,  -- 客户主键
    name_cn             TEXT NOT NULL,                     -- 企业中文名
    name_en             TEXT NOT NULL,                     -- 企业英文名
    country             TEXT NOT NULL,                     -- 注册国家/地区
    credit_rating       TEXT NOT NULL,                     -- 银行内部信用评级（AAA/AA/A/BBB...）
    relationship_manager TEXT NOT NULL                      -- 客户经理姓名
);

-- ============================================================
-- 2. letters_of_credit 信用证
-- status 取值：已开立 / 已通知 / 已交单 / 已承兑 / 已付款 / 已过期 / 已注销
-- ============================================================
CREATE TABLE IF NOT EXISTS letters_of_credit (
    lc_number        TEXT PRIMARY KEY,   -- 信用证编号，如 SCB2026LC001234
    applicant        TEXT NOT NULL,       -- 开证申请人（进口商），企业中文名
    beneficiary      TEXT NOT NULL,       -- 受益人（出口商），企业中文名
    issuing_bank     TEXT NOT NULL,       -- 开证行
    advising_bank    TEXT,                -- 通知行
    amount           REAL NOT NULL,       -- 信用证金额
    currency         TEXT NOT NULL,       -- 币种（USD/CNY/EUR）
    issue_date       TEXT NOT NULL,       -- 开证日期 YYYY-MM-DD
    expiry_date      TEXT NOT NULL,       -- 有效期/到期日 YYYY-MM-DD
    shipment_deadline TEXT,               -- 最迟装运期 YYYY-MM-DD
    status           TEXT NOT NULL,       -- 信用证状态
    incoterm         TEXT,                -- 贸易术语（FOB/CIF/CFR...）
    tenor            TEXT NOT NULL        -- 付款期限（即期/30天/60天/90天）
);

-- ============================================================
-- 3. shipments 货物发运
-- status 取值：已订舱 / 已装船 / 在途 / 已到港 / 已清关 / 已交付
-- ============================================================
CREATE TABLE IF NOT EXISTS shipments (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,  -- 发运主键
    lc_number        TEXT NOT NULL REFERENCES letters_of_credit(lc_number),  -- 关联信用证
    bl_number        TEXT NOT NULL,       -- 提单号
    vessel_voyage    TEXT NOT NULL,       -- 船名航次，如 COSCO UNIVERSE V.045E
    port_loading     TEXT NOT NULL,       -- 装货港
    port_discharge   TEXT NOT NULL,       -- 卸货港
    etd              TEXT,                -- 预计离港 YYYY-MM-DD
    eta              TEXT,                -- 预计到港 YYYY-MM-DD
    ata              TEXT,                -- 实际到港 YYYY-MM-DD（未到则为空）
    status           TEXT NOT NULL,       -- 货物状态
    container_numbers TEXT,               -- 集装箱号（逗号分隔）
    goods_description TEXT NOT NULL,      -- 货物描述
    quantity         TEXT                 -- 数量/件数描述
);

-- ============================================================
-- 4. documents 单据
-- doc_type 取值：商业发票 / 提单 / 产地证 / 保险单 / 汇票
-- status 取值：已交单 / 审单中 / 相符 / 不符点
-- ============================================================
CREATE TABLE IF NOT EXISTS documents (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,  -- 单据主键
    lc_number        TEXT NOT NULL REFERENCES letters_of_credit(lc_number),  -- 关联信用证
    doc_type         TEXT NOT NULL,       -- 单据类型
    presented_date   TEXT,                -- 交单日期 YYYY-MM-DD
    status           TEXT NOT NULL,       -- 审单状态
    discrepancies    TEXT                 -- 不符点描述（无不符点则为空）
);

-- ============================================================
-- 5. payments 付款
-- status 取值：待付款 / 已付款 / 逾期
-- ============================================================
CREATE TABLE IF NOT EXISTS payments (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,  -- 付款主键
    lc_number        TEXT NOT NULL REFERENCES letters_of_credit(lc_number),  -- 关联信用证
    amount           REAL NOT NULL,       -- 付款金额
    currency         TEXT NOT NULL,       -- 币种
    due_date         TEXT NOT NULL,       -- 应付日期 YYYY-MM-DD
    paid_date        TEXT,                -- 实际付款日期（未付则为空）
    status           TEXT NOT NULL,       -- 付款状态
    payer            TEXT NOT NULL,       -- 付款方
    payee            TEXT NOT NULL        -- 收款方
);

-- ============================================================
-- 审计日志表（银行合规要求：每次自然语言查询留痕）
-- ============================================================
CREATE TABLE IF NOT EXISTS query_audit (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,  -- 日志主键
    ts               TEXT NOT NULL,       -- 查询时间（UTC ISO8601）
    question         TEXT NOT NULL,       -- 用户原始问题
    sql_text         TEXT,                -- 最终执行的 SQL（脱敏前）
    row_count        INTEGER,             -- 返回行数
    elapsed_ms       REAL,                -- 端到端耗时（毫秒）
    has_error        INTEGER NOT NULL DEFAULT 0,  -- 是否出错（0/1）
    error_msg        TEXT                 -- 错误信息（如有）
);

CREATE INDEX IF NOT EXISTS idx_shipments_lc ON shipments(lc_number);
CREATE INDEX IF NOT EXISTS idx_documents_lc ON documents(lc_number);
CREATE INDEX IF NOT EXISTS idx_payments_lc ON payments(lc_number);
CREATE INDEX IF NOT EXISTS idx_lc_status ON letters_of_credit(status);
CREATE INDEX IF NOT EXISTS idx_lc_expiry ON letters_of_credit(expiry_date);
