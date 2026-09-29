# 代码阅读顺序

1. `risk/models.py`：业务 SLO、审核容量、观察、风险与历史记录。
2. `risk/features.py`、`risk/calibration.py`：确定性金额桶与单侧 Wilson 上界、时间切分。
3. `risk/budget.py`、`policy/simulator.py`：风险、业务成本、容量和时延投影。
4. `risk/optimizer.py`、`risk/frontier.py`、`policy/compiler.py`：确定性搜索、可行前沿与硬约束校验。
5. `policy/control.py`、`policy/registry.py`：候选事务、过期检查、人工发布、唯一生效策略和并发锁。
6. `risk/datasets.py`、`risk/history.py`、`risk/outcomes.py`：合成数据隔离、批量历史关联和业务结果冲突语义。
7. `policy/engine.py`：单一运行时授权规则。
8. `domain/models.py`、`approvals/service.py`：canonical identity 与完整策略审批绑定。
9. `graph/nodes/operations.py`：执行时重新授权与即时验证。
10. `effects/service.py`、`effects/reconcile.py`：幂等账本、不确定结果和显式对账。
11. `tools/postconditions.py`：独立读取业务状态，不把 verified 当作 correct。
12. `graph/runtime.py`：PostgreSQL checkpointer、run/thread、恢复、dry-run。
13. `persistence/models.py`、`migrations/versions/`：外键、索引、不可变触发器和升级。
14. `api/control.py`、`apps/web/app/control.tsx`：身份验证和中文控制平面。
15. `policy/commands.py`：可复现生成、编译、回放、发布与演示。
16. `tests/unit/test_risk.py`、`tests/integration/test_control.py`：算法、约束、并发发布和结果闭环。
17. `tests/integration/test_workflows.py`、`test_effect_boundaries.py`：原有恢复、篡改、重复执行、不确定结果安全约束。

讨论重点：为什么零失败不等于零风险；如何阻止未来标签泄漏；可行前沿与经济最优有何区别；为什么回放不是因果证明；发布锁与运行锁如何共同保护授权；为什么回执和 delayed outcome 必须独立。
