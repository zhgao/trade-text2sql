"""Schema catalog: English table/column annotations + few-shot examples,
used by schema_linking and sql_gen. String literal values stay in their
original language (the demo ledger is an Asian trade book)."""

# table name -> {comment: English table description, columns: {column: English note}}
SCHEMA_CATALOG: dict = {
    "corporates": {
        "comment": "Corporate clients (the bank's corporate customers, with internal credit rating and relationship manager)",
        "columns": {
            "id": "Client primary key",
            "name_cn": "Company name in Chinese",
            "name_en": "Company name in English",
            "country": "Country/region of registration",
            "credit_rating": "Internal bank credit rating (AAA/AA/A/BBB)",
            "relationship_manager": "Relationship manager name",
        },
    },
    "letters_of_credit": {
        "comment": "Letters of credit (the core trade instrument)",
        "columns": {
            "lc_number": "L/C number, primary key, e.g. SCB2026LC001234",
            "applicant": "Applicant (importer), company name in Chinese",
            "beneficiary": "Beneficiary (exporter), company name in Chinese",
            "issuing_bank": "Issuing bank, e.g. Standard Chartered Bank",
            "advising_bank": "Advising bank",
            "amount": "L/C amount (numeric)",
            "currency": "Currency (USD/CNY/EUR)",
            "issue_date": "Issue date, YYYY-MM-DD",
            "expiry_date": "Expiry date, YYYY-MM-DD",
            "shipment_deadline": "Latest shipment date, YYYY-MM-DD",
            "status": "L/C status, one of: \u5df2\u5f00\u7acb (Issued) / \u5df2\u901a\u77e5 (Advised) / \u5df2\u4ea4\u5355 (Documents presented) / \u5df2\u627f\u5151 (Accepted) / \u5df2\u4ed8\u6b3e (Paid) / \u5df2\u8fc7\u671f (Expired) / \u5df2\u6ce8\u9500 (Cancelled)",
            "incoterm": "Incoterms (FOB/CIF/CFR)",
            "tenor": "Tenor (At sight / 30 days / 60 days / 90 days)",
        },
    },
    "shipments": {
        "comment": "Shipments (the logistics trail of each cargo lot)",
        "columns": {
            "id": "Shipment primary key",
            "lc_number": "Linked L/C number",
            "bl_number": "Bill of lading number",
            "vessel_voyage": "Vessel name and voyage",
            "port_loading": "Port of loading",
            "port_discharge": "Port of discharge",
            "etd": "Estimated date of departure",
            "eta": "Estimated date of arrival",
            "ata": "Actual date of arrival (empty if not yet arrived)",
            "status": "Cargo status, one of: \u5df2\u8ba2\u8231 (Booked) / \u5df2\u88c5\u8239 (Loaded) / \u5728\u9014 (In transit) / \u5df2\u5230\u6e2f (Arrived) / \u5df2\u6e05\u5173 (Customs cleared) / \u5df2\u4ea4\u4ed8 (Delivered)",
            "container_numbers": "Container numbers (comma separated)",
            "goods_description": "Goods description",
            "quantity": "Quantity / package description",
        },
    },
    "documents": {
        "comment": "Documents (presentation and examination under L/C)",
        "columns": {
            "id": "Document primary key",
            "lc_number": "Linked L/C number",
            "doc_type": "Document type, one of: \u5546\u4e1a\u53d1\u7968 (Commercial invoice) / \u63d0\u5355 (Bill of lading) / \u4ea7\u5730\u8bc1 (Certificate of origin) / \u4fdd\u9669\u5355 (Insurance policy) / \u6c47\u7968 (Draft)",
            "presented_date": "Presentation date",
            "status": "Examination status, one of: \u5df2\u4ea4\u5355 (Presented) / \u5ba1\u5355\u4e2d (Under examination) / \u76f8\u7b26 (Compliant) / \u4e0d\u7b26\u70b9 (Discrepant)",
            "discrepancies": "Discrepancy description (empty when compliant)",
        },
    },
    "payments": {
        "comment": "Payments (amounts payable / paid under L/C)",
        "columns": {
            "id": "Payment primary key",
            "lc_number": "Linked L/C number",
            "amount": "Payment amount (numeric)",
            "currency": "Currency",
            "due_date": "Due date, YYYY-MM-DD",
            "paid_date": "Actual payment date (empty if unpaid)",
            "status": "Payment status, one of: \u5f85\u4ed8\u6b3e (Pending) / \u5df2\u4ed8\u6b3e (Paid) / \u903e\u671f (Overdue)",
            "payer": "Payer (company name in Chinese)",
            "payee": "Payee (company name in Chinese)",
        },
    },
}

# Few-shot examples: (English question, SQLite SQL), covering the main query patterns.
# String literals keep the ledger's original values.
FEW_SHOTS: list = [
    (
        "What is the document status of L/C SCB2026LC001207?",
        "SELECT doc_type, presented_date, status, discrepancies FROM documents "
        "WHERE lc_number = 'SCB2026LC001207' LIMIT 50;",
    ),
    (
        "Where is the cargo under B/L COSU6123456780 right now?",
        "SELECT bl_number, vessel_voyage, port_loading, port_discharge, status, eta, ata "
        "FROM shipments WHERE bl_number = 'COSU6123456780' LIMIT 50;",
    ),
    (
        "Which payments are overdue?",
        "SELECT lc_number, amount, currency, due_date, payer, payee FROM payments "
        "WHERE status = '\u903e\u671f' LIMIT 50;",
    ),
    (
        "Which letters of credit expire in the next 30 days?",
        "SELECT lc_number, applicant, beneficiary, amount, currency, expiry_date, status "
        "FROM letters_of_credit "
        "WHERE expiry_date BETWEEN date('now') AND date('now', '+30 days') LIMIT 50;",
    ),
    (
        "What is the total L/C amount for Shanghai Huamao (\u4e0a\u6d77\u534e\u8d38\u8fdb\u51fa\u53e3\u6709\u9650\u516c\u53f8)?",
        "SELECT currency, COUNT(*) AS lc_count, SUM(amount) AS total_amount "
        "FROM letters_of_credit "
        "WHERE applicant = '\u4e0a\u6d77\u534e\u8d38\u8fdb\u51fa\u53e3\u6709\u9650\u516c\u53f8' OR beneficiary = '\u4e0a\u6d77\u534e\u8d38\u8fdb\u51fa\u53e3\u6709\u9650\u516c\u53f8' "
        "GROUP BY currency LIMIT 50;",
    ),
    (
        "Which letters of credit does Shenzhen Hengxin (\u6df1\u5733\u6052\u4fe1\u7535\u5b50\u79d1\u6280\u6709\u9650\u516c\u53f8) hold?",
        "SELECT lc_number, applicant, beneficiary, amount, currency, status, expiry_date "
        "FROM letters_of_credit "
        "WHERE applicant = '\u6df1\u5733\u6052\u4fe1\u7535\u5b50\u79d1\u6280\u6709\u9650\u516c\u53f8' OR beneficiary = '\u6df1\u5733\u6052\u4fe1\u7535\u5b50\u79d1\u6280\u6709\u9650\u516c\u53f8' "
        "LIMIT 50;",
    ),
    (
        "What is the shipment status of L/C SCB2026LC001213?",
        "SELECT bl_number, vessel_voyage, port_loading, port_discharge, status, eta "
        "FROM shipments WHERE lc_number = 'SCB2026LC001213' LIMIT 50;",
    ),
]


def format_schema(tables: "list[str] | None" = None) -> str:
    """Render the schema catalog as prompt-ready text."""
    names = tables or list(SCHEMA_CATALOG.keys())
    lines = []
    for t in names:
        if t not in SCHEMA_CATALOG:
            continue
        info = SCHEMA_CATALOG[t]
        lines.append(f"Table {t} ({info['comment']}):")
        for col, comment in info["columns"].items():
            lines.append(f"  - {col}: {comment}")
    return "\n".join(lines)


def format_few_shots() -> str:
    parts = []
    for i, (q, sql) in enumerate(FEW_SHOTS, 1):
        parts.append(f"Example {i}\nQuestion: {q}\nSQL: {sql}")
    return "\n\n".join(parts)
