# 最终验证记录 — 2026-09-26

本记录针对 Risk-Budgeted Control Plane 重构后的真实代码，未使用 LLM 生成测试结果。

| 检查 | 实际结果 |
|---|---|
| 全套 pytest（独立 PostgreSQL 测试库） | **120 passed，0 failed，65.45 秒** |
| ruff check src tests | All checks passed |
| ruff format --check src tests | 83 files already formatted |
| mypy | Success，74 source files |
| 前端 TypeScript | 通过 |
| Next.js production build | 通过 |
| 原有开发库升级 | 通过，保留旧运行记录 |
| 全新空数据库迁移与 bootstrap | 通过，生效策略 bootstrap-conservative；临时库验证后删除 |
| Alembic metadata drift | No new upgrade operations detected |
| 100000 条 PostgreSQL 历史 → 编译 → 发布 | 通过，167 候选、60 可行 |
| 发布后正常 LangGraph 请求 | $2 退款 ALLOW，postcondition verified，无人工中断 |
| HTTP /ready 与中文前端代理 | 已运行检查 |
| 中文控制平面浏览器验证 | 生效 revision、$500 预算、审核容量、候选及约束数据显示正常 |
| Docker Compose | 已检查依赖与配置一致性；本机无 Docker，未进行容器启动验证 |

测试覆盖 Wilson 的零/小/大样本、未知和未来标签遮蔽、金额边界、确定性搜索、无解/多解、预算/容量/SLA、硬拒绝和单调性、无 LLM 回放、不同决策转移、数据库不可变策略、候选过期、发布身份与幂等、执行共享锁与发布排他锁、延迟结果冲突及损失聚合。原有 canonical args 篡改、跨客户越权、审批绑定、恢复、崩溃注入、重复执行、in_doubt 和双向对账测试保留并通过。

最终全量测试使用新生成的工作区临时目录；先前一次运行因 Windows 对已有 pytest 临时目录的访问权限报错，120 项中的 119 项当时已通过，换新目录后全量通过。没有删除仍成立的安全测试。

演示完整输出：[risk-backtest.json](risk-backtest.json)。CLI 报告中的 185.718 秒是该次生成、写库、编译、发布耗时，不包括后续运行时示例。已知结果覆盖率 95.034%；训练截止前已知 50660 条；验证集 40000 条。

2026-09-26 的控制平面演示使用 offline 规划：本次启动时进程/用户环境没有可用 LLM Key，未发起或声称发起真实模型调用。阿里云 provider 与用户指定模型/endpoint 配置保留；Key 应配置在启动服务的环境中。控制平面的校准、优化、回放本来就不调用 LLM。

合成结果只证明当前数据生成器和配置下的机制，不构成真实支付业务的收益保证。每日风险预算是编译时的历史投影约束，运行时指标用于观测风险承诺及真实损失。

## 2026-09-28 真实模型补充验收

用户配置 `.env` Key 后，已启动 aliyun / qwen3.8-flash 并完成 4/4 场景验收；最终轮 8 次调用、1919 tokens。账本验证小额退款单次执行，人工审批和硬拒绝场景零副作用。当前后端已使用真实模型，详情见 [ALIYUN_VERIFICATION.md](ALIYUN_VERIFICATION.md)。
