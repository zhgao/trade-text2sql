# TradeQuery 贸易智能查询

> 备注：本项目 UI 与问答为**英文版**，面向外资银行演示；种子数据保留中文企业名（亚洲贸易账本的真实场景）。

面向外资银行贸易金融业务（信用证 / 发运 / 单据 / 付款）的 **Text2SQL 演示系统**：
客户经理用一句话英文提问，系统自动生成 SQL、查询业务数据库，并用英文总结答案。

> 演示数据全部为虚构（如"上海华贸进出口有限公司"），仅用于功能展示。

## 架构

```
                    +------------------+
                    |  frontend/       |
                    |  聊天页面 (单文件)|
                    +--------+---------+
                             |  HTTP
                    +--------v---------+
                    | backend/main.py  |  FastAPI
                    |  /api/chat       +------> query_audit (审计日志表)
                    |  /api/health     |
                    |  /api/schema     |
                    +--------+---------+
                             |
                    +--------v---------+
                    | LangGraph Agent  |  backend/agent/graph.py
                    +--------+---------+
                             |
   intent(意图) -> schema_linking(选表) -> sql_gen(生成SQL)
        |                闲聊直接英文回复
        v
   guardrail(护栏:白名单/单条SELECT/强制LIMIT)
        v
   execute(只读执行,10秒超时) -> repair(报错/空结果可疑时回填LLM,最多2次)
        v
   answer(英文总结 + 原始数据行)
                             |
                    +--------v---------+
                    | SQLite trade.db  |  backend/db/trade.db
                    | 5 张业务表       |
                    +------------------+
```

LLM 通过 OpenAI 兼容接口调用（默认 Gemini 的 OpenAI endpoint），prompt 全部为英文，
表名列名保持英文。

## 快速开始

```bash
cd trade-text2sql

# 1. 创建虚拟环境并安装依赖（系统 Python 受 PEP 668 保护，不要装到系统里）
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 2. 配置 LLM（复制模板后填入真实 key；.env 不要提交）
cp .env.example .env
# 编辑 .env，填入 LLM_API_KEY

# 3. 启动（首次启动自动建库 + 灌种子数据）
.venv/bin/python -m backend.main
# 或: .venv/bin/uvicorn backend.main:app --host 0.0.0.0 --port 8000
```

浏览器打开 http://localhost:8000 即可使用。无 LLM key 时服务照常启动，
`/api/chat` 返回"未配置 LLM key"的友好提示（数据库与链路本身不受影响）。

运行测试：

```bash
.venv/bin/python -m pytest backend/tests -q
```

## 环境变量

| 变量 | 默认值 | 说明 |
|---|---|---|
| `LLM_API_KEY` | （空） | LLM API Key，必填；为空时问答降级 |
| `LLM_BASE_URL` | `https://generativelanguage.googleapis.com/v1beta/openai` | OpenAI 兼容接口地址 |
| `LLM_MODEL` | `gemini-2.5-flash` | 模型名 |
| `PORT` | `8000` | 服务端口 |

## 示例问题（UI 为英文版）

- What is the document status of L/C SCB2026LC001207?
- Where is the cargo under B/L COSU6123456780 right now?
- Which payments are overdue?
- Which letters of credit expire in the next 30 days?
- Which letters of credit does Shenzhen Hengxin (深圳恒信电子科技有限公司) hold?
- What is the total L/C amount for Shanghai Huamao (上海华贸进出口有限公司)?
- What is the shipment status of L/C SCB2026LC001213?

## 安全护栏（银行场景要点）

1. **只读执行**：SQLite 以 `mode=ro` 只读模式打开，写入在驱动层即失败。
2. **语句白名单**：必须为单条 `SELECT` / `WITH`；`sqlparse` 解析 + 关键字黑名单
   （INSERT/UPDATE/DELETE/DROP/CREATE/ALTER……）双重拦截。
3. **表白名单**：仅允许 5 张业务表（corporates / letters_of_credit / shipments /
   documents / payments），FROM/JOIN 逐一校验。
4. **强制 LIMIT**：无 LIMIT 自动补 50，超过 200 钳制到 200。
5. **超时保护**：SQL 执行 10 秒超时终止等待。
6. **审计日志**：每次查询写入 `query_audit` 表（时间、原问题、SQL、行数、耗时、是否报错），
   满足银行合规留痕要求。
7. **Secret 管理**：`.env` 只提供模板，真实 key 不进代码库、不进日志。

## 数据模型

| 表 | 中文含义 | 关键字段 |
|---|---|---|
| corporates | 企业客户 | 中英文名、国家、信用评级、客户经理 |
| letters_of_credit | 信用证 | 信用证号(PK)、申请人/受益人、金额币种、到期日、状态 |
| shipments | 货物发运 | 提单号、船名航次、装/卸货港、ETA/ATA、货物状态 |
| documents | 单据 | 单据类型、交单日期、审单状态、不符点 |
| payments | 付款 | 金额、应付/实付日期、付款状态（待付款/已付款/逾期） |

建表 SQL：`backend/db/schema.sql`；种子数据：`backend/db/seed.sql`
（8 家企业、15 个信用证、20 条发运、28 条单据、8 条付款，含逾期与不符点案例）。

## 生产环境映射（给银行 IT）

本演示用 `Python + LangGraph + SQLite + Gemini` 保证开箱即跑；
生产落地到银行技术栈时的对应关系：

| 演示 | 生产（建议） |
|---|---|
| Python LangGraph | **LangGraph4j**（Java 版图编排，节点逻辑 1:1 翻译） |
| langchain-openai ChatOpenAI | **Spring AI** `ChatClient`（OpenAI 兼容或行内 LLM 网关） |
| SQLite（`mode=ro`） | **Oracle 只读备库 / 只读账号**（`GRANT SELECT` 最小权限 + Resource Manager 限流） |
| Gemini 公有云 | **行内私有 LLM**（或专线网关），客户数据不出内网 |
| query_audit 表 | 审计日志进 **SIEM / 行内日志平台**，保留满足监管年限 |
| 无鉴权演示 | **RBAC**：按客户经理的客户组合过滤数据（SQL 改写加 `relationship_manager = ?` 条件）；**数据脱敏**：金额/对手方按角色脱敏后展示 |

银行 hardening 清单（投产前必做）：
- RBAC 行级权限：查询自动拼接待办人/所属机构的数据隔离条件。
- 数据脱敏：非授权角色看到的金额、对手方做掩码。
- Prompt 注入防护：用户问题只作为"问题"传入，不拼进 system prompt 的指令区；
  SQL 生成后仍走护栏二次校验（本演示已实现）。
- 审计：全量记录问题原文、SQL、行数、耗时、报错，进不可篡改存储。
- 模型与连接池走行内网关，配置熔断与重试预算。

## 目录结构

```
trade-text2sql/
  backend/
    main.py            FastAPI 入口（/api/chat|health|schema + 静态前端 + 审计）
    db/
      schema.sql       建表（含中文注释）+ query_audit 审计表
      seed.sql         种子数据
      db.py            建库/只读执行（10秒超时）
    agent/
      config.py        LLM 配置（环境变量，无 key 不报错）
      schema_catalog.py  表结构英文目录 + few-shot 示例
      guardrail.py     SQL 安全护栏
      graph.py         LangGraph 图（7 节点）
    tests/
      test_guardrail.py  护栏单元测试
      test_e2e.py        Fake LLM 端到端测试
      test_api.py        HTTP 接口测试
      fakes.py           测试用 Fake LLM
  frontend/
    index.html         浅色 Apple 风聊天页面（单文件）
  requirements.txt
  .env.example
  README.md
```
