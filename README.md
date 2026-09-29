# SafeOps — Risk-Budgeted Control Plane for Autonomous Business Agents

SafeOps 是面向自主业务 Agent 的**风险预算控制平面**。它把业务损失预算、人工审核产能、处理时延和历史业务结果编译成确定性授权策略，先对历史 canonical actions 回放，再由管理员明确发布。LangGraph 运行时只执行数据库中已发布的不可变策略。

核心主线：**Business economics → constrained optimization → deterministic Agent authorization**。

## 为什么需要它

固定的“超过 $10 就人工审批”无法回答：每天允许承担多少预计错误退款损失？财务团队能否处理积压？扩大自动处理权限后，损失和时延会怎样变化？

SafeOps 用可测量的历史结果和明确的业务约束回答这些问题。LLM 负责理解请求和规划工具调用；它不计算权限、不批准退款、不生成业务正确性的 ground truth。

## 架构

```mermaid
flowchart TD
    O[延迟业务结果 Business Outcomes] --> C[Wilson 风险校准]
    S[业务 SLO / 风险预算 / 审核容量] --> P[确定性约束优化]
    C --> P
    P --> F[候选策略与可行前沿]
    F --> B[历史 Action 回放与差异]
    B --> H[管理员审核并发布]
    H --> A[数据库中的不可变 Active Policy]
    A --> R[LangGraph Runtime]
    L[LLM 规划 → Canonical Action] --> R
    R --> G[确定性授权 / 人工审批 / 执行时复核]
    G --> E[Effect Ledger / 幂等执行 / 回执]
    E --> V[独立即时后置条件验证]
    V --> O
    E --> Q[in_doubt → 显式对账]
```

控制平面在 `risk/` 和 `policy/`，运行时在 `graph/`、`approvals/`、`effects/`。只有 `policy/engine.py` 一套授权算法，没有旧阈值回退或双引擎。

## 风险算法与真实字段

退款特征只来自已验证的结构化 Action：工具类型、金额、金额桶。金额桶上界集中定义为 1000、2000、5000、10000、30000、100000 美分。客户资历、历史退款次数、客户等级和争议记录没有可靠的请求时点数据，因此明确标为 unavailable，不由 LLM 猜测。

对每个金额分段，已知结果数为 n，incorrect / disputed / reversed 的数量为 k；unknown 不计入成功或失败。采用单侧 95% Wilson 上置信界：

```text
p = k / n, z = 1.6448536269514722
U = [p + z²/(2n) + z√(p(1−p)/n + z²/(4n²))] / [1 + z²/n]
n = 0 时 U = 1
运行时 expected_loss_cents = ceil(U × exposure_cents)
```

3 个样本、0 次失败不会被视为零风险。历史记录按到达时间切为 60% 训练、40% 验证；训练截止时间之后才观测到的结果被遮蔽为 unknown，避免未来信息泄漏。默认已知样本少于 30，或覆盖率低于 70% 的退款进入人工审核；硬拒绝边界优先。

只优化退款的经济权限。账户安全和订阅写操作保留强制审核；只读工具没有财务敞口。业务结果、回执和即时后置条件相互独立。

## 编译器实际做了什么

业务目标以不可变 `BusinessSLO` 快照持久化，包含按角色的审核容量。默认每日预算 50000 美分、7 日预算 350000 美分、自动处理目标 85%、整体 p95 目标 300 秒、财务审核容量 20 笔/小时。

- **目标函数**：最小化验证集上每笔业务成本之和。AUTO 成本为 U×金额；REVIEW 为人工成本 + 保守审核时延×每秒延迟成本 + 可配置人工残余错误率×金额；DENY 为拒绝摩擦成本。默认残余错误率为 0，是明确假设。
- **候选生成**：以硬拒绝金额的 `[0, .01, .02, .05, .1, .3, 1]` 为自动额度搜索网格，生成低、中、高风险的非递增额度组合；高风险人工额度搜索 `.3` 和 `1` 倍硬边界。风险段由 U≤.02、U≤.08、其余划分。默认网格产生 167 个候选，实际额度来自搜索结果。
- **约束**：每日/滚动预计损失上界、人工产能、队列深度、队列 SLA、整体 p95、自动化目标、硬拒绝、敏感工具强制审批、风险及金额权限单调性。人工残余错误损失也计入预算。
- **容量估计**：验证窗口至少 24 小时才使用观测到达率；审核时延至少 30 条观测才使用历史 p95。否则使用 SLO 中明确的保守回退值。时延取历史 p95 与“一小时需求+积压清空时间”的较大值；这是确定性容量近似，不声称是排队分布模型。审核比例≤5% 时，整体 p95 使用配置的自动处理时间。
- **前沿**：在可行解中按预计损失上界、审核负载、自动率比较 Pareto 支配关系；经济成本最低的可行候选为推荐。相同目标的等价规则可能同时位于前沿。
- **回放**：直接读取历史 Action 和结果，不重新规划，不调用 LLM；保存当前→候选与历史记录→候选两种转移统计，DENY→AUTO 独立统计。编译不会修改生效策略，无解时不会放宽目标。

这些估计依赖历史代表性、分段稳定性和结果覆盖率。人工审核过的历史及未执行的拒绝记录存在选择偏差；回放不是因果证明，不能声称知道已拒绝操作的反事实结果。Wilson 是分段概率区间，也不是整个投资组合损失的联合 95% 保证。

## 安全约束

- LLM never authorizes；optimizer never executes；未发布候选不影响运行时。
- 新库通过显式 `safeops migrate` 初始化保守策略。缺少生效策略、快照、发布记录或摘要不匹配时拒绝继续，不静默 AUTO。
- 执行链：Action → 确定性特征 → 已发布校准 → 风险上界 → 已发布额度规则 → ALLOW / REQUIRE_APPROVAL / DENY。
- 审批绑定 run/thread、客户、canonical action、完整策略及 revision；执行前重新获取生效策略。策略变化后必须新建请求并重新审核，不迁移旧审批。
- 发布持有 PostgreSQL 排他 advisory lock；执行时授权和下游调用持有对应共享锁，避免复核到调用之间切换策略。
- `thread_id = run_id`；官方 PostgreSQL checkpointer 保留中断与恢复。
- 稳定 `run_id:action_id` 幂等键、数据库执行账本及下游幂等契约共同避免重复副作用。
- 网络超时/结果不明进入 `in_doubt`，不会自动重试。管理员使用业务证据显式对账。
- dry-run 不产生真实副作用、不产生业务结果、不污染历史校准。
- 本地工具读取独立业务状态核验后置条件；不支持独立验证的 adapter 返回 unavailable。verified 不代表业务决策 correct。
- Business outcome observation 支持相同请求幂等重放；unknown 可追加为一个最终结果，矛盾结果返回 409，保留原观察历史。结果来源只接受明确的人工审计/下游系统等，不自动从 LLM 文本推断。

## 快速启动（Conda）

需要 PostgreSQL 17、Python 3.12、Node.js/npm。以下从项目根目录执行：

```powershell
conda env create -f environment.yml
conda activate safeops
Copy-Item .env.example .env
# 编辑 .env 中 DATABASE_URL，目标数据库需要预先创建
safeops migrate
safeops demo --seed-only
safeops serve --port 8001
```

当前机器已有 Conda 前缀 `D:\个人简历\个人项目\.conda-safeops`；可使用 `conda activate` 加该绝对路径。PostgreSQL 安装在 `E:\PostgreSQL\17`，配置说明见 [LOCAL_SETUP.md](docs/LOCAL_SETUP.md)。不要覆盖已有 `.env`。

另开终端启动中文控制台：

```powershell
Set-Location apps/web
npm ci
$env:BACKEND_URL='http://127.0.0.1:8001'
npm run dev
```

访问 http://127.0.0.1:3000 ，输入配置好的访问令牌。默认本地演示管理员令牌为 `demo-admin`；生产配置拒绝 demo 令牌。控制平面页面可编辑 SLO、生成历史、编译、比较候选和明确发布。

真实 LLM 只参与规划。在忽略提交的 `.env` 中设置：

```dotenv
LLM_PROVIDER=aliyun
LLM_MODEL=qwen3.8-flash
LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_API_KEY=your-local-key
```

模型可用性取决于实际账户。Key 不进入前端、URL、报告或仓库。控制平面的校准、编译和回放不需要 Key。离线规划通过 `LLM_PROVIDER=offline` 启用；不会替代风险算法。

## 可复现演示

默认命令只生成候选。`--release` 是管理员明确授权发布本次推荐候选；正常网页操作则先查看 diff 再点击发布。

```powershell
$env:SAFEOPS_ADMIN_TOKEN='demo-admin'
safeops risk-demo --count 100000 --seed 42 --output reports/risk-backtest.json
# 审核报告后可单独发布选定候选：
safeops release-policy --candidate <candidate-id>
# 完整本地演示：生成、编译、明确发布、执行一笔 $2 退款
safeops risk-demo --count 100000 --seed 42 --release --output reports/risk-backtest.json
```

也支持拆开执行：

```powershell
safeops generate-demo-history --count 100000 --seed 42
safeops compile-policy --dataset <dataset-id>
safeops run-policy-backtest --candidate <candidate-id>
# 使用审核过的业务目标文件创建新的持久化 SLO 快照
safeops compile-policy --dataset <dataset-id> --slo business-slo.json
```

`run-policy-backtest` 返回该不可变候选在编译时保存的回放结果；改变历史或 SLO 后应重新编译。未指定 dataset 的编译只读取运行时退款历史。risk-demo 的规划使用 offline，以排除 LLM 网络波动；实际执行经过完整 LangGraph 与 PostgreSQL 账本。本地工具操作的是项目演示业务数据库，不会调用真实支付网关。

### 2026-09-26 实际结果

完整输出保存在 [risk-backtest.json](docs/risk-backtest.json)，数字由代码计算。该次生成、持久化、编译和发布用时 185.718 秒（不包含随后运行时示例）。

| 指标 | 结果 |
|---|---:|
| 历史 Action / 训练 / 验证 | 100000 / 60000 / 40000 |
| 全部历史已知结果覆盖率 | 95.034% |
| 训练截止时间内已知结果 | 50660 |
| 候选 / 可行候选 | 167 / 60 |
| 生效前自动率 → 候选自动率 | 0% → 98.0025% |
| 候选审核率 / 拒绝率 | 1.9575% / 0.04% |
| 每日损失点估计 / 上界 | $339.79 / $411.68 |
| 每日风险预算 | $500 |
| 7 日损失上界 / 预算 | $2881.73 / $3500 |
| 审核需求 / 容量 | 1.3746 / 20 笔每小时 |
| 审核容量占用 | 6.8732% |
| 审核时延保守估计 / 整体 p95 | 247.44 秒 / 5 秒 |
| 相对原策略 REVIEW→AUTO / DENY→AUTO | 39201 / 0 |

选中规则的低/中/高风险自动额度为 `[30000, 10000, 2000]` 美分，人工额度均为 100000 美分。该额度仍受已知样本、覆盖率、实际风险分段及单调约束限制。发布 revision 为 `12742b288021441d67af8ec2d469124b7aa5f92a3d5550999f53e0d79b693f99`。

随后 run `54c359e5cdbc4a7e8c20e9c6facc5edc` 的 $2 退款得到 ALLOW，风险上界为 0.00165839，保守预计损失向上取整为 1 美分，立即后置条件 verified。未伪造 delayed correct outcome。

## API

全部控制接口位于 `/api/control`，使用已有 Bearer 身份认证和 Pydantic 模型。除 active 返回 PolicyDefinition，其余单对象返回 `{data: ...}`，列表返回该包装的列表。

| 方法 | 路径 | 用途 |
|---|---|---|
| GET / PUT | `/slo` | 读取 / 管理员创建业务目标快照 |
| POST | `/outcomes` | 财务或管理员记录延迟业务结果 |
| GET | `/calibrations` | 校准样本、分段上界和窗口 |
| POST | `/history` | 管理员生成隔离的演示历史 |
| GET | `/datasets` | 数据集来源与摘要 |
| POST | `/compile` | 校准、搜索、校验并持久化候选 |
| GET | `/candidates` | 候选列表，支持 calibration_id 过滤 |
| GET | `/candidates/{id}` | 候选、来源、约束与 diff 摘要 |
| GET | `/candidates/{id}/diff` | 当前/候选与历史转移统计 |
| GET | `/frontier` | 可行前沿，支持 calibration_id 过滤 |
| POST | `/candidates/{id}/release` | 管理员按 diff_digest + expected_revision 发布 |
| GET | `/active` | 唯一生效策略 |
| GET | `/revisions` | 发布者、理由与历史摘要 |
| GET | `/metrics` | 运行时预算、决策率、审核负载、结果及拒绝原因 |
| GET | `/events` | 持久化控制事件 |

缺少身份 401、权限不足 403、对象缺失 404、过期候选/冲突观察/不可行发布 409、请求格式错误 422。SLO 或观察改变会使旧候选过期；不自动重新解释审核过的 diff。原有 `/api/runs`、审批、effect 对账、检查点和 SSE 接口保留；OpenAPI 位于后端 `/docs`。

## 数据库与升级

新增 `business_slos`、`risk_calibrations`、`history_datasets`、`action_history`、`business_outcomes`、`policy_candidates`、`policy_revisions`、`policy_releases`、`control_state`。容量嵌入不可变 SLO，风险分段嵌入校准快照，回放嵌入候选；FK 关联 SLO、校准、数据集、基线 revision、候选与发布，支持从发布追到历史窗口/摘要及 optimizer configuration。

快照与结果观察由数据库触发器禁止 UPDATE/DELETE；候选与发布带摘要校验。唯一索引保证每个 Action 只有一个最终非 unknown 结果。`action_history` 保存 canonical action、决策、revision，并补充回执/后置条件。`agent_events.run_id` 允许空值以复用现有事件表记录控制事件。

Alembic 迁移 `7216a16dfb8d` 和 `ec11ec3b5c44` 在原 schema 上升级。执行 `safeops migrate` 完成应用迁移、官方 checkpoint schema 设置和显式 bootstrap；已有生效策略不会被重置。原有执行记录不会被冒充为带已知业务结果的训练数据；升级前旧策略审批无法沿用，应新建审核请求。

每日 expected_loss_consumption 表示最近 24 小时自动授权的保守风险承诺，并非实际已发生损失；未收到业务结果不会当作成功。rolling 指标使用生效 SLO 的滚动窗口。编译保证历史投影满足约束，不承诺未来流量漂移下绝不超预算；发现变化后需重新编译审核，运行时不会因队列繁忙自行放宽权限。

## 验证与开发

所有 Python 操作在 Conda 环境中执行。测试必须使用独立、名称以 `_test` 结尾的数据库；fixture 会清空该测试库的应用表及检查点。

```powershell
# 将 DATABASE_URL 临时指向测试库，先执行 safeops migrate
$env:TEST_DATABASE_URL='postgresql://user:password@127.0.0.1:5432/safeops_test'
$env:LLM_PROVIDER='offline'
python -m pytest -q --basetemp=.test-tmp-local
python -m ruff check src tests
python -m ruff format --check src tests
python -m mypy
python -m alembic check
Set-Location apps/web
npm run typecheck
npm run build
```

真实验证结果见 [VERIFICATION.md](docs/VERIFICATION.md)。Docker Compose 保留 PostgreSQL → migrate → seed → backend → frontend 的依赖关系；当前机器没有 Docker，未声称验证过容器启动。Compose 端口默认 8000，本机直接启动使用 8001。

阅读入口：[READING_GUIDE.md](docs/READING_GUIDE.md)。MIT License。

## 真实模型验收

2026-09-28 已使用用户 `.env` 中的 Key 对 `qwen3.8-flash` 完成真实端到端验收：付款查询、小额自动退款、强制人工审批、超额拒绝均通过。最终轮 8 次真实模型调用、1919 tokens，详见 [阿里云验收记录](docs/ALIYUN_VERIFICATION.md)。
