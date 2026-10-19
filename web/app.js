/* Local records only. Text is always escaped before HTML rendering. */
if(new URLSearchParams(location.search).has("embedded"))document.documentElement.classList.add("embedded");
let token, items = [], selected=location.hash.startsWith("#management")?"":location.hash.slice(1), view = location.hash==="#management"?"management":"all", tab = "overview", action;
let catalog={projects:[],collections:[]}, intake=[], catalogEdit, draftNodes=[];
let nodeSelected=0, customTemplate=false;
let capture={enabled:false,chats:[],inputs:[]};
let management={jobs:[],candidates:[],reports:[]};
const standardNodes=["需求","方案","开发","测试","待发布","观察","完成"].map(name=>({name,human:["观察","完成"].includes(name),evidence:["待发布","观察","完成"].includes(name),approval:["观察","完成"].includes(name)}));
const titleOf=i=>i.display_title || zh(i.title);
const stageOf=i=>`${i.phase}节点 · ${i.status==="阻塞"?"任务阻塞":pending(i)?"请求审批":statusOf(i)}`;
const expanded = new Set();
const $ = (s) => document.querySelector(s);
const filters=document.createElement("div");
filters.className="filters";
filters.innerHTML='<label class="search"><i data-lucide="search"></i><input id="search" placeholder="搜索需求、编号或项目" aria-label="搜索需求"></label><select id="project" aria-label="项目"><option value="">全部项目</option></select><select id="phase" aria-label="阶段"><option value="">全部阶段</option></select>';
document.querySelector(".sidebar nav").after(filters);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const display = s => esc(zh(s));
const icons = () => lucide.createIcons();
const badge = (s) => `<span class="badge ${s === "失败" || s === "阻塞" ? "red" : s === "通过" || s === "已完成" || s === "批准" ? "green" : s === "待确认" || s === "待决策" ? "amber" : s === "进行中" ? "blue" : ""}">${esc(s)}</span>`;
const date = s => s ? new Date(s).toLocaleString("zh-CN", {month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit"}) : "";
const scopes = {develop:"继续本地开发与隔离测试",merge:"合并已验证的本地代码",preflight:"发布预检（只读核验，不部署）"};
const threadUrl = thread => /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(thread?.id || "") ? `codex://threads/${encodeURIComponent(thread.id)}` : "";
const pending = i => i.coordination?.scope ? Number(i.coordination.decision_state==="待确认") : i.decisions.filter(d=>d.state==="待确认").length;
const statusOf = i => {
  const c=i.coordination;
  if(!c?.scope)return i.status;
  if(c.stale)return "待重新确认";
  if(c.decision_state==="待确认")return "待决策";
  return c.jobs.at(-1)?.state || c.decision_state;
};
function toast(message, error=false) {
  $("#toast").textContent = message; $("#toast").className = error ? "error" : "";
  $("#toast").style.display = "block"; clearTimeout(toast.timer);
  toast.timer = setTimeout(() => $("#toast").style.display = "none", 5000);
}
async function api(path, data) {
  const response = await fetch(path, {method: data ? "POST" : "GET",
    headers: {"Authorization":"Bearer " + token, "Content-Type":"application/json"},
    ...(data ? {body:JSON.stringify(data)} : {})});
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "请求失败");
  return result;
}
async function load() {
  [items,catalog,intake,capture,management] = await Promise.all([api("/api/items"),api("/api/catalog"),api("/api/intake"),api("/api/capture"),api("/api/management")]);
  if (!items.some(i => i.id === selected)) selected = items[0]?.id;
  const project = $("#project").value;
  $("#project").innerHTML = '<option value="">全部项目</option>' + catalog.projects.map(p => `<option value="${esc(p.id)}">${esc(p.name)}</option>`).join("");
  $("#project").value = project;
  refreshPhases();
  const active=items.find(i=>i.id===selected);
  if(active?.project_id)expanded.add(active.project_id);
  render();
}
function visibleItems(){
  const query=$("#search").value.toLowerCase();
  return items.filter(i=>(view==="archived"?i.archived:!i.archived)&&
    (!query||`${titleOf(i)} ${i.title} ${zh(i.title)} ${i.project_label||i.project} ${i.id} ${i.coordination?.thread?.title||""}`.toLowerCase().includes(query))&&
    (!$("#project").value||i.project_id===$("#project").value)&&(!$("#phase").value||i.phase===$("#phase").value)&&
    (view!=="blocked"||i.status==="阻塞")&&(view!=="decisions"||pending(i)));
}
function render() {
  renderTree();
  const decisions = items.reduce((n,i) => n + pending(i),0);
  $("#all-count").textContent = items.length;
  $("#decision-count").textContent = decisions;
  $("#blocked-count").textContent = items.filter(i=>i.status==="阻塞").length;
  $("#stats").innerHTML = [
    ["交付事项", items.length, "本机记录"], ["进行中",items.filter(i=>i.status==="进行中").length,"当前执行"],
    ["待人工决策",decisions,"版本绑定"], ["阻塞事项",items.filter(i=>i.status==="阻塞").length,"需处理"]
  ].map(([l,n,d])=>`<div class="stat"><div class="stat-label">${l}</div><div class="stat-value">${n}<small>${d}</small></div></div>`).join("");
  const filtered=visibleItems();
  if(view!=="intake"&&!filtered.some(i=>i.id===selected))selected=filtered[0]?.id;
  $("#intake-list").hidden=!["intake","capture"].includes(view);
  $("#management").hidden=view!=="management";
  $("#detail").hidden=["intake","capture","management"].includes(view);
  if(view==="management")renderManagement();
  if(view==="capture"){
    $("#intake-list").innerHTML=`<section class="section"><h2>全局聊天采集</h2><p>${esc(capture.health)} · ${esc(capture.adapter||"")}</p><p class="muted">${esc(capture.classification||"")} · ${esc(capture.history||"")}</p></section><h3>新输入 · 待需求分析</h3>${capture.inputs.map(e=>`<article class="item"><strong>${esc(capture.chats.find(c=>c.id===e.thread_id)?.title||"未命名会话")}</strong> ${badge(e.state)}<p>${esc(e.excerpt)}</p><a href="${esc(threadUrl({id:e.thread_id}))}">打开原会话</a></article>`).join("")||'<p class="muted">暂无启用后的新输入</p>'}<h3>会话目录 · ${capture.chats.length}</h3>${capture.chats.filter(c=>!c.archived).map(c=>`<article class="item"><a href="${esc(threadUrl(c))}">${esc(c.title)}</a><p class="muted">${esc(catalog.projects.find(p=>p.id===c.project_id)?.name||"未明确归属项目")}</p></article>`).join("")}`;
  }
  if(view==="intake"){
    $("#intake-list").innerHTML=intake.map(e=>`<article class="item"><div class="item-top"><strong>${esc({create:"新增需求",update:"修改需求",archive:"归档需求",ignore:"忽略闲聊",clarify:"待澄清"}[e.input.intent])}</strong>${badge(e.state)}</div><p>${esc(e.input.reason)}</p><div class="code">${esc(e.input.thread_id)}</div><pre class="change-patch">${esc(JSON.stringify(e.input.patch,null,2))}</pre>${e.state==="待确认"?`<button data-review="${e.id}" data-decision="accept">采纳变更</button>`:""}${["待确认","待澄清"].includes(e.state)?`<button data-review="${e.id}" data-decision="reject">拒绝</button>`:""}</article>`).join("")||'<div class="empty">暂无会话变更</div>';
    $$("[data-review]").forEach(b=>b.onclick=async()=>{try{await api("/api/intake/"+b.dataset.review,{decision:b.dataset.decision});await load();toast("变更已处理");}catch(e){toast(e.message,true);}});
  }
  renderDetail(); icons();
}
const $$ = s => [...document.querySelectorAll(s)];
function renderDetail() {
  const i = items.find(i=>i.id===selected);
  if (!i) {$("#detail").innerHTML=`<div class="empty">${items.length?"没有匹配的需求":"暂无需求"}</div>`;return;}
  const readonly = i.origin !== "local";
  const c=i.coordination || {revision:0,jobs:[]};
  $("#detail").innerHTML = `<div class="detail-id">${esc(i.id)} · ${esc(i.project_label||i.project)} ${readonly ? "· 源状态 "+display(i.source_state) : "· 记录版本 "+i.version}</div>
    <h2 class="detail-title">${esc(titleOf(i))}</h2><div class="current-stage">${badge(stageOf(i))} <span class="muted">${readonly ? "源快照 "+esc(i.source_health) : "本机需求"} · ${date(i.updated)}</span></div>${i.intake_source?`<div class="code">来源会话 ${esc(i.intake_source.thread_id)} · 待确认关联</div>`:""}
    <div class="stepper">${i.workflow.map(n=>`<span class="step ${n.name===i.phase?"current":""}">${esc(n.name)}</span>`).join("")}</div>
    ${i.blocker_analysis?`<section class="blocker-review"><h3>当前为什么不能继续</h3><p>${esc(i.blocker_analysis.summary)}</p>${i.blocker_analysis.topics.map(t=>`<div class="blocker-topic"><strong>${esc(t.title)}</strong><p>${esc(t.impact)}</p><p><b>建议解决：</b>${esc(t.solution)}</p></div>`).join("")}<div class="approval-range"><strong>建议你审批的内容</strong><p>继续本地排查、修复上述问题并完成隔离回归；不是批准发布。</p><p>${esc(i.blocker_analysis.approval.exclusions)}</p><button data-repair-proposal><i data-lucide="list-checks"></i>审阅修复范围</button></div><p class="muted">${esc(i.blocker_analysis.basis)}</p><details><summary>技术证据与原始失败项</summary><div class="notice">${display(i.blocker)}</div>${(i.failed_tests||[]).map(t=>`<div>${display(t)}</div>`).join("")}</details></section>`:i.blocker?`<div class="notice">${esc(i.blocker)}</div>`:""}
    ${i.failure_evidence?.length?`<details class="failure-sources"><summary>失败证据位置</summary>${i.failure_evidence.map(e=>`<div>${display(e.name)}<div class="code">${esc(e.path)}</div></div>`).join("")}</details>`:""}
    <section class="coordination">
      <div class="evidence-head"><h3>推进工作区</h3><button data-coordinate="bind" title="关联或更换 Codex 对话"><i data-lucide="link"></i>${c.thread?"更换对话":"关联对话"}</button></div>
      ${c.thread?`<div class="chat-link"><i data-lucide="messages-square"></i><strong>${esc(c.thread.title)}</strong>${threadUrl(c.thread)?`<a href="${esc(threadUrl(c.thread))}" title="打开关联对话">打开对话</a>`:'<span class="muted">对话编号无效，请重新关联</span>'}</div><div class="code">${esc(c.thread.id)}</div>`:`<p class="muted">尚未关联对话</p>`}
      ${c.progress?`<p class="progress">${esc(c.progress)}</p><div class="muted">核验来源：${esc(c.progress_evidence)||"人工记录，未独立认证"}</div>`:""}
      ${c.stale?'<div class="notice">源版本或证据已变化；历史进展仅供溯源，当前审批不可使用，需重新确认。</div>':""}
      ${c.scope?`<div class="next-step"><div class="evidence-head"><strong>下一步：${esc(scopes[c.scope])}</strong>${badge(c.decision_state)}</div><p>${esc(c.next_step)}</p><div class="code">绑定候选 ${esc(i.candidate)||"未绑定"}</div>${c.decision_note?`<p class="muted">人工意见：${esc(c.decision_note)}</p>`:""}</div>`:""}
      <div class="detail-actions"><button data-coordinate="propose"><i data-lucide="list-checks"></i>确认下一步</button>${c.scope&&c.decision_state==="待确认"?`<button class="primary" data-coordinate="approve" ${!c.thread?"disabled":""}><i data-lucide="check"></i>批准并推进</button><button data-coordinate="reject"><i data-lucide="x"></i>退回修改</button>`:""}</div>
      ${[...c.jobs].reverse().map(j=>`<div class="job"><div class="evidence-head"><strong>${esc(scopes[j.scope])}</strong>${badge(j.state)}</div><p class="muted">${esc(j.detail)||"审批已保存，尚未发送到 Codex"}</p><div class="code">指令 ${esc(j.id)}${j.turn_id?" · 执行轮次 "+esc(j.turn_id):""}</div><div class="detail-actions">${j.state==="待发送"?`<button data-dispatch="${esc(j.id)}"><i data-lucide="send"></i>发送到关联对话</button>`:""}<button data-copy="${esc(j.id)}"><i data-lucide="copy"></i>复制续接指令</button><button data-receipt="${esc(j.id)}"><i data-lucide="file-check"></i>核验执行结果</button></div><details><summary>审批指令与限制</summary><pre>${esc(j.prompt)}</pre></details></div>`).join("")}
    </section>
    ${readonly ? `<div class="notice">交付源记录只读。上方审批仅推进明确范围，不改写冻结包，不等于已上线。</div>` :
      `<div class="detail-actions"><button data-action="update"><i data-lucide="square-pen"></i>更新进度</button><button data-action="evidence"><i data-lucide="flask-conical"></i>追加证据</button><button data-action="decision"><i data-lucide="circle-check"></i>请求决策</button><button data-action="${i.archived?"restore":"archive"}"><i data-lucide="archive"></i>${i.archived?"恢复":"归档"}</button></div>`}
    <div class="tabs">${[["overview","概览"],["evidence","测试证据"],["timeline","时间线"],["decisions","决策"]].map(([k,l])=>`<button data-tab="${k}" class="${tab===k?"active":""}">${l}${k==="evidence"?" · "+i.evidence.length:""}</button>`).join("")}</div>
    <div id="detail-content"></div>`;
  const content = $("#detail-content");
  if (tab==="overview") content.innerHTML=`<section class="section"><h3>需求描述</h3><p>${display(i.description)||"暂无描述"}</p></section><section class="section"><h3>验收标准</h3><p>${display(i.acceptance)||"待补充"}</p></section><section class="section"><h3>当前候选版本</h3><div class="code">${esc(i.candidate)||"未绑定候选"}</div></section>`;
  if (tab==="timeline") content.innerHTML=`<section class="section timeline">${[...i.events].reverse().map(e=>`<div class="timeline-entry"><div>${display(e.message)}</div><div class="muted">${date(e.at)} · ${display(e.actor)}</div></div>`).join("")}</section>`;
  if (tab==="evidence") content.innerHTML=[...i.evidence].reverse().map(e=>`<div class="evidence"><div class="evidence-head"><strong>${display(e.label)}</strong>${badge(e.result)}</div><div class="code">候选 ${esc(e.candidate)||"未知"}</div><div class="muted">${esc(e.verification)}${e.candidate!==i.candidate?" · 历史候选":""}</div><p class="muted">${display(e.detail)}</p>${Object.keys(e.profiles).length?`<table class="matrix"><thead><tr><th>环境 / 测试组</th><th>组结果</th><th>测试项</th></tr></thead><tbody>${Object.entries(e.profiles).map(([p,groups])=>`<tr><td><details><summary title="${esc(p)}">${display(p)}</summary>${groups.map(g=>`<div class="group"><span title="${esc(g.name)}">${display(g.name)}</span>${badge(g.result)}</div>`).join("")}</details></td><td>${groups.filter(g=>g.result==="通过").length}/${groups.length}</td><td>${groups.reduce((n,g)=>n+(Number(g.tests)||0),0)}</td></tr>`).join("")}</tbody></table>`:""}</div>`).join("")||'<div class="empty">尚无测试证据</div>';
  if (tab==="decisions") content.innerHTML=(c.scope?`<section class="section"><h3>${esc(scopes[c.scope])}</h3>${badge(c.decision_state)}<p>${esc(c.next_step)}</p><p class="muted">${esc(c.decision_note||"")}</p></section>`:"")+i.decisions.map(d=>`<div class="evidence"><div class="evidence-head"><strong>${esc(d.label)}</strong>${badge(d.state)}</div>${readonly&&c.scope?'<p class="muted">这是冻结源状态，不是新的合并审批；实际推进以上方明确范围为准。</p>':""}<div class="code">候选 ${esc(d.candidate)}</div><p class="muted">${esc(d.note)}</p>${d.state==="待确认"&&!readonly?`<button data-resolve="${esc(d.id)}">处理决策</button>`:""}</div>`).join("")||'<div class="empty">暂无决策记录</div>';
  $$("[data-tab]").forEach(b=>b.onclick=()=>{tab=b.dataset.tab;renderDetail();icons();});
  $$("[data-action]").forEach(b=>b.onclick=()=>openAction(b.dataset.action));
  $$("[data-resolve]").forEach(b=>b.onclick=()=>openAction("resolve",b.dataset.resolve));
  $$("[data-coordinate]").forEach(b=>b.onclick=()=>openCoordinate(b.dataset.coordinate));
  $$("[data-repair-proposal]").forEach(b=>b.onclick=()=>{
    openCoordinate("propose");
    $("#action-form [name=scope]").value="develop";
    $("#action-form [name=next_step]").value=i.blocker_analysis.approval.next_step;
  });
  $$("[data-dispatch]").forEach(b=>b.onclick=()=>dispatch(i.id,b.dataset.dispatch));
  $$("[data-copy]").forEach(b=>b.onclick=async()=>{try{await navigator.clipboard.writeText(c.jobs.find(j=>j.id===b.dataset.copy).prompt);toast("续接指令已复制，请在关联对话发送");}catch(e){toast("复制失败，可展开审批指令查看",true);}});
  $$("[data-receipt]").forEach(b=>b.onclick=()=>openCoordinate("receipt",b.dataset.receipt));
  $$(".job").forEach((node,index)=>{
    const j=[...c.jobs].reverse()[index];
    if(j.state==="待发送"){
      const b=document.createElement("button");b.textContent="撤销未发指令";
      b.onclick=async()=>{try{await api("/api/items/"+i.id+"/coordinate",{action:"cancel",revision:c.revision,job_id:j.id});await load();toast("未发送指令已撤销");}catch(e){toast(e.message,true);}};
      node.querySelector(".detail-actions").appendChild(b);
    }
  });
}
function openCoordinate(type, jobId) {
  const i=items.find(i=>i.id===selected),c=i.coordination||{revision:0,jobs:[]};
  action={type:"coordinate",operation:type,revision:c.revision,itemId:i.id,jobId};
  $("#action-title").textContent={bind:"关联 Codex 对话",propose:"确认下一步范围",approve:"人工批准并推进",reject:"退回修改",receipt:"核验执行结果"}[type];
  const field=(n,l,v="")=>`<label>${l}<textarea name="${n}" maxlength="2000" rows="3" required>${esc(v)}</textarea></label>`;
  $("#action-fields").innerHTML=type==="bind"?`<label>查找对话标题<input id="thread-query" maxlength="160" value="${esc(c.thread?.title||"")}"></label><button type="button" id="find-threads"><i data-lucide="search"></i>查找对话</button><div id="thread-results"></div><label>对话编号<input name="thread_id" required value="${esc(c.thread?.id||i.intake_source?.thread_id||"")}" maxlength="80"></label><label>已核实进展<textarea name="progress" maxlength="2000">${esc(c.progress||"")}</textarea></label><label>核验来源<textarea name="progress_evidence" maxlength="2000">${esc(c.progress_evidence||"")}</textarea></label>`:
    type==="propose"?`<label>审批类型<select name="scope">${Object.entries(scopes).map(([v,l])=>`<option value="${v}" ${v===(c.scope||"preflight")?"selected":""}>${l}</option>`).join("")}</select></label>${field("next_step","具体执行范围",c.next_step||"")}<div class="notice">不包含远端推送、部署、线上模板修改或生产工单重试。</div>`:
    type==="receipt"?`<label>本轮状态<select name="state"><option>本轮结束</option><option>执行失败</option><option>执行中</option></select></label>${field("detail","实际结果与证据来源")}<div class="notice">人工核验记录不是独立认证；本轮结束不等于需求完成或生产已部署。</div>`:
    `<strong>${esc(scopes[c.scope])}</strong><p>${esc(c.next_step)}</p><div class="code">候选 ${esc(i.candidate)||"未绑定"}</div>${field("note","审批意见")}<div class="notice">${type==="approve"?"确认后保存审批并尝试发送至《"+esc(c.thread?.title)+"》。共享连接不可用时保留待发送，可手动续接。":""}不包含生产操作，不授予工具权限提升。</div>`;
  $("#action-form .primary").textContent=type==="approve"?"确认批准并推进":"保存";
  $("#action-dialog").showModal();icons();
  if(type==="bind")$("#find-threads").onclick=async()=>{
    const b=$("#find-threads");b.disabled=true;
    try{const threads=await api("/api/threads",{query:$("#thread-query").value});
      $("#thread-results").innerHTML=threads.map(t=>`<button type="button" class="thread-result" data-thread="${esc(t.id)}"><strong>${esc(t.title)}</strong><span class="code">${esc(t.cwd)}</span></button>`).join("")||'<p class="muted">未找到对话，请使用准确标题或对话编号</p>';
      $$("[data-thread]").forEach(x=>x.onclick=()=>{$("#action-fields [name=thread_id]").value=x.dataset.thread;});
    }catch(e){toast(e.message,true);}finally{b.disabled=false;}
  };
}
async function dispatch(id,jobId) {
  try{const i=items.find(i=>i.id===id);await api("/api/items/"+id+"/coordinate",{action:"dispatch",revision:i.coordination.revision,job_id:jobId});await load();toast("正在尝试发送，执行结果以回执为准");setTimeout(()=>load().catch(()=>{}),2000);}
  catch(e){toast(e.message,true);}
}
function openAction(type, decision) {
  action={type,decision};const i=items.find(i=>i.id===selected);
  $("#action-form .primary").textContent="保存记录";
  $("#action-title").textContent={update:"更新进度",evidence:"追加测试证据",decision:"请求人工决策",resolve:"处理人工决策",archive:"归档需求",restore:"恢复需求"}[type];
  const field=(name,label,value="",area=false)=>`<label>${label}${area?`<textarea name="${name}" rows="3" maxlength="4000">${esc(value)}</textarea>`:`<input name="${name}" value="${esc(value)}" maxlength="${name==="note"||name==="detail"?2000:160}">`}</label>`;
  const select=(name,label,values,value)=>`<label>${label}<select name="${name}">${values.map(v=>`<option ${v===value?"selected":""}>${esc(v)}</option>`).join("")}</select></label>`;
  $("#action-fields").innerHTML=["archive","restore"].includes(type)?`<p>${esc(i.title)}</p>`:type==="update"?`${field("title","标题",i.title)}${field("description","需求描述",i.description,true)}<div class="form-row">${select("phase","交付阶段",i.workflow.map(n=>n.name),i.phase)}${select("status","执行状态",["待处理","进行中","阻塞","待决策","已完成"],i.status)}</div>${field("candidate","候选版本",i.candidate)}${field("blocker","阻塞原因",i.blocker,true)}${field("acceptance","验收标准",i.acceptance,true)}${field("note","进展 / 部署或验收凭据","",true)}`:
    type==="evidence"?`${field("label","证据标题")}${field("candidate","候选版本",i.candidate)}${select("result","测试结果",["未验证","通过","失败"],"未验证")}${field("detail","证据路径 / 运行编号 / 说明","",true)}`:
    type==="decision"?`${field("label","决策标题（绑定当前候选）")}<div class="code">${esc(i.candidate)||"请先更新候选版本"}</div>`:
    `${select("state","决策结果",["批准","拒绝"],"批准")}${field("note","决策意见","",true)}<div class="notice">仅记录你的决策，不执行部署或重试工单。</div>`;
  $("#action-dialog").showModal();icons();
}
async function submit(form, path, extra={}) {
  const button=form.querySelector(".primary");button.disabled=true;
  try {await api(path,{...Object.fromEntries(new FormData(form)),...extra});form.closest("dialog").close();await load();toast("记录已保存");}
  catch(e){toast(e.message,true);}finally{button.disabled=false;}
}
$("#create-form").onsubmit=e=>{e.preventDefault();submit(e.target,"/api/items");};
$("#action-form").onsubmit=async e=>{
  e.preventDefault();
  if(action.type!=="coordinate"){const i=items.find(i=>i.id===selected);return submit(e.target,"/api/items/"+i.id,{action:action.type,decision:action.decision,version:i.version});}
  const a={...action},button=e.target.querySelector(".primary");button.disabled=true;
  try{const i=items.find(i=>i.id===a.itemId);
    const result=await api("/api/items/"+i.id+"/coordinate",{...Object.fromEntries(new FormData(e.target)),action:a.operation,revision:a.revision,job_id:a.jobId,thread_id:a.operation==="receipt"?i.coordination.thread.id:new FormData(e.target).get("thread_id")});
    $("#action-dialog").close();await load();toast("协调记录已保存");
    if(a.operation==="approve")await dispatch(i.id,result.coordination.jobs.at(-1).id);
  }catch(error){toast(error.message,true);}finally{button.disabled=false;}
};
$$("[data-close]").forEach(b=>b.onclick=()=>b.closest("dialog").close());
$("#new").onclick=()=>{$("#create-form").reset();$("#create-form [name=project_id]").innerHTML=catalog.projects.map(p=>`<option value="${esc(p.id)}" ${p.id===$("#project").value?"selected":""}>${esc(p.name)}</option>`).join("");if(!catalog.projects.length)return toast("请先在 Codex 中添加项目",true);$("#create-dialog").showModal();};
$$("[data-view]").forEach(b=>b.onclick=()=>{view=b.dataset.view;$("#project").value="";$("#phase").value="";$$("[data-view]").forEach(x=>x.classList.toggle("active",x===b));$("#view-title").textContent=b.childNodes[1].textContent.trim();refreshPhases();render();});
["search","project","phase"].forEach(id=>$("#"+id).addEventListener(id==="search"?"input":"change",()=>{if(id==="project")refreshPhases();render();}));
$("#sync").onclick=async()=>{const b=$("#sync");b.disabled=true;try{const r=await api("/api/sync",{});await load();toast(`已同步 ${r.imported} 条记录${r.errors.length?"；"+r.errors.join("；"):""}`,r.errors.length>0);}catch(e){toast(e.message,true);}finally{b.disabled=false;}};
$("#export").onclick=async()=>{try{const data=await api("/api/export"), url=URL.createObjectURL(new Blob([JSON.stringify({schema:1,exported_at:new Date().toISOString(),items:data},null,2)],{type:"application/json"}));const a=document.createElement("a");a.href=url;a.download="delivery-console-export.json";a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}catch(e){toast(e.message,true);}};
function refreshPhases(){
  const old=$("#phase").value,p=catalog.projects.find(p=>p.id===$("#project").value);
  const names=p?p.template.map(n=>n.name):[...new Set(items.flatMap(i=>i.workflow.map(n=>n.name)))];
  $("#phase").innerHTML='<option value="">全部阶段</option>'+names.map(n=>`<option>${esc(n)}</option>`).join("");
  $("#phase").value=names.includes(old)?old:"";
}
function renderTree(){
  if(!$("#project-tree")){
    const node=document.createElement("section");node.id="project-tree";
    $(".sidebar .filters").after(node);
  }
  const groups=[...catalog.collections,{id:"",name:"未分组"}];
  const visible=visibleItems(), searching=!!$("#search").value;
  const treeItem=i=>`<div class="tree-row ${i.id===selected?"selected":""}"><button class="tree-item" data-tree-item="${i.id}" title="${esc(i.title)}"><strong>${esc(titleOf(i))}</strong><small class="${i.status==="阻塞"?"blocked-text":""}">${esc(stageOf(i))}</small></button>${threadUrl(i.coordination?.thread)?`<a class="tree-chat" href="${esc(threadUrl(i.coordination.thread))}" title="打开 Codex 对话：${esc(i.coordination.thread.title)}"><i data-lucide="external-link"></i></a>`:`<button class="tree-bind" data-tree-bind="${i.id}" title="关联 Codex 对话"><i data-lucide="link"></i></button>`}</div>`;
  $("#project-tree").innerHTML=`<div class="tree-tools"><strong>项目</strong><button data-new-catalog="collection" title="新建项目集" aria-label="新建项目集"><i data-lucide="folder-plus"></i></button>${catalog.readonly_projects?"":'<button data-new-catalog="project" title="新建项目" aria-label="新建项目"><i data-lucide="plus"></i></button>'}</div>
    <button class="tree-view" data-tree-view="management"><i data-lucide="sliders-horizontal"></i>管理面板</button><button class="tree-view" data-tree-view="capture"><i data-lucide="messages-square"></i>聊天采集 ${capture.inputs.length}</button><button class="tree-view" data-tree-view="intake"><i data-lucide="inbox"></i>会话变更 ${intake.filter(e=>e.state==="待确认").length}</button><button class="tree-view" data-tree-view="archived"><i data-lucide="archive"></i>已归档</button>`+
    groups.map(g=>`<section class="tree-group" data-drop="${g.id}"><div class="tree-group-head"><strong>${esc(g.name)}</strong>${g.id?`<button data-edit-catalog="${g.id}" title="编辑项目集" aria-label="编辑项目集"><i data-lucide="pencil"></i></button>`:""}</div>${catalog.projects.filter(p=>p.collection_id===g.id).map(p=>{
      const rows=visible.filter(i=>i.project_id===p.id),open=expanded.has(p.id)||rows.length>0&&(searching||["blocked","decisions","archived"].includes(view));
      const phases=[...new Set([...p.template.map(n=>n.name),...rows.map(i=>i.phase)])];
      return `<div class="tree-project" draggable="true" data-drag="${esc(p.id)}"><div class="tree-project-head"><button data-expand="${esc(p.id)}" aria-expanded="${open}" title="${open?"收起":"展开"}项目"><i data-lucide="${open?"chevron-down":"chevron-right"}"></i></button><button class="project-name" data-project-name="${esc(p.name)}" data-project-id="${esc(p.id)}" title="${esc(p.path||p.name)}">${esc(p.name)} <small>${rows.length}</small></button><button data-edit-catalog="${esc(p.id)}" title="项目与流程配置" aria-label="项目与流程配置"><i data-lucide="settings-2"></i></button></div>${open?phases.filter(phase=>rows.some(i=>i.phase===phase)).map(phase=>`<details open><summary>${esc(phase)} <small>${rows.filter(i=>i.phase===phase).length}</small></summary>${rows.filter(i=>i.phase===phase).map(treeItem).join("")}</details>`).join("")||'<p class="muted">暂无需求</p>':""}</div>`;
    }).join("")}</section>`).join("")+
    (visible.some(i=>!i.project_id)?`<section class="unassigned"><h3>未关联项目的历史需求</h3>${visible.filter(i=>!i.project_id).map(treeItem).join("")}</section>`:"");
  $$("[data-new-catalog]").forEach(b=>b.onclick=()=>openCatalog(b.dataset.newCatalog));
  $$("[data-edit-catalog]").forEach(b=>b.onclick=()=>openCatalog(null,b.dataset.editCatalog));
  $$("[data-expand]").forEach(b=>b.onclick=()=>{expanded.has(b.dataset.expand)?expanded.delete(b.dataset.expand):expanded.add(b.dataset.expand);render();});
  $$("[data-project-name]").forEach(b=>b.onclick=()=>{view="all";expanded.add(b.dataset.projectId);$("#project").value=b.dataset.projectId;$("#view-title").textContent=b.dataset.projectName;refreshPhases();render();});
  const selectTree=id=>{selected=id;tab="overview";history.replaceState(null,"","#"+selected);render();};
  $$("[data-tree-item]").forEach(b=>b.onclick=()=>selectTree(b.dataset.treeItem));
  $$("[data-tree-bind]").forEach(b=>b.onclick=()=>{selectTree(b.dataset.treeBind);openCoordinate("bind");});
  $$("[data-tree-view]").forEach(b=>b.onclick=()=>{view=b.dataset.treeView;$("#project").value="";$("#phase").value="";$("#view-title").textContent={management:"管理面板",intake:"会话变更",capture:"聊天采集",archived:"已归档"}[view];render();});
  $$("[data-drag]").forEach(n=>n.ondragstart=e=>{e.dataTransfer.setData("text/plain",n.dataset.drag);e.dataTransfer.effectAllowed="move";});
  $$("[data-drop]").forEach(n=>{
    n.ondragover=e=>{e.preventDefault();n.classList.add("drag-over");};
    n.ondragleave=()=>n.classList.remove("drag-over");
    n.ondrop=async e=>{e.preventDefault();n.classList.remove("drag-over");const p=catalog.projects.find(p=>p.id===e.dataTransfer.getData("text/plain"));if(!p)return;try{await api("/api/catalog",{id:p.id,revision:p.revision,collection_id:n.dataset.drop});await load();toast("项目已移动");}catch(error){toast(error.message,true);}};
  });
}
function openCatalog(kind,id){
  const existing=[...catalog.projects,...catalog.collections].find(e=>e.id===id);
  catalogEdit=existing||{kind};
  $("#catalog-form").reset();
  $("#catalog-title").textContent=(existing?"编辑":"新建")+(catalogEdit.kind==="project"?"项目":"项目集");
  $("#catalog-form [name=name]").value=existing?.name||"";
  $("#catalog-form [name=name]").readOnly=!!existing&&existing.kind==="project";
  $("#collection-field").hidden=$("#template-field").hidden=catalogEdit.kind!=="project";
  $("#catalog-form [name=collection_id]").innerHTML='<option value="">未分组</option>'+catalog.collections.map(g=>`<option value="${g.id}">${esc(g.name)}</option>`).join("");
  $("#catalog-form [name=collection_id]").value=existing?.collection_id||"";
  draftNodes=structuredClone(existing?.template||standardNodes);
  customTemplate=JSON.stringify(draftNodes)!==JSON.stringify(standardNodes);
  nodeSelected=0;
  $("#catalog-form [name=template_mode][value="+(customTemplate?"custom":"standard")+"]").checked=true;
  renderNodes();$("#catalog-dialog").showModal();
}
function renderNodes(){
  $("#catalog-dialog").classList.toggle("custom-workflow",customTemplate);
  $("#add-node").hidden=!customTemplate;
  $("#node-properties").hidden=!customTemplate;
  $("#template-nodes").innerHTML=draftNodes.map((n,index)=>`${index?'<i class="flow-arrow" data-lucide="arrow-down"></i>':""}<button type="button" class="flow-node ${index===nodeSelected?"active":""}" data-node-select="${index}" ${!customTemplate?"disabled":""}><span>${index+1}</span><strong>${esc(n.name)}</strong>${n.approval?'<i data-lucide="shield-check"></i>':""}</button>`).join("");
  $$("[data-node-select]").forEach(b=>b.onclick=()=>{nodeSelected=Number(b.dataset.nodeSelect);renderNodes();});
  const index=nodeSelected,n=draftNodes[index];
  $("#node-properties").innerHTML=`<h3>节点属性</h3><label>名称<input aria-label="节点名称" value="${esc(n.name)}" maxlength="40" data-node-name="${index}" ${index===0||index===draftNodes.length-1?"readonly":""}></label><div class="node-flags">${[["human","人工确认"],["evidence","通过证据"],["approval","人工批准"]].map(([key,label])=>`<label><input type="checkbox" data-node-flag="${index}" data-flag="${key}" ${n[key]?"checked":""}>${label}</label>`).join("")}</div><div class="node-tools"><button type="button" data-node-move="${index}" data-delta="-1" title="上移节点" ${index<2||index===draftNodes.length-1?"disabled":""}><i data-lucide="arrow-up"></i></button><button type="button" data-node-move="${index}" data-delta="1" title="下移节点" ${index===0||index>=draftNodes.length-2?"disabled":""}><i data-lucide="arrow-down"></i></button><button type="button" data-node-remove="${index}" title="删除节点" ${index===0||index===draftNodes.length-1?"disabled":""}><i data-lucide="trash-2"></i></button></div>`;
  $$("[data-node-name]").forEach(n=>n.oninput=()=>{draftNodes[Number(n.dataset.nodeName)].name=n.value;$(`[data-node-select="${n.dataset.nodeName}"] strong`).textContent=n.value;});
  $$("[data-node-flag]").forEach(n=>n.onchange=()=>draftNodes[Number(n.dataset.nodeFlag)][n.dataset.flag]=n.checked);
  $$("[data-node-move]").forEach(n=>n.onclick=()=>{const from=Number(n.dataset.nodeMove),to=from+Number(n.dataset.delta);[draftNodes[from],draftNodes[to]]=[draftNodes[to],draftNodes[from]];nodeSelected=to;renderNodes();});
  $$("[data-node-remove]").forEach(n=>n.onclick=()=>{draftNodes.splice(Number(n.dataset.nodeRemove),1);nodeSelected=Math.min(nodeSelected,draftNodes.length-1);renderNodes();});
  icons();
}
$("#add-node").onclick=()=>{if(draftNodes.length>=20)return toast("最多20个节点",true);draftNodes.splice(-1,0,{name:"新节点",human:true,evidence:true,approval:false});nodeSelected=draftNodes.length-2;renderNodes();};
$$("[name=template_mode]").forEach(n=>n.onchange=()=>{customTemplate=n.value==="custom";if(!customTemplate)draftNodes=structuredClone(standardNodes);nodeSelected=0;renderNodes();});
$("#catalog-form").onsubmit=async e=>{
  e.preventDefault();const button=e.target.querySelector(".primary");button.disabled=true;
  try{await api("/api/catalog",{...Object.fromEntries(new FormData(e.target)),kind:catalogEdit.kind,id:catalogEdit.id,revision:catalogEdit.revision,...(catalogEdit.kind==="project"?{template:draftNodes}:{})});$("#catalog-dialog").close();await load();refreshPhases();toast("配置已保存");}catch(error){toast(error.message,true);}finally{button.disabled=false;}
};
(async()=>{try{const response=await fetch("/api/session");if(!response.ok)throw new Error("无法连接本机服务");const s=await response.json();token=s.token;$("#sync").hidden=!s.source_import;$("#phase").innerHTML+=s.phases.map(p=>`<option>${esc(p)}</option>`).join("");await load();if(view==="management")$("#view-title").textContent="管理面板";icons();}catch(e){toast(e.message,true);}})();
setInterval(()=>{if(token&&!document.querySelector("dialog[open]"))load().catch(e=>toast(e.message,true));},15000);
