# 阿里云接入验证 — 2026-09-20

- Provider：`aliyun`，通过百炼 OpenAI 兼容协议调用。
- Endpoint：`https://dashscope.aliyuncs.com/compatible-mode/v1`（用户提供）。
- 模型：`qwen3.8-flash`。
- 凭据：只从本地 `.env` 读取，本文和代码中不保存 Key。
- 配置缺失、鉴权失败、超时、重定向、无效或不完整输出均报错，不回退离线模型。

## 实际调用

演示请求：查询 INV-10032 的金额和付款状态，不修改数据。

运行 ID：`b203f48777714082ab1d83dd93f25304`，状态 `completed`。

| 节点 | 服务返回模型 | 输入 token | 输出 token |
| --- | --- | ---: | ---: |
| Supervisor | qwen3.8-flash | 119 | 50 |
| Billing 规划 | qwen3.8-flash | 297 | 23 |

共 489 token。路由为 billing，工具为 lookup_invoice，回执确认演示发票已付款、金额 9000 美分、已退款 0。业务工具仍使用项目的合成数据；本次没有真实资金操作。

87 项自动化测试通过，Ruff、格式检查和 mypy 通过。自动化测试使用离线模型或 MockTransport，真实调用由独立验收执行。此单次成功不代表所有模型输出都可靠；工具参数、引用、权限、策略与审批校验仍然执行。

控制台入口仍为 http://127.0.0.1:3000，后端使用 8001。本地 `.env` 已切换为 aliyun，后续新请求将产生真实模型调用。`safeops eval` 仍测量离线规则提供者。

参考：[阿里云模型说明](https://help.aliyun.com/zh/model-studio/qwen3-8-flash)、[思考模式参数](https://help.aliyun.com/zh/model-studio/deep-thinking)。
