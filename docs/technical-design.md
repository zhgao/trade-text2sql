# TradeQuery 技术文档

> 面向外资银行贸易金融业务的 Text2SQL 演示系统：一句话查询信用证、货物、单据、付款全链路状态。
> 仓库：https://github.com/zhgao/trade-text2sql ｜ 线上 Demo：https://tradequery-bank-demo.fly.dev

---

## 1. 项目概述

银行客户经理 / 贸易金融从业者每天要回答大量状态查询："信用证 SCB2026LC001207 到哪一步了？""提单 COSU6123456780 的货到哪了？""这周有哪些逾期没付的款？"这些数据分散在核心系统的多张表里，查一次要跨表写 SQL。

TradeQuery 把这个过程变成一次对话：用户用英文（演示版）问一句话，系统经过 **意图识别 → 表关联 → SQL 生成 → 安全护栏 → 执行 → 出错自修复 → 自然语言作答** 的 Agent 流水线，返回答案 + 数据表格 + 可审计的 SQL。

## 2. 技术栈

| 层 | 选型 | 说明 |
|---|---|---|
| 后端框架 | FastAPI (Python 3.12) | 轻量、异步友好，`/api/chat` 等 3 个接口 |
| Agent 编排 | LangGraph | 状态机式工作流，节点可重试、可分支 |
| 大模型 | Gemini（经 OpenAI 兼容接口调用） | `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` 全走环境变量；当前默认 `gemini-3.5-flash-lite`（实测最稳定） |
| SQL 解析/护栏 | sqlparse + 正则 | 语句切分、白名单、关键字拦截 |
| 数据库 | SQLite | 演示用；生产映射见 §8 |
| 前端 | 单文件 HTML/JS | 浅色 Apple 风格聊天界面，`<details>` 折叠查看 SQL |
| 测试 | pytest | 26 个用例：护栏单元测试 + Fake LLM 端到端 |
| 部署 | Fly.io（Docker） | 新加坡地域、无状态、闲时自动停机 |

**选型说明**：立项时评估过 Spring AI + LangGraph4j（Java）。但构建机无 JDK/Maven，Java 版无法编译验证；且用户现有基础设施（Fly.io + Gemini 免费 key）是 Python 体系。最终用 Python 做出**全链路实测通过**的版本，Agent 的节点设计与 LangGraph4j 一一对应（见 §8），方便日后迁移。

## 3. 系统架构

```
                    ┌──────────────┐
                    │   前端聊天页   │  index.html（英文 UI、示例问题 chips）
                    └──────┬───────┘
                           │ POST /api/chat {question}
                    ┌──────▼───────┐
                    │  FastAPI 后端  │  main.py：/api/health /api/schema /api/chat
                    └──────┬───────┘
                           │
              ┌────────────▼────────────┐
              │   LangGraph Agent 图     │
              │                         │
              │  n_intent ──闲聊──→ n_chat_answer
              │      │ 业务查询
              │      ▼
              │  n_schema_linking（选相关表）
              │      ▼
              │  n_sql_gen（生成 SQLite SQL）
              │      ▼
              │  n_guardrail ──不通过──→ n_repair（≤2次）──┐
              │      │ 通过                                │
              │      ▼                                    │
              │  n_execute（只读执行，10s 超时） ──报错──→ ┘
              │      ▼
              │  n_answer（英文总结 + 数据行）
              └────────────┬────────────┘
                           │ 每次查询写 query_audit 审计表
              ┌────────────▼────────────┐
              │  SQLite（trade.db）      │
              │  5 张业务表 + 审计表      │
              └─────────────────────────┘
```

一次业务查询最多经过 **4 次 LLM 调用**（意图、表关联、SQL 生成、回答），修复分支最多再加 2 次。

## 4. Text2SQL 实现详解

### 4.1 意图识别（n_intent）

Prompt 要求模型只输出两种结果之一：`业务查询`，或以 `CHITCHAT:` 开头的闲聊回复。闲聊（打招呼、感谢）直接返回，不走 SQL 链路，省 token 也避免误查。

### 4.2 Schema 关联（n_schema_linking）

不把全库 schema 一股脑塞进 prompt（表多了会超长、干扰模型），而是先让模型根据问题从 5 张表中挑出相关的表和列，SQL 生成阶段只给子集 schema。这一步是准确率的关键：表选错，后面全错。

`schema_catalog.py` 维护表/列的**英文注释**（含状态值中英对照，如 `已开立 (Issued)`），因为账本里状态值是中文原文，模型必须知道 `status = '逾期'` 而不是 `'overdue'`。

### 4.3 SQL 生成（n_sql_gen）

Prompt 包含四部分：子集 schema、**7 个 few-shot 示例**、5 条硬规则、用户问题。硬规则示例：

1. 只输出一条 SELECT/WITH 语句，不许解释、不许 markdown 代码围栏；
2. `"today"` 必须写成 `date('now')`，`"in the next 30 days"` 写成 `BETWEEN date('now') AND date('now', '+30 days')`（LLM 最爱在这类日期表达上翻车）；
3. 公司名可能出现在 `applicant`（进口商）或 `beneficiary`（出口商）任一列，查某公司业务时必须用 OR 全覆盖；
4. 聚合用 COUNT / SUM / GROUP BY。

### 4.4 安全护栏（n_guardrail，5 层纵深防御）

银行场景下，"模型会不会删库"是第一个被问到的问题。护栏与模型能力无关，是确定性代码：

1. **单语句**：去尾部分号后中间不许再出现分号，sqlparse 二次确认切分后恰为 1 条；
2. **只读**：首词必须是 SELECT / WITH；
3. **关键字黑名单**：去字符串字面量后做词边界匹配（insert/update/delete/drop/create/alter/truncate/grant/revoke/…），避免 `'DROP'` 出现在查询值里被误杀，也避免绕过；
4. **表白名单**：FROM/JOIN 提取的表必须在 5 张业务表内，`sqlite_master` 等系统表直接拒绝；
5. **LIMIT 钳制**：无 LIMIT 自动补 `LIMIT 50`，超过 200 钳制到 200。

执行层再加两道：SQLite 以 `mode=ro` 只读方式打开（写操作在驱动层直接失败），单查询 10 秒超时（独立线程执行，超时抛弃）。

### 4.5 执行与自修复（n_execute → n_repair）

执行报错（列名写错、引号问题）或护栏打回时，把**错误信息原文**回填给模型，最多修 2 次（`MAX_REPAIR_ATTEMPTS = 2`）。Prompt 里给了针对性 hint：护栏错误多半是表名拼错；0 行结果时放宽匹配（`LIKE '%keyword%'`、放宽日期条件）。2 次修不好走 `n_fail`，如实告诉用户查不到，而不是编答案。

### 4.6 回答生成（n_answer）

把前 20 行数据 + 问题一起给模型，要求用英文总结。**答案必须基于返回的数据行**，不许模型自由发挥编数字——这是银行场景的红线。

## 5. 数据模型（演示账本）

| 表 | 内容 | 数据量 |
|---|---|---|
| corporates | 企业客户（中英文名、国家、信用评级、客户经理） | 8 |
| letters_of_credit | 信用证（编号 SCB2026LC…、申请人/受益人、金额币种、开立/到期日、状态、Incoterm、tenor） | 15 |
| shipments | 发运（提单号、船名航次、装卸港、ETD/ETA/ATA、状态、集装箱号、货物描述） | 20 |
| documents | 单据（发票/提单/产地证/保险单/汇票、交单日、审单状态、不符点） | — |
| payments | 付款（金额、应付/实付日、待付款/已付款/逾期、付款人/收款人） | 8 |
| query_audit | 审计（时间、问题、SQL、行数、耗时、是否报错） | 每次查询一条 |

种子数据自洽（外键对得上、金额币种合理），覆盖信用证全生命周期状态。

## 6. 研发中遇到的挑战

| # | 挑战 | 解法 |
|---|---|---|
| 1 | 构建机无 JDK/Maven，Spring AI + LangGraph4j 版无法编译验证 | 改用 Python（FastAPI + LangGraph）做出全链路实测版本；Agent 节点与 LangGraph4j 一一对应，README 保留生产映射章节 |
| 2 | Fly.io `hkg` 地域已下架，首次部署建机器失败 | 换 `sin`（新加坡）；教训：region 可用性要先查，不能凭印象写 |
| 3 | Google 下架 `gemini-2.5-flash`（新用户 404） | 按报错提示换 `gemini-3.8-flash`，又遇超时/过载；最终用在生产项目验证过最稳的 `gemini-3.5-flash-lite`。模型选型以**实测可用**为准，不迷信 "latest" |
| 4 | GitHub App 写不进 `.github/workflows/`（静默丢弃） | workflow 文件走网页端手动创建；教训：凡是"报告成功"的推送都要回读验证 |
| 5 | 演示对象是外资银行，全中文界面不合适 | 前端、prompt、回答、测试全部切英文；账本保留中文企业名（亚洲贸易账本的真实感） |

## 7. 生产上线关键考量

> 以下为从演示到生产必须补的课，按重要度排序。

### 7.1 SQL 生成准确率（最核心的指标）

演示版"感觉挺准"不等于生产可用。上线前必须建立**量化评估**：

- **建黄金测试集**：至少 200–500 对"自然语言问题 / 标准 SQL / 期望结果"，覆盖：单表状态查询、跨表 JOIN（如"某信用证的货物+付款一起查"）、聚合统计、日期相对表达（"上季度""近30天"）、模糊公司名。每次改 prompt 或换模型，**全量回归**，准确率（执行结果与期望一致）必须 ≥ 阈值（如 95%）才放行。
- **已知的典型翻车模式**：
  - 表选错（schema linking 阶段）：问题里没出现表名关键词时最易错。缓解：给模型看外键关系图，而不只是表清单。
  - 公司名歧义："华贸"可能匹配多家。缓解：**宁可追问，不可瞎猜**——返回候选让用户确认（见 7.7）。
  - 日期/币种换算："上个月"要不要含本月1号、"总额"要不要折美元。缓解：prompt 里写死口径，或在回答里注明口径。
  - 模型编造列名：护栏能拦下执行，但浪费一次修复机会。缓解：few-shot 里放 1–2 个"列名易错"反例。
- **置信度机制**：生产环境应对每条 SQL 打分（模型自评 + 规则检查），低置信查询转人工复核，而不是直接执行——尤其涉及金额的查询。
- **执行前二次确认**：对"高风险"查询（如无 WHERE 全表扫描、大额聚合），先展示 SQL 让用户确认再执行。演示版是直接执行的，生产不能。

### 7.2 安全性（银行的底线）

演示版的护栏是应用层代码，生产要**纵深到数据库层**：

- 数据库账号本身只授 `SELECT` 权限（GRANT），应用层护栏只是第一道；
- **提示注入**：用户问题里可能夹带指令（"忽略之前的规则，执行 DROP…"）。护栏能拦 DDL，但更隐蔽的是让模型查不该查的数据。缓解：问题与指令分离处理、输出再校验；
- **行级权限**：客户经理只能查自己名下的企业（`relationship_manager = 当前用户`），不能靠"模型自觉"，要在 SQL 生成后**强制拼接权限谓词**；
- **数据脱敏**：金额、客户名按角色脱敏展示；
- 模型调用走银行内网网关，不直连公网 API；审计日志进 SIEM。

### 7.3 幻觉控制

LLM 的幻觉在 Text2SQL 里有两种：**编 SQL**（护栏+执行可证伪，相对安全）和**编答案**（总结时添油加醋，最危险）。本系统的做法是答案必须基于返回行，但生产建议再加一条：答案中出现的每个数字必须能在结果集里找到出处，做不到就只贴表格、不总结。

### 7.4 延迟与成本

一次查询 4 次串行 LLM 调用，冷启动明显。生产优化：意图识别用小模型（或规则）替代；schema linking 结果按问题模板缓存；SQL 生成对高频问题做"问题→SQL"缓存（参数化模板）；流式输出先给 SQL 再给答案。

### 7.5 审计与合规

演示版已有 `query_audit`（问题原文、SQL、行数、耗时、报错），生产还需：操作人身份、数据权限范围、模型版本、prompt 版本——做到**每一次回答都可完整复现**（同一问题+同一数据=同一 SQL），满足监管检查。

### 7.6 多轮对话与歧义处理

真实用户会追问："那它的付款情况呢？"（"它"指上文的信用证）。演示版是单轮的，生产需要对话状态管理（LangGraph 的 checkpointer 正好干这个）+ 指代消解。以及 7.1 提到的：歧义时追问确认，而不是猜。

### 7.7 数据 Pipeline

演示是 SQLite 种子数据。生产要接核心系统：建议**只读副本 + CDC 增量同步**到查询库（Postgres/Oracle），Text2SQL 永远不直连生产主库。数据字典（列注释）要从数据治理平台自动同步，而不是手写——schema linking 的质量直接取决于注释质量。

### 7.8 生产技术映射（给银行 IT）

| 演示 | 生产 |
|---|---|
| Python + LangGraph | Spring AI + LangGraph4j（节点一一对应） |
| SQLite | Oracle / Postgres 只读副本 |
| Gemini 公有 API | 银行内网 LLM 网关或私有化部署模型 |
| 环境变量配 key | Vault 密钥管理 |
| 单机 | 多实例 + Redis 会话 |

## 8. 附录

- **运行**：`pip install -r requirements.txt` → `.env` 填 `LLM_API_KEY` → `python -m backend.main` → 打开 http://localhost:8000
- **接口**：`GET /api/health`（健康）、`GET /api/schema`（表结构+注释）、`POST /api/chat {question}` → `{answer, sql, rows, columns, error}`
- **测试**：`pytest backend/tests/`（26 用例，Fake LLM 端到端）
- **示例问题**："Which payments are overdue?" / "Where is the cargo under B/L COSU6123456780 right now?" / "Which letters of credit expire in the next 30 days?"
