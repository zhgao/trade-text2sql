"""SQLAlchemy 2.0 table definitions for the TradeQuery demo ledger.

Dialect-neutral: the same MetaData works on SQLite (demo default),
PostgreSQL and MySQL.

Design notes:
- Date-ish fields (issue_date, expiry_date, etd, ...) are stored as
  TEXT/VARCHAR 'YYYY-MM-DD' strings — exactly like the original SQLite
  schema.sql. This keeps the seed data and the ISO-date string comparisons
  identical on all three dialects (see the dialect date-expression table in
  backend/agent/graph.py for the matching query side).
- query_audit is the compliance audit log: one row per natural-language query.
"""
from sqlalchemy import Column, Float, Index, Integer, MetaData, String, Table

metadata = MetaData()

corporates = Table(
    "corporates",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),  # 客户主键
    Column("name_cn", String, nullable=False),  # 企业中文名
    Column("name_en", String, nullable=False),  # 企业英文名
    Column("country", String, nullable=False),  # 注册国家/地区
    Column("credit_rating", String, nullable=False),  # 银行内部信用评级
    Column("relationship_manager", String, nullable=False),  # 客户经理姓名
)

letters_of_credit = Table(
    "letters_of_credit",
    metadata,
    Column("lc_number", String, primary_key=True),  # 信用证编号，如 SCB2026LC001234
    Column("applicant", String, nullable=False),  # 开证申请人（进口商）
    Column("beneficiary", String, nullable=False),  # 受益人（出口商）
    Column("issuing_bank", String, nullable=False),  # 开证行
    Column("advising_bank", String),  # 通知行
    Column("amount", Float, nullable=False),  # 信用证金额
    Column("currency", String, nullable=False),  # 币种
    Column("issue_date", String, nullable=False),  # 开证日期 YYYY-MM-DD
    Column("expiry_date", String, nullable=False),  # 到期日 YYYY-MM-DD
    Column("shipment_deadline", String),  # 最迟装运期 YYYY-MM-DD
    Column("status", String, nullable=False),  # 信用证状态
    Column("incoterm", String),  # 贸易术语
    Column("tenor", String, nullable=False),  # 付款期限
)

shipments = Table(
    "shipments",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),  # 发运主键
    Column("lc_number", String, nullable=False),  # 关联信用证
    Column("bl_number", String, nullable=False),  # 提单号
    Column("vessel_voyage", String, nullable=False),  # 船名航次
    Column("port_loading", String, nullable=False),  # 装货港
    Column("port_discharge", String, nullable=False),  # 卸货港
    Column("etd", String),  # 预计离港 YYYY-MM-DD
    Column("eta", String),  # 预计到港 YYYY-MM-DD
    Column("ata", String),  # 实际到港 YYYY-MM-DD（未到则为空）
    Column("status", String, nullable=False),  # 货物状态
    Column("container_numbers", String),  # 集装箱号（逗号分隔）
    Column("goods_description", String, nullable=False),  # 货物描述
    Column("quantity", String),  # 数量/件数描述
)

documents = Table(
    "documents",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),  # 单据主键
    Column("lc_number", String, nullable=False),  # 关联信用证
    Column("doc_type", String, nullable=False),  # 单据类型
    Column("presented_date", String),  # 交单日期 YYYY-MM-DD
    Column("status", String, nullable=False),  # 审单状态
    Column("discrepancies", String),  # 不符点描述
)

payments = Table(
    "payments",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),  # 付款主键
    Column("lc_number", String, nullable=False),  # 关联信用证
    Column("amount", Float, nullable=False),  # 付款金额
    Column("currency", String, nullable=False),  # 币种
    Column("due_date", String, nullable=False),  # 应付日期 YYYY-MM-DD
    Column("paid_date", String),  # 实际付款日期（未付则为空）
    Column("status", String, nullable=False),  # 付款状态
    Column("payer", String, nullable=False),  # 付款方
    Column("payee", String, nullable=False),  # 收款方
)

query_audit = Table(
    "query_audit",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("ts", String, nullable=False),  # 查询时间（UTC ISO8601）
    Column("question", String, nullable=False),  # 用户原始问题
    Column("sql_text", String),  # 最终执行的 SQL
    Column("row_count", Integer),  # 返回行数
    Column("elapsed_ms", Float),  # 端到端耗时（毫秒）
    Column("has_error", Integer, nullable=False, default=0),  # 是否出错（0/1）
    Column("error_msg", String),  # 错误信息（如有）
)

Index("idx_shipments_lc", shipments.c.lc_number)
Index("idx_documents_lc", documents.c.lc_number)
Index("idx_payments_lc", payments.c.lc_number)
Index("idx_lc_status", letters_of_credit.c.status)
Index("idx_lc_expiry", letters_of_credit.c.expiry_date)

#: table name -> Table, used by init_db() seeding and write_audit().
#: (query_audit is included for the audit writer; it is not seeded.)
TABLES = {
    "corporates": corporates,
    "letters_of_credit": letters_of_credit,
    "shipments": shipments,
    "documents": documents,
    "payments": payments,
    "query_audit": query_audit,
}
