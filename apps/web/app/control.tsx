"use client";
import {useCallback, useEffect, useState} from "react";
import {apiError, errorMessage} from "./zh";

type SLO = {max_expected_loss_per_day_cents:number; max_expected_loss_per_window_cents:number; target_auto_resolution_rate:number; p95_resolution_seconds:number; capacities:{role:string; reviewers:number; service_rate_per_hour:number; max_queue_depth:number; sla_seconds:number}[]};
type Projection = {auto_rate:number; review_rate:number; deny_rate:number; expected_loss_per_day_cents:number; upper_bound_loss_per_day_cents:number; review_capacity_utilization:number; review_load_per_hour:number; resolution_p95_seconds:number; outcome_coverage:number};
type Candidate = {id:string; base_revision:string; diff_digest:string; source:string; feasible:boolean; frontier:boolean; result:{errors:string[]; replay:{current:Projection; candidate:Projection; transitions:Record<string,number>; historical_transitions:Record<string,number>}}};
type Active = {revision:string; auto_limits:number[]; review_limits:number[]};
type Doc<T> = {data:T};
const percent=(n:number)=>`${(n*100).toFixed(2)}%`;
const money=(n:number)=>`$${(n/100).toFixed(2)}`;

const reasons:Record<string,string>={"daily risk budget exceeded":"每日风险预算超限","rolling risk budget exceeded":"滚动窗口预算超限","review capacity exceeded":"人工审核产能不足","review queue depth exceeded":"审核队列超限","review queue SLA exceeded":"审核时延超限","resolution SLA exceeded":"处理时延超限","automation target not met":"未达到自动化目标","amount monotonicity violation":"金额权限不满足单调约束","risk monotonicity violation":"风险权限不满足单调约束"};
const transitionLabels:Record<string,string>={"AUTO->REVIEW":"自动 → 人工","AUTO->DENY":"自动 → 拒绝","REVIEW->AUTO":"人工 → 自动","REVIEW->DENY":"人工 → 拒绝","DENY->AUTO":"拒绝 → 自动","DENY->REVIEW":"拒绝 → 人工",unchanged:"保持不变"};
function Changes({values}:{values:Record<string,number>}){return <table><thead><tr><th>决策变化</th><th>历史请求数量</th></tr></thead><tbody>{Object.entries(values).map(([key,count])=><tr key={key}><td>{transitionLabels[key]||key}</td><td>{count.toLocaleString()}</td></tr>)}</tbody></table>;}

export default function ControlPlane({token}:{token:string}) {
  const [active,setActive]=useState<Active>();
  const [slo,setSlo]=useState<SLO>();
  const [draft,setDraft]=useState("");
  const [candidates,setCandidates]=useState<Candidate[]>([]);
  const [selected,setSelected]=useState<Candidate>();
  const [datasets,setDatasets]=useState<{id:string; count:number}[]>([]);
  const [dataset,setDataset]=useState("");
  const [metrics,setMetrics]=useState<Record<string,number>>({});
  const [status,setStatus]=useState("");
  const [busy,setBusy]=useState(false);
  const [note,setNote]=useState("");
  const api=useCallback(async <T,>(path:string,body?:unknown,method="POST"):Promise<T>=>{
    const response=await fetch(`/api/control/${path}`,{method:body===undefined?"GET":method,headers:{Authorization:`Bearer ${token}`,"Content-Type":"application/json"},body:body===undefined?undefined:JSON.stringify(body)});
    const result=await response.json(); if(!response.ok) throw new Error(apiError(response.status,result.detail)); return result;
  },[token]);
  const refresh=useCallback(async()=>{
    const [a,s,c,d,m]=await Promise.all([api<Active>("active"),api<Doc<{definition:SLO}>>("slo"),api<Doc<Candidate>[]>("candidates"),api<Doc<{id:string;count:number}>[]>("datasets"),api<Doc<Record<string,number>>>("metrics")]);
    setActive(a);setSlo(s.data.definition);setDraft(JSON.stringify(s.data.definition,null,2));setCandidates(c.map(x=>x.data));setDatasets(d.map(x=>x.data));setMetrics(m.data);
  },[api]);
  useEffect(()=>{refresh().catch(e=>setStatus(errorMessage(e)));},[refresh]);
  async function act(fn:()=>Promise<void>){setBusy(true);setStatus("");try{await fn();await refresh();}catch(e){setStatus(errorMessage(e));}finally{setBusy(false);}}
  if(!slo||!active)return <section className="card" role="status">{status||"正在加载控制平面…"}</section>;
  return <>
    <section className="card"><h2>业务目标 → 策略编译 → 确定性授权</h2><p>根据历史退款及延迟业务结果计算风险。候选策略只有经过管理员发布，才会影响运行时。</p><p>当前生效策略：<code>{active?.revision}</code></p><p>低 / 中 / 高风险自动额度：{active?.auto_limits.map(money).join(" / ")} · 人工额度：{active?.review_limits.map(money).join(" / ")}</p><small>合成数据用于演示；运行时指标只统计真实运行记录。金额单位为美分。</small></section>
    {status&&<div className="error" role="status">{status}</div>}
    <div className="stats"><section><small>每日风险预算</small><strong>{money(slo?.max_expected_loss_per_day_cents||0)}</strong></section><section><small>近 24 小时预算占用</small><strong>{percent(metrics.risk_budget_utilization||0)}</strong></section><section><small>待审核队列</small><strong>{metrics.review_queue_depth||0}</strong></section><section><small>业务结果覆盖率</small><strong>{percent(metrics.business_outcome_coverage||0)}</strong></section></div>
    <section className="card"><h2>业务 SLO 与审核容量</h2><p>自动处理目标：{percent(slo?.target_auto_resolution_rate||0)} · 处理时延上限：{slo?.p95_resolution_seconds} 秒</p><table><thead><tr><th>审核角色</th><th>人数</th><th>每小时容量</th><th>队列上限</th><th>队列 SLA</th></tr></thead><tbody>{slo?.capacities.map(c=><tr key={c.role}><td>{({finance:"财务",security:"安全",operator:"运营",admin:"管理员"} as Record<string,string>)[c.role]||c.role}</td><td>{c.reviewers}</td><td>{c.reviewers*c.service_rate_per_hour}</td><td>{c.max_queue_depth}</td><td>{c.sla_seconds} 秒</td></tr>)}</tbody></table><details><summary>编辑完整业务目标</summary><textarea aria-label="业务 SLO JSON" style={{width:"100%",minHeight:300}} value={draft} onChange={e=>setDraft(e.target.value)}/><button disabled={busy} onClick={()=>act(async()=>{await api("slo",JSON.parse(draft),"PUT");setSelected(undefined);setStatus("业务目标已保存，需要重新编译策略。");})}>保存业务目标</button></details></section>
    <section className="card"><h2>历史数据与风险校准</h2><div className="actions"><select aria-label="历史数据来源" value={dataset} onChange={e=>setDataset(e.target.value)}><option value="">运行时历史</option>{datasets.map(d=><option key={d.id} value={d.id}>合成历史 · {d.count.toLocaleString()} 条 · {d.id.slice(0,8)}</option>)}</select><button disabled={busy} onClick={()=>act(async()=>{const r=await api<Doc<{id:string}>>("history",{count:100000,seed:42});setDataset(r.data.id);setStatus("10 万条历史数据已准备完成。");})}>生成 10 万条演示历史</button><button disabled={busy} onClick={()=>act(async()=>{const r=await api<Doc<{candidate_count:number;feasible_count:number;recommended:string|null}>>("compile",{source:dataset?"synthetic":"runtime",dataset_id:dataset||null});setStatus(`编译完成：${r.data.candidate_count} 个候选，${r.data.feasible_count} 个可行。${r.data.recommended?"可查看推荐候选并审核发布。":"当前目标无可行解。"}`);if(r.data.recommended)setSelected((await api<Doc<Candidate>>(`candidates/${r.data.recommended}`)).data);})}>{busy?"计算中…":"校准并编译候选策略"}</button></div></section>
    <section className="card"><h2>候选策略与可行前沿</h2><div className="table-scroll" style={{maxHeight:440,overflow:"auto"}}><table><thead><tr><th>候选 / 状态</th><th>自动率</th><th>审核率</th><th>每日损失上界</th><th>审核容量占用</th><th>约束检查</th></tr></thead><tbody>{candidates.map(c=><tr key={c.id} className={selected?.id===c.id?"selected":""}><td><button className="link" onClick={()=>act(async()=>setSelected((await api<Doc<Candidate>>(`candidates/${c.id}`)).data))}>{c.id.slice(0,10)}</button><small>{c.frontier?"可行前沿":c.feasible?"可行":"不可行"} · {c.source==="synthetic"?"合成历史":"运行时历史"}</small></td><td>{percent(c.result.replay.candidate.auto_rate)}</td><td>{percent(c.result.replay.candidate.review_rate)}</td><td>{money(c.result.replay.candidate.upper_bound_loss_per_day_cents)}</td><td>{percent(c.result.replay.candidate.review_capacity_utilization)}</td><td>{c.feasible?"通过":<details><summary>{c.result.errors.length} 项未满足</summary><pre>{c.result.errors.map(e=>reasons[e]||e).join("\n")}</pre></details>}</td></tr>)}</tbody></table></div>{!candidates.length&&<p>暂无候选，请先编译。</p>}</section>
    {selected&&<section className="card"><h2>发布前差异审查</h2>{selected.base_revision!==active.revision&&<p>此回放基于编译时的历史策略，已不是当前生效策略；发布已禁用。如需再次变更，请重新编译。</p>}<p>候选 {selected.id.slice(0,16)} · 历史结果覆盖率 {percent(selected.result.replay.candidate.outcome_coverage)}</p><table><thead><tr><th>指标</th><th>编译时生效策略</th><th>候选策略</th></tr></thead><tbody>{([ ["自动处理率","auto_rate",percent],["人工审核率","review_rate",percent],["拒绝率","deny_rate",percent],["每日预计损失","expected_loss_per_day_cents",money],["每日损失上界","upper_bound_loss_per_day_cents",money],["审核容量占用","review_capacity_utilization",percent],["时延保守估计（秒）","resolution_p95_seconds",(x:number)=>x.toFixed(1)]] as [string,keyof Projection,(x:number)=>string][]).map(([name,key,fmt])=><tr key={key}><td>{name}</td><td>{fmt(selected.result.replay.current[key])}</td><td>{fmt(selected.result.replay.candidate[key])}</td></tr>)}</tbody></table><h3>相对编译基线的决策变化</h3><Changes values={selected.result.replay.transitions}/><details><summary>相对历史已记录决策的变化</summary><Changes values={selected.result.replay.historical_transitions}/></details><p>回放估算不等于因果验证，也不证明拒绝操作的未知结果。审核 diff 后，由管理员明确发布。</p><label>发布理由<input value={note} onChange={e=>setNote(e.target.value)} placeholder="填写已核查的风险、容量和业务依据"/></label><button disabled={busy||!selected.feasible||note.length<5||selected.base_revision!==active?.revision} onClick={()=>act(async()=>{await api(`candidates/${selected.id}/release`,{note,diff_digest:selected.diff_digest,expected_revision:active?.revision});setSelected(undefined);setStatus("策略已发布，旧审批不能继续授权执行。");})}>发布已审核的候选策略</button></section>}
  </>;
}
