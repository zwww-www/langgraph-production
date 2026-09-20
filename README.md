# SafeOps Agent

基于 **LangGraph、FastAPI、PostgreSQL 和 Next.js** 的智能运营工作台，支持阿里云百炼真实模型调用、人工审批、持久化恢复、副作用防重与异常对账。

用户用自然语言提交账务、账户或订阅请求，模型负责识别意图并提出工具调用计划，系统通过确定性策略决定是否执行、拒绝或交由人工审批。执行过程、模型用量、审批决定和工具回执均可在中文控制台查看。

> 当前项目使用合成业务数据演示发票、账户和订阅操作。阿里云模型调用已经实际验证；业务工具尚未连接真实支付或账户系统。本项目是面向生产问题的工程参考实现，不代表已经完成生产部署。

## 功能概览

| 能力 | 实现方式 |
| --- | --- |
| 自然语言任务分派 | Supervisor 输出经过校验的业务领域与置信度 |
| 领域隔离 | 账务、账户、订阅三个实际编译的 LangGraph 子图，各有独立提示词与工具目录 |
| 确定性授权 | Policy Engine 根据工具、金额、风险和策略版本决定执行路径 |
| 人工审批 | `interrupt` 挂起、持久化审批、授权角色校验和恢复执行 |
| 副作用防重 | 稳定动作身份、幂等键、PostgreSQL 执行账本与已完成回执复用 |
| 不确定结果处理 | `in_doubt` 状态与管理员显式对账，不将超时等同于失败 |
| 故障恢复 | 官方 `AsyncPostgresSaver`、持久化任务队列及运行锁 |
| 运行观测 | 数据库事件、SSE 时间线、模型 token 用量、耗时和检查点历史 |
| 安全回放 | 从检查点创建独立模拟执行分支，使用模拟工具回执 |
| 长期记忆 | 按客户保存结构化偏好、套餐信息及近期工单引用 |
| 模型接入 | 阿里云百炼、通用 OpenAI 兼容协议、离线规则和 cassette |
| 工具接入 | 本地合成业务工具、HTTP、MCP，均受固定工具白名单约束 |

## 系统架构

```mermaid
flowchart TD
    UI[中文控制台 / API 客户端] --> API[FastAPI]
    API --> Queue[PostgreSQL 持久化任务队列]
    Queue --> Worker[异步 Worker]
    Worker --> Supervisor[Supervisor 意图分派]
    Supervisor --> Billing[账务子图]
    Supervisor --> Account[账户子图]
    Supervisor --> Subscription[订阅子图]
    Supervisor --> Policy[确定性策略评估]
    Billing --> Policy
    Account --> Policy
    Subscription --> Policy
    Policy -->|允许| Execute[重新校验授权 / 执行账本 / 工具调用]
    Policy -->|需要审批或转人工| Approval[人工审批 / interrupt]
    Policy -->|拒绝或直接答复| Compose[生成答复]
    Approval -->|批准业务动作| Execute
    Approval -->|拒绝或人工接管| Compose
    Execute --> Verify[回执验证]
    Verify --> Compose
    Compose --> End[结束]
    API -.持久化事件 / SSE.-> UI
```

模型输出始终视为待验证的计划。Supervisor 不能直接调用业务工具；领域子图校验工具名称、参数结构和引用来源；执行节点再次核对动作身份、当前策略及审批记录。最终答复从已验证回执生成，不由模型随意宣称操作成功。

API 先保存任务再返回 `202`。进程内 Worker 从 PostgreSQL 读取任务，通过事务级 advisory lock 防止同一任务并发执行。进程退出后锁自动释放，重启可从检查点继续。当前单 Worker 逐个处理任务，多进程通过数据库锁协调；没有引入独立消息中间件。

### 默认业务策略

| 操作 | 默认处理 |
| --- | --- |
| 查询发票、支付状态、账户、订阅 | 允许只读查询，并校验客户归属 |
| 退款金额 ≤ 1000 美分 | 允许自动处理 |
| 1000 < 退款金额 ≤ 100000 美分 | 需要财务审批 |
| 退款金额 > 100000 美分 | 拒绝 |
| 重置凭据、锁定账户 | 需要安全角色审批 |
| 变更套餐、取消订阅 | 需要运营角色审批 |
| 低置信度、无法安全规划的请求 | 转人工处理 |

阈值通过环境变量配置；管理员可满足所有审批角色。通过人工接管审批不会凭空生成一个业务动作。

## 技术栈与目录

- 后端：Python 3.12、异步 FastAPI、Pydantic、httpx。
- 工作流：LangGraph、PostgreSQL checkpointer。
- 数据库：PostgreSQL 17、SQLAlchemy Async、psycopg、Alembic。
- 前端：Next.js 16、React 19、TypeScript。
- 质量检查：pytest、Ruff、mypy、GitHub Actions。

```text
src/safeops/
├── api/             认证、HTTP 接口和 SSE
├── graph/           Supervisor、领域子图、业务节点、Runtime 和 Worker
├── domain/          数据模型、规范化动作身份与业务异常
├── policy/          风险评估和确定性授权
├── approvals/       审批请求、角色校验及防重放
├── effects/         副作用账本、回执与对账
├── tools/           工具注册表、本地业务、HTTP/MCP 适配器
├── persistence/     数据库连接与应用表
├── events/          持久化事件与结构化日志
├── memory/          跨工单的结构化客户记忆
├── llm/             真实模型、离线模型与 cassette
└── evaluation/      路由、策略和故障恢复评估
apps/web/            中文运营控制台
migrations/          应用表迁移
tests/              单元测试与 PostgreSQL 集成测试
docs/                审计、验证、本机配置和阅读指南
```

## 快速启动

以下命令使用 PowerShell。Python 环境使用 conda。

### 当前电脑：使用已配置环境

本机 PostgreSQL 位于 `E:\PostgreSQL\17`，数据库服务端口为 `5432`；项目已配置 `.env`。由于另一个项目使用 `8000`，本机 SafeOps 使用 `8001`。

```powershell
Set-Location 'langgraph-production'
conda activate '.conda-safeops'
safeops serve --port 8001
```

另开终端启动前端：

```powershell
Set-Location 'langgraph-production\apps\web'
npm run dev
```

前端已通过 `apps/web/.env.local` 配置 `BACKEND_URL=http://127.0.0.1:8001`。若服务已经运行，无需重复启动。

- 控制台：[http://127.0.0.1:3000](http://127.0.0.1:3000)
- API 文档：[http://127.0.0.1:8001/docs](http://127.0.0.1:8001/docs)
- 就绪检查：[http://127.0.0.1:8001/ready](http://127.0.0.1:8001/ready)
- 演示管理员：`demo-admin`；演示运营账号：`demo-operator`。

### 新环境：安装与初始化

准备 conda、Node.js 22、npm 和 PostgreSQL 17，然后在项目根目录执行：

```powershell
conda env create -f environment.yml
conda activate safeops
# 仅首次创建，不覆盖已有数据库连接和 Key
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

使用数据库管理员创建项目账号及数据库（`psql` 需加入 PATH，或使用安装目录下的完整路径）：

```powershell
psql -h 127.0.0.1 -p 5432 -U postgres -d postgres
```

进入 psql 后执行；账号已存在时跳过对应创建步骤：

```sql
CREATE ROLE safeops LOGIN;
\password safeops
CREATE DATABASE safeops OWNER safeops;
CREATE DATABASE safeops_test OWNER safeops;
\q
```

在 `.env` 中设置连接地址。示例中的密码需替换；若包含 URL 特殊字符，应进行 URL 编码。

```dotenv
DATABASE_URL=postgresql://safeops:YOUR_PASSWORD@127.0.0.1:5432/safeops
ENVIRONMENT=development
LLM_PROVIDER=offline
```

初始化并启动后端：

```powershell
safeops migrate
safeops demo --seed-only
safeops serve --port 8001
```

另开终端安装并启动前端：

```powershell
Set-Location apps/web
npm ci
# 仅首次创建；已有配置时直接检查其中的 BACKEND_URL
if (-not (Test-Path .env.local)) {
    Set-Content .env.local 'BACKEND_URL=http://127.0.0.1:8001' -Encoding utf8
}
npm run dev
```

`safeops migrate` 先运行 Alembic，再初始化官方 checkpointer 表；多个 Worker 启动前完成一次迁移即可。Windows 下应使用 `safeops serve`，它会设置 psycopg 异步连接所需的 Selector 事件循环。

### Docker Compose

```powershell
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
docker compose up --build
```

Compose 按顺序启动 PostgreSQL、迁移、种子数据、后端和前端；容器方式的后端映射为 `8000`，前端为 `3000`。请确保这些端口未被本机服务占用。Compose 会覆盖数据库地址，前端通过容器服务名访问后端。

本机因未安装 Docker，尚未实际验证容器构建与启动。仓库提供了相应 CI 检查，但不把“已配置 CI”等同于“CI 已运行成功”。

## 接入阿里云真实模型

在本地 `.env` 中配置：

```dotenv
LLM_PROVIDER=aliyun
LLM_MODEL=qwen3.8-flash
LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_API_KEY=YOUR_API_KEY
LLM_TIMEOUT_SECONDS=60
LLM_MAX_TOKENS=2048
```

上述地址是本项目已验证的接口地址；其他地域应使用与 Key 对应的地址。修改后重启后端。`.env` 已被 Git 忽略，不要将真实 Key 写入 README、示例文件、工单或日志。

阿里云适配器请求 JSON 输出，并发送 `enable_thinking=false`。配置缺失、接口失败、超时及输出不完整会报错，不自动重试、不悄悄切回离线模型。模型返回内容仍经过业务结构与权限校验。

| Provider | 用途 |
| --- | --- |
| `aliyun` | 阿里云百炼，额外设置关闭思考模式 |
| `openai-compatible` | 显式配置的 HTTPS 兼容端点 |
| `offline` | 可重复的离线规则，不访问模型服务 |

真实模型参与路由和领域规划；授权、业务工具及回执答复仍由代码控制。日常提交请求会消耗模型额度，检查点模拟回放在需要重新规划时也可能调用模型。

接口参考：[模型说明](https://help.aliyun.com/zh/model-studio/qwen3-8-flash)、[思考模式](https://help.aliyun.com/zh/model-studio/deep-thinking)。

## 使用示例

### 1. 查询发票

在控制台输入客户 `CUS-001`，提交：

> 请查询发票 INV-10032 的金额、付款状态和已退款金额，只查询，不修改数据。

执行流程：任务分派 → 账务规划 → 策略评估 → `lookup_invoice` → 回执验证 → 完成。

初始化数据中，这张发票总额为 90 美元、已付款、已退款 0 美元。执行过退款后，应以当前数据库回执为准。

CLI 等价示例：

```powershell
safeops demo --ticket-id DEMO-LOOKUP --text '查询发票 INV-10032 的付款状态'
```

### 2. 人工审批退款

提交：`退款 INV-10032 的 $45`。

1. 模型提出退款计划，金额规范化为 `4500` 美分。
2. 策略判定需要财务审批，运行进入“待审批”。
3. 在“审批中心”查看动作、参数、风险与所需角色。
4. 使用 `demo-admin` 填写理由并批准，Worker 读取持久化决定后恢复执行。
5. 在运行详情查看工具回执。拒绝审批不会触发退款。

`demo-operator` 无权批准财务操作。CLI 可显式批准合成演示动作：

```powershell
safeops demo --ticket-id DEMO-REFUND --text 'Refund $45 on INV-10032' --approve
```

同一工单重复提交会复用运行；修改已有工单内容会被拒绝。新工单代表新的业务请求。种子初始化不会重置已有余额。

### 3. 故障恢复与对账

建议在独立演示或测试环境中执行，并暂停其他处理同一数据库任务的 Worker，以便观察故障边界：

```powershell
safeops demo --ticket-id DEMO-CRASH --text 'Reset credentials ACC-2041' --approve --crash-at external_effect_executed
```

该示例在合成业务操作完成后中断，关闭 Runtime 并用新连接恢复。由于最终回执尚未确认，运行进入 `in_doubt`，不会盲目重复调用工具。

在“待对账操作”中核查下游结果并填写证据：

- **确认已执行**：必须提供已核实的结果回执；恢复时复用回执。
- **确认未执行**：释放执行声明，允许使用原幂等键重试；若已有执行证据则拒绝此决定。

也可使用 CLI 维护：

```powershell
safeops reconcile --effect 'RUN_ID:ACTION_ID' --outcome not_performed --evidence '已按幂等键查询下游，确认无执行记录' --token demo-admin
```

只有真正核查过下游后才能确认结果。对账会排队恢复任务，需要运行中的 API Worker 继续处理。

## 为什么需要执行账本

**检查点恢复不等于外部副作用恰好执行一次。** 外部退款可能已经成功，而本地尚未保存检查点。直接重试节点可能重复退款。

SafeOps 使用 `thread_id:action_id` 作为稳定操作键；动作身份来自客户、工单、工具、领域及规范化参数。先原子声明执行，再调用工具、记录执行证据并保存完成回执。

| 故障位置 | 恢复处理 |
| --- | --- |
| 执行声明已保存，工具结果未知 | 进入 `in_doubt`，等待对账 |
| 下游已执行，本地证据未保存 | 进入 `in_doubt`，核查下游 |
| 执行证据已保存，完成回执缺失 | 保留证据，等待对账确认 |
| 完成回执已保存，图检查点尚未保存 | 复用回执，不再次派发 |

系统不声称提供跨任意外部服务的分布式 exactly-once。实际保证依赖下游的幂等、客户归属和业务限额契约。不同工单的语义重复也不能仅靠运行幂等键消除。

审批同样绑定线程、工单、客户、动作、相关状态、风险、角色和策略版本。完全相同的审批重放被接受，修改决定、审批人或理由会冲突。执行时再次核对策略，避免审批后权限状态变化。

## API 速查

除健康检查外，业务 API 使用 `Authorization: Bearer <token>`；身份和角色由服务端映射，客户端不能通过请求体声明审批角色。

| 方法与路径 | 用途 |
| --- | --- |
| `POST /api/tickets` | 创建不可变工单 |
| `POST /api/runs` | 创建或复用运行并排队 |
| `GET /api/runs`、`GET /api/runs/{id}` | 列表与运行状态 |
| `GET /api/runs/{id}/state` | 当前持久化状态 |
| `GET /api/runs/{id}/checkpoints` | 检查点历史 |
| `GET /api/runs/{id}/events`、`/stream` | 历史事件与 SSE |
| `POST /api/runs/{id}/resume` | 排队恢复，不接受任意状态或审批注入 |
| `POST /api/runs/{id}/replay` | 从指定检查点创建模拟执行分支 |
| `GET /api/approvals`、`GET /api/approvals/{token}` | 审批队列与详情 |
| `POST /api/approvals/{token}/approve`、`/reject` | 提交审批决定 |
| `GET /api/effects/in-doubt`、`GET /api/effects/{key}` | 待对账操作与账本详情 |
| `POST /api/effects/{key}/reconcile` | 管理员对账 |
| `GET /api/customers/{id}/memory` | 结构化客户记忆 |
| `GET /health`、`GET /ready` | 存活与就绪状态 |

SSE 使用持久化数字游标，支持 `after` 和 `Last-Event-ID`。前端通过 fetch 携带认证头读取流，不将令牌放入 URL。检查点分支会清除旧审批和回执；已有计划从策略节点重新评估，早期检查点重新路由或规划，空初始化检查点不可回放。

## 测试与评估

### 静态检查与单元测试

```powershell
conda activate safeops
ruff check src tests migrations
ruff format --check src tests migrations
mypy src/safeops
pytest tests/unit -q
```

当前电脑请将环境激活命令替换为前文的 conda 绝对路径。测试使用模拟传输或离线模型，不需要真实模型调用。

### PostgreSQL 完整测试

在专门的终端设置独立测试数据库连接：

```powershell
$env:DATABASE_URL='postgresql://safeops:YOUR_PASSWORD@127.0.0.1:5432/safeops_test'
$env:TEST_DATABASE_URL=$env:DATABASE_URL
$env:LLM_PROVIDER='offline'
safeops migrate
pytest -q
safeops eval --database --json reports/evaluation.json
```

**集成测试会清空指定测试库中的应用和检查点数据。** 数据库名必须以 `_test` 结尾，禁止指向演示库或生产库。未配置 `TEST_DATABASE_URL` 时，相关集成测试会显式跳过。完成后关闭此终端，避免启动服务时误用测试库环境变量。

```powershell
# 无数据库的路由、工具参数与策略评估
safeops eval

# 前端类型检查与生产构建
Set-Location apps/web
npm ci
npm run build
```

`safeops eval` 的默认路由样例使用离线规则；`--database` 会另外执行真实图、审批和恢复流程，其 Runtime 读取模型配置。因此，复现离线指标时必须像上面一样显式设置 `LLM_PROVIDER=offline`。数据库评估拒绝外部工具绑定，并会写入合成业务和评估记录，应使用独立测试库。

故障注入包含九个边界：规划前后、审批前后、声明执行后、外部执行后、执行证据记录后、答复生成前后。恢复比较覆盖领域、计划、策略、结果及节点轨迹；运行编号、时间和生成的下游操作编号不纳入语义一致性比较。

### 已完成验证

| 检查 | 实际结果 |
| --- | --- |
| 自动化测试 | 接入阿里云后 87 项通过 |
| Ruff / mypy | 通过 |
| Next.js 生产构建 / TypeScript | 通过 |
| 新 PostgreSQL 安装迁移 | 通过 |
| 浏览器操作 | 中文界面、查询、审批、回执、时间线、检查点模拟回放已验证 |
| 离线 20 条样例 | 路由、宏平均 F1、完整工具调用准确率均为 1.0 |
| 九点故障评估 | 6 个确定性完成，3 个预期待对账，重复外部操作为 0 |
| 阿里云真实调用 | `qwen3.8-flash` 完成路由和工具规划，查询成功 |
| Docker / 公共 MCP / 真实支付系统 | 尚未实际验证 |

离线样例是与规则一起编写的小规模合成语料，不能代表真实模型或生产流量准确率。87 项自动化测试的结果也不等于真实模型全场景验收。

最近一次展示案例运行编号为 `33b719629e6c4051a8f529cb15356d11`，查询 INV-10032，总耗时约 3.2 秒。两次真实模型调用分别使用 195 和 352 token，共 547 token；业务回执为已付款、总额 90 美元、已退款 0 美元。

## 扩展与部署边界

- **HTTP/MCP**：仅可将已登记工具绑定到运维配置的地址，模型不能选择服务器或扩展权限。下游必须接收客户范围和幂等键，并返回结构化回执。
- **Cassette**：支持 `record`、`replay`、`strict_replay`；回放未命中直接失败。保存响应与提示摘要，不保存原始提示正文；单个文件使用单写入者。
- **记忆与检查点**：记忆跨工单保存受限结构化信息，检查点负责单个工作流恢复；不会任意持久化模型生成的个人信息。
- **认证**：默认 token 用于内部演示，生产模式拒绝默认演示 token。面向客户的多租户授权、SSO、限流、TLS、备份和故障切换需要另行建设。
- **敏感信息**：常见密钥字段及模式会脱敏，但自由文本脱敏不是完整的个人信息识别系统。
- **吞吐量**：已实现运行锁和持久化恢复，尚未完成多主机负载、数据库故障切换及生产容量验证。

MCP 配置示例：

```dotenv
MCP_SERVERS={"ops":"http://localhost:9000/mcp"}
TOOL_BINDINGS={"issue_refund":{"transport":"mcp","server":"ops","tool":"refund"}}
```

## 延伸阅读

- [原项目审计与重构说明](docs/AUDIT.md)
- [源码阅读顺序与面试讨论点](docs/READING_GUIDE.md)
- [整体验证记录](docs/VERIFICATION.md)
- [当前电脑的数据库和端口配置](docs/LOCAL_SETUP.md)
- [阿里云真实调用验证](docs/ALIYUN_VERIFICATION.md)
- [离线评估报告样例](docs/evaluation-example.json)

## 许可证

采用 [MIT License](LICENSE)，保留原项目版权和署名信息。
