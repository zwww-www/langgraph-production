// Translate presentation only; API values and diagnostic JSON remain unchanged.
const labels: Record<string, string> = {
  runs: "执行记录", approvals: "审批中心", effects: "待对账操作",
  queued: "排队中", running: "执行中", waiting_approval: "待审批", completed: "已完成",
  failed: "执行失败", in_doubt: "结果不明", pending: "待处理", released: "已释放",
  billing: "账务", account: "账户", subscription: "订阅", answer: "直接答复", escalate: "转人工处理",
  low: "低", medium: "中", high: "高", critical: "严重",
  admin: "管理员", operator: "运营人员", finance: "财务人员", security: "安全人员",
  supervisor: "任务分派", policy: "策略评估", human_gate: "人工审批", execute: "工具执行",
  verify: "回执验证", compose: "生成答复", runtime: "执行引擎", __start__: "初始化",
  lookup_invoice: "查询发票", get_payment_status: "查询支付状态", issue_refund: "发起退款",
  lookup_account: "查询账户", reset_credentials: "重置凭据", lock_account: "锁定账户",
  get_subscription: "查询订阅", change_plan: "变更套餐", cancel_subscription: "取消订阅",
  run_started: "执行开始", run_completed: "执行完成", run_suspended: "执行已暂停", run_failed: "执行失败",
  supervisor_started: "开始任务分派", supervisor_completed: "任务分派完成",
  subgraph_entered: "进入业务子图", plan_created: "操作计划已生成", plan_rejected: "操作计划被拒绝",
  risk_assessed: "风险评估完成", approval_requested: "已请求审批", approval_decided: "审批决定已保存",
  approval_received: "已读取审批结果", tool_started: "工具执行开始", tool_completed: "工具执行完成",
  tool_refused: "工具拒绝执行", receipt_verified: "回执验证通过", compose_started: "开始生成答复",
  effect_in_doubt: "操作结果不明，需对账",
  "invalid tool or arguments": "工具或参数无效", "read-only scoped lookup": "权限范围内的只读查询",
  "refund exceeds limit": "退款金额超过限额", "within automatic refund limit": "退款金额在自动审批限额内",
  "financial change requires review": "资金变更需要人工审批", "security change requires review": "安全变更需要人工审批",
  "subscription change requires review": "订阅变更需要人工审批",
  "no business action": "无需执行业务操作", "human judgment required": "需要人工判断",
  "Backend unavailable": "后端服务暂不可用，请稍后重试",
  "role cannot decide this approval": "当前角色无权审批此请求",
  "conflicting approval replay": "该审批已有不同的决定，请刷新后查看",
  "run is active in another worker": "该任务正在执行，请稍后重试",
  "recorded execution contradicts not_performed": "已有执行记录，不能确认未执行",
  "effect is not awaiting reconciliation": "该操作已不处于待对账状态，请刷新",
  "reconciliation requires admin": "仅管理员可以完成对账",
};

export function label(value?: string, fallback = "—"): string {
  return value ? labels[value] || value : fallback;
}

export function apiError(status: number, detail: unknown): string {
  if (typeof detail === "string" && labels[detail]) return labels[detail];
  if (typeof detail === "string" && /[\u3400-\u9fff]/.test(detail)) return detail;
  const messages: Record<number, string> = {
    401: "访问令牌无效或已过期，请重新连接", 403: "当前身份无权执行此操作",
    404: "未找到请求的记录，请刷新后重试", 409: "操作状态冲突，请刷新后重试",
    422: "提交内容不符合要求，请检查必填字段和数据格式",
    502: "后端服务暂不可用，请稍后重试",
  };
  return messages[status] || `请求失败（状态码 ${status}），请稍后重试`;
}

export function errorMessage(error: unknown): string {
  if (error instanceof SyntaxError) return "数据格式错误，请检查 JSON 是否有效";
  const message = error instanceof Error ? error.message : String(error);
  return /[\u3400-\u9fff]/.test(message) ? message : "连接失败，请检查网络和服务状态后重试";
}
