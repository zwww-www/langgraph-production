# 阿里云真实 API 验收 — 2026-09-28

模型：`qwen3.8-flash`。接口：`https://dashscope.aliyuncs.com/compatible-mode/v1`。Key 从用户本地 `.env` 读取，未写入脚本或报告。

通过实际 HTTP API 提交请求，后台 worker 调用真实阿里云模型，使用当前已发布策略与 PostgreSQL 检查点、执行账本。未使用 MockTransport、离线 provider 或录制回放。

| 场景 | 实际工具 | 策略 | 外部操作记录数 | 结果 |
|---|---|---|---:|---|
| invoice_lookup | get_payment_status | ALLOW | 0 | 通过 |
| small_refund | issue_refund | ALLOW | 1 | 通过 |
| mandatory_review | reset_credentials | REQUIRE_APPROVAL | 0 | 通过 |
| hard_deny | issue_refund | DENY | 0 | 通过 |

最终一轮：**4/4 通过，8 次真实模型调用，1919 tokens**。每个请求包括 supervisor 和 domain plan 两次调用，模型名称和 usage 均来自服务响应并记录在持久化事件中。

小额退款的执行账本为 completed、execution_count=1，独立后置条件 verified。敏感凭据重置在人工审批处中断，验收后明确拒绝，没有执行重置。硬拒绝退款没有业务操作记录。只读查询的后置条件为 unavailable，因为没有状态变更需要验证。

第一轮验收脚本错误地把“付款状态查询”的预期工具写成 lookup_invoice；真实模型正确选择 get_payment_status。修正测试断言后重新运行四个场景，全部通过。两轮共 16 次真实调用、3828 tokens；两次小额退款共修改本地演示退款金额 $4，无真实资金交易。

业务工具绑定仍是项目的本地演示数据库；真实 API 指模型调用，不是实际支付网关。生产业务状态和密钥未提交到报告。

后端 8001 已使用 aliyun provider 启动，后续新请求会调用真实模型。

## 本轮运行 ID

- invoice_lookup: `efe7a47098314d3c927c77d730f8eeac`
- small_refund: `6dbc22fa5eca4ec8a591aa1bc0573324`
- mandatory_review: `69fdf6f1f6994913b335f97f9ba1315f`
- hard_deny: `c306b32da6754435858785924b341dac`

完整脱敏记录：[live-api-verification.json](live-api-verification.json)。

复现（项目根目录，已激活 Conda 且后端已加载真实配置）：

```powershell
python scripts/verify_live_api.py
```

此脚本会产生少量模型费用，并在当前项目的演示数据中执行一笔 $2 退款；建议保留现有本地工具绑定。
