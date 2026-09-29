"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import ControlPlane from "./control";
import { apiError, errorMessage, label } from "./zh";

type Json = Record<string, unknown>;
type Run = {id: string; ticket_id: string; status: string; domain?: string; risk?: {level: string}; created_at: string; finished_at?: string; dry_run: boolean};
type Event = {id: number; event_type: string; node: string; timestamp: string; payload: Json};
type Approval = {token: string; run_id: string; request: {requested_role: string; action?: {tool: string; args: Json}; policy: {risk: {score: number; reasons: string[]}}}};
type Effect = {idempotency_key: string; run_id: string; tool: string; args: Json; status: string; execution_count: number; created_at: string};
type Checkpoint = {checkpoint_id: string; created_at: string; next: string[]; values: Json};

function Pretty({value}: {value: unknown}) { return <pre>{JSON.stringify(value, null, 2)}</pre>; }
function Badge({text}: {text: string}) { return <span className={`badge ${text}`}>{label(text)}</span>; }

export default function Dashboard() {
  const [token, setToken] = useState("");
  const [tab, setTab] = useState("control");
  const [runs, setRuns] = useState<Run[]>([]);
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [effects, setEffects] = useState<Effect[]>([]);
  const [selected, setSelected] = useState("");
  const [events, setEvents] = useState<Event[]>([]);
  const [state, setState] = useState<Json>({});
  const [checkpoints, setCheckpoints] = useState<Checkpoint[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("查一下 INV-10032");
  const [customer, setCustomer] = useState("CUS-001");
  const [note, setNote] = useState("");
  const [receipt, setReceipt] = useState("{}");
  const [connected, setConnected] = useState(false);
  const streamAbort = useRef<AbortController | null>(null);

  const api = useCallback(async <T,>(path: string, body?: unknown): Promise<T> => {
    const response = await fetch(`/api/${path}`, {method: body === undefined ? "GET" : "POST",
      headers: {"Authorization": `Bearer ${token}`, "Content-Type": "application/json"},
      body: body === undefined ? undefined : JSON.stringify(body)});
    const result = await response.json();
    if (!response.ok) throw new Error(apiError(response.status, result.detail));
    return result as T;
  }, [token]);

  const refresh = useCallback(async () => {
    const [r, a, e] = await Promise.all([api<Run[]>("runs"), api<Approval[]>("approvals"), api<Effect[]>("effects/in-doubt")]);
    setRuns(r); setApprovals(a); setEffects(e);
  }, [api]);

  const inspect = useCallback(async (id: string) => {
    const [s, c] = await Promise.all([api<Json>(`runs/${id}/state`), api<Checkpoint[]>(`runs/${id}/checkpoints`)]);
    setState(s); setCheckpoints(c);
  }, [api]);

  const action = async (fn: () => Promise<void>) => {
    setBusy(true); setError("");
    try { await fn(); await refresh(); } catch (e) {setError(errorMessage(e));} finally {setBusy(false);}
  };

  useEffect(() => {
    if (!connected) return;
    const tick = () => {refresh().catch(e => setError(errorMessage(e)));};
    tick(); const timer = setInterval(tick, 4000);
    return () => clearInterval(timer);
  }, [connected, refresh]);

  useEffect(() => {
    if (!selected || !connected) return;
    let disposed = false;
    const controller = new AbortController(); streamAbort.current?.abort(); streamAbort.current = controller;
    setEvents([]);
    inspect(selected).catch(e => setError(errorMessage(e)));
    // Fetch streaming supports the bearer header; credentials never enter URL/localStorage.
    (async () => {
      let cursor = 0;
      while (!disposed) {
        const response = await fetch(`/api/runs/${selected}/stream?after=${cursor}`, {
          headers: {Authorization: `Bearer ${token}`}, signal: controller.signal});
        if (!response.ok || !response.body) throw new Error("无法连接事件流，请检查服务后重新连接");
        const reader = response.body.getReader(); const decoder = new TextDecoder(); let buffer = "";
        while (true) {
          const {value, done} = await reader.read(); if (done) break;
          buffer += decoder.decode(value, {stream: true});
          let end;
          while ((end = buffer.indexOf("\n\n")) >= 0) {
            const frame = buffer.slice(0, end); buffer = buffer.slice(end + 2);
            if (!frame.includes("event: agent_event")) continue;
            const data = frame.split("\n").find(line => line.startsWith("data: "));
            if (data) {const event = JSON.parse(data.slice(6)) as Event; cursor = event.id;
              setEvents(previous => previous.some(e => e.id === event.id) ? previous : [...previous, event]);}
          }
        }
        if (!disposed) {await inspect(selected); await new Promise(resolve => setTimeout(resolve, 1500));}
      }
    })().catch(e => {if (!controller.signal.aborted) setError(errorMessage(e));});
    return () => {disposed = true; controller.abort();};
  }, [selected, connected, token, inspect]);

  async function submit() {
    const id = `T-${crypto.randomUUID()}`;
    await api("tickets", {id, customer_id: customer, text: message});
    const run = await api<{run_id: string}>("runs", {ticket_id: id});
    setSelected(run.run_id); setTab("runs");
  }

  return <div className="shell">
    <aside><div className="brand"><span className="mark">S</span> SafeOps<span className="tiny">智能体</span></div>
      <p className="eyebrow">运营工作台</p>
      {[ ["control", "风险控制平面", ""], ["runs", "执行记录", runs.length], ["approvals", "审批中心", approvals.length], ["effects", "待对账操作", effects.length] ].map(([id, label, count]) =>
        <button className={`nav ${tab === id ? "active" : ""}`} key={id} onClick={() => setTab(String(id))}>{label}<span>{count}</span></button>)}
      <div className="sidebar-foot"><span className="dot"/> PostgreSQL 持久化执行<br/><small>人工授权，操作可追溯。</small></div>
    </aside>
    <main><header><div><p className="eyebrow">控制台 / {tab === "control" ? "风险控制平面" : label(tab)}</p><h1>{tab === "control" ? "风险控制平面" : tab === "runs" ? "执行记录" : tab === "approvals" ? "审批中心" : "待对账操作"}</h1></div>
      <div className="login"><input aria-label="访问令牌" type="password" placeholder="访问令牌" value={token} onChange={e => {setToken(e.target.value); setConnected(false);}}/>
        <button onClick={() => action(async () => {await refresh(); setConnected(true);})} disabled={busy || !token}>连接</button></div></header>
      {error && <div role="alert" className="error">{error}</div>}
      {!connected && <section className="card"><h2>连接运营工作台</h2><p>请输入已配置的访问令牌。本地演示可使用： <code>demo-admin</code> 或 <code>demo-operator</code>。令牌仅保存在当前标签页。</p></section>}
      {connected && <>
      <div className="stats"><section><small>执行总数</small><strong>{runs.length}</strong></section><section><small>待审批</small><strong>{approvals.length}</strong></section><section><small>待对账</small><strong>{effects.length}</strong></section><section><small>已完成</small><strong>{runs.filter(r => r.status === "completed").length}</strong></section></div>
      {tab === "control" && <ControlPlane token={token}/> }
      {tab === "runs" && <>
        <section className="card"><h2>新建操作请求</h2><div className="request"><input aria-label="客户编号" value={customer} onChange={e => setCustomer(e.target.value)}/><input aria-label="操作请求" className="grow" value={message} onChange={e => setMessage(e.target.value)}/><button disabled={busy || !message} onClick={() => action(submit)}>开始执行 ↗</button></div><small>演示：客户 CUS-001 拥有发票 INV-10032 和账户 ACC-2041。可输入“退款 INV-10032 的 $45”。</small></section>
        <section className="card"><div className="section-title"><h2>最近执行记录</h2><button className="secondary" onClick={() => action(refresh)}>刷新</button></div><div className="table-scroll"><table><thead><tr><th>执行记录 / 工单</th><th>业务领域</th><th>状态</th><th>风险</th><th>开始时间</th><th>耗时</th></tr></thead><tbody>{runs.map(r => <tr key={r.id} className={selected === r.id ? "selected" : ""}><td><button className="link" onClick={() => setSelected(r.id)}>{r.id.slice(0, 12)} {r.dry_run && "[模拟执行]"}</button><small>{r.ticket_id}</small></td><td>{label(r.domain)}</td><td><Badge text={r.status}/></td><td>{label(r.risk?.level)}</td><td>{new Date(r.created_at).toLocaleString("zh-CN")}</td><td>{r.finished_at ? `${((+new Date(r.finished_at) - +new Date(r.created_at))/1000).toFixed(1)} 秒` : "—"}</td></tr>)}</tbody></table></div>{!runs.length && <p className="empty">暂无执行记录，请在上方提交请求。</p>}</section>
        {selected && <section className="card"><div className="section-title"><div><p className="eyebrow">执行详情</p><h2>{selected.slice(0, 16)}</h2></div><button disabled={busy} onClick={() => action(async () => {await api(`runs/${selected}/resume`, {});})}>从数据库恢复执行</button></div>
          <div className="detail-grid"><div><h3>执行时间线</h3><ol className="timeline">{events.map(e => <li key={e.id}><b>{label(e.node)}</b><span>{label(e.event_type)}</span><small>{new Date(e.timestamp).toLocaleTimeString("zh-CN")} {e.payload.duration_ms !== undefined && `· ${e.payload.duration_ms} 毫秒`}</small><details><summary>事件数据</summary><Pretty value={e.payload}/></details></li>)}</ol></div><div><h3>状态 / 调试数据</h3><small>以下为原始接口数据，字段名和标识符保留原文，便于核查。</small><Pretty value={state}/><h3>检查点历史</h3>{checkpoints.map(c => <details key={c.checkpoint_id}><summary>{new Date(c.created_at).toLocaleString("zh-CN")} · {c.next.map(node => label(node)).join(" → ") || "已结束"}</summary><button disabled={busy || !Object.keys(c.values).length} onClick={() => action(async () => {const r = await api<{run_id:string}>(`runs/${selected}/replay`, {checkpoint_id:c.checkpoint_id});setSelected(r.run_id);})}>创建模拟执行分支</button><Pretty value={c.values}/></details>)}</div></div></section>}
      </>}
      {tab === "approvals" && <><section className="card"><label>审批意见<input value={note} onChange={e => setNote(e.target.value)} placeholder="填写已核查的证据及审批理由"/></label></section>{approvals.map(a => <section className="card" key={a.token}><div className="section-title"><h2>{label(a.request.action?.tool, "转人工处理")}</h2><Badge text="waiting_approval"/></div><p>所需角色： <b>{label(a.request.requested_role)}</b> · 风险评分： <b>{a.request.policy.risk.score}</b></p><p>{a.request.policy.risk.reasons.map(reason => label(reason)).join(" · ")}</p><Pretty value={a.request.action?.args || {}}/><div className="actions"><button disabled={busy} onClick={() => action(async () => {await api(`approvals/${a.token}/approve`, {note});})}>批准</button><button className="danger" disabled={busy} onClick={() => action(async () => {await api(`approvals/${a.token}/reject`, {note});})}>拒绝</button><button className="secondary" onClick={() => {setSelected(a.run_id);setTab("runs");}}>查看执行详情</button></div></section>)}{!approvals.length && <section className="card empty">暂无待审批请求。</section>}</>}
      {tab === "effects" && <><section className="card"><h2>对账证据</h2><p>请先核查下游系统，再确认执行结果。超时不代表执行失败。</p><label>证据说明<input value={note} onChange={e => setNote(e.target.value)} placeholder="填写下游操作编号及核查结果"/></label><label>已核实的回执（JSON，确认已执行时必填）<textarea value={receipt} onChange={e => setReceipt(e.target.value)}/></label></section>{effects.map(e => <section className="card" key={e.idempotency_key}><h2>{label(e.tool)} <Badge text={e.status}/></h2><p className="mono">{e.idempotency_key}</p><p>{new Date(e.created_at).toLocaleString("zh-CN")} · 已记录执行次数： {e.execution_count}</p><Pretty value={e.args}/><div className="actions">{["performed", "not_performed"].map(outcome => <button key={outcome} disabled={busy || note.length < 5 || (outcome === "not_performed" && e.execution_count > 0)} onClick={() => action(async () => {await api(`effects/${encodeURIComponent(e.idempotency_key)}/reconcile`, {outcome, evidence:note, result:outcome === "performed" ? JSON.parse(receipt) : null});})}>{outcome === "performed" ? "确认已执行" : "确认未执行"}</button>)}</div></section>)}{!effects.length && <section className="card empty">暂无结果不明的操作。</section>}</>}
      </>}
      <footer>SafeOps 智能体 <span>确定性授权 · 持久化工作流 · 显式记录不确定结果</span></footer>
    </main>
  </div>;
}

