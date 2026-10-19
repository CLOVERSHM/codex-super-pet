let token, catalog={projects:[],collections:[]}, board={sessions:[]}, skills=[];
let projectId="", selected="", query="", statusFilter="", workspace="sessions";
let sortKey="updated", sortDirection=-1, drawerOpen=false, skillDraft=null, snapshot="", detailSignature="";
let welcomePage=0, welcomeStarted=false;
const welcomeKey="delivery-console-onboarding-v1";
const welcomePages=[
  ["首次采集","默认选中近七天（含今天）和未加白项目。确认后在本机后台读取历史用户输入，候选须人工审阅。"],
  ["选择项目","最左侧项目树跟随 Codex 已保存项目；点击项目后，右侧只看该项目的会话。"],
  ["筛选会话","上方状态分类展示数量；搜索框、项目筛选和表头排序帮助你快速定位。"],
  ["查看详情","点击表格行看本轮状态、阻塞原因与下一步；行末箭头用于打开原 Codex 会话。"],
  ["管理面板","在左下角进入历史采集、项目加白和进度报告。采集结果需要逐条人工确认。"],
  ["需求账本","在左下角进入需求、证据和审批记录；审批不会自动执行生产操作。"]
];
const shanghaiDay=d=>new Intl.DateTimeFormat("en-CA",{timeZone:"Asia/Shanghai",year:"numeric",month:"2-digit",day:"2-digit"}).format(d);
const $=selector=>document.querySelector(selector);
const esc=value=>String(value??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const labels={in_progress:"进行中",completed:"已完成",blocked:"遇阻塞",waiting_approval:"待审批",waiting_user:"待用户补充",unverified:"待核实",awaiting_report:"待回报"};
const date=value=>value?new Date(Number(value)).toLocaleString("zh-CN",{month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit"}):"";
const project=id=>catalog.projects.find(p=>p.id===id);
const icon=()=>window.lucide?.createIcons();
const threadUrl=id=>/^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(id||"")?`codex://threads/${encodeURIComponent(id)}`:"";
async function api(path,data){
  const response=await fetch(path,{method:data?"POST":"GET",headers:{"Authorization":`Bearer ${token}`,"Content-Type":"application/json"},body:data?JSON.stringify(data):undefined});
  const result=await response.json();
  if(!response.ok)throw Error(result.error||"请求失败");
  return result;
}
function notify(message){$("#toast").textContent=message;$("#toast").hidden=false;setTimeout(()=>$("#toast").hidden=true,4500)}
async function load(){
  const next=await Promise.all([api("/api/catalog"),api("/api/board"),api("/api/session-skills")]);
  const signature=JSON.stringify(next);
  if(signature===snapshot)return;
  snapshot=signature;
  [catalog,board,skills]=next;
  render();
  if(!localStorage.getItem(welcomeKey)&&!$("#welcome-dialog").open)openWelcome();
}
function sessions(){return board.sessions.filter(s=>!!project(s.project_id))}
function switchWorkspace(next){
  workspace=next;
  $("#session-workspace").hidden=next!=="sessions";
  for(const name of ["management","ledger"]){
    const frame=$(`#${name}-frame`);
    frame.hidden=next!==name;
    if(next===name&&!frame.getAttribute("src"))frame.src=`/legacy?embedded=1${name==="management"?"#management":""}`;
  }
  document.querySelectorAll("[data-workspace]").forEach(b=>{
    b.classList.toggle("active",b.dataset.workspace===next);
    b.setAttribute("aria-current",b.dataset.workspace===next?"page":"false");
  });
  if(next!=="sessions")closeDrawer();
}
function closeDrawer(){
  drawerOpen=false;
  $("#detail").hidden=true;
  $("#drawer-backdrop").hidden=true;
}
function openDrawer(id){
  if(selected!==id)skillDraft=null;
  selected=id;drawerOpen=true;
  renderDetail(true);
  $("#detail").hidden=false;
  $("#drawer-backdrop").hidden=false;
  $("#detail-close").focus();
}
function render(){
  const all=sessions();
  const count=id=>all.filter(s=>s.project_id===id).length;
  const row=(id,name,n,child=false)=>`<button class="tree-row ${projectId===id?"active":""} ${child?"tree-child":""}" data-project="${esc(id)}" title="${esc(name)}"><i data-lucide="${id?"folder":"layers"}"></i><span>${esc(name)}</span><small>${n}</small></button>`;
  const groups=new Set(catalog.projects.map(p=>p.collection_id||""));
  $("#projects").innerHTML=row("","全部项目",all.length)+[...catalog.collections.filter(c=>groups.has(c.id)).map(c=>`<div class="tree-group">${esc(c.name)}</div>${catalog.projects.filter(p=>p.collection_id===c.id).map(p=>row(p.id,p.name,count(p.id),true)).join("")}`),...catalog.projects.filter(p=>!p.collection_id).map(p=>row(p.id,p.name,count(p.id)))].join("");
  $("#project-count").textContent=catalog.projects.length;
  $("#health").className="health"+(board.health==="运行中"?"":" error");
  $("#health").textContent=`${board.health} · 上次对账 ${board.last_checked?date(Number(board.last_checked)*1000):"尚无"} · 启动及每 ${Math.round(board.interval_seconds/60)} 分钟检查输入变化；不是轮次结束信号`;
  const filter=$("#project-filter");
  filter.innerHTML='<option value="">全部项目</option>'+catalog.projects.map(p=>`<option value="${esc(p.id)}">${esc(p.name)}</option>`).join("");
  if(!project(projectId))projectId="";
  filter.value=projectId;
  const scoped=all.filter(s=>!projectId||s.project_id===projectId);
  $("#status-tabs").innerHTML=[["","全部"],...Object.entries(labels)].map(([value,label])=>`<button type="button" data-status="${value}" class="${statusFilter===value?"active":""}" aria-pressed="${statusFilter===value}">${label}<small>${value?scoped.filter(s=>s.state===value).length:scoped.length}</small></button>`).join("");
  const visible=scoped.filter(s=>(!statusFilter||s.state===statusFilter)&&(!query||[s.title,s.report?.summary,s.report?.stage,project(s.project_id)?.name].some(v=>String(v||"").toLowerCase().includes(query))));
  const compare=(a,b)=>{
    const value=s=>sortKey==="updated"?Number(s.updated_at_ms)||0:sortKey==="project"?project(s.project_id)?.name||"":sortKey==="state"?labels[s.state]||s.state:s.title||"";
    const x=value(a),y=value(b);
    return sortDirection*(typeof x==="number"?x-y:String(x).localeCompare(String(y),"zh-CN")) || String(a.id).localeCompare(String(b.id));
  };
  visible.sort(compare);
  $("#session-count").textContent=`${visible.length} 个会话`;
  $("#sessions").innerHTML=visible.map(s=>{
    const url=threadUrl(s.id);
    const progress=s.report?.summary||(s.state==="awaiting_report"?"输入已变化，等待本轮状态回报":"尚无可信状态回报");
    return `<tr tabindex="0" data-session="${esc(s.id)}" aria-label="查看 ${esc(s.title)} 详情"><td class="title-cell" title="${esc(s.title)}"><strong>${esc(s.title)}</strong></td><td><span class="badge ${esc(s.state)}">${labels[s.state]||"待核实"}</span></td><td>${esc(project(s.project_id)?.name)}</td><td class="progress-cell"><span>${esc(s.report?.stage||"尚无阶段")}</span><small title="${esc(progress)}">${esc(progress)}</small></td><td class="date-cell">${date(s.updated_at_ms)}</td><td class="action-cell">${url?`<a href="${esc(url)}" title="在 Codex 打开会话" aria-label="在 Codex 打开 ${esc(s.title)}"><i data-lucide="external-link"></i></a>`:"<span title=\"无效会话标识\">—</span>"}</td></tr>`;
  }).join("")||'<tr><td colspan="6" class="empty">此范围暂无会话</td></tr>';
  document.querySelectorAll("[data-sort]").forEach(b=>{
    const active=b.dataset.sort===sortKey;
    b.closest("th").setAttribute("aria-sort",active?(sortDirection===1?"ascending":"descending"):"none");
    b.classList.toggle("active",active);
    b.querySelector("i,svg")?.remove();
    b.insertAdjacentHTML("beforeend",`<i data-lucide="${active?(sortDirection===1?"arrow-up":"arrow-down"):"arrow-down-up"}"></i>`);
  });
  if(drawerOpen){
    if(visible.some(s=>s.id===selected))renderDetail();
    else closeDrawer();
  }
  document.querySelectorAll("[data-project]").forEach(b=>b.onclick=()=>{
    projectId=b.dataset.project;closeDrawer();switchWorkspace("sessions");render();
  });
  document.querySelectorAll("[data-status]").forEach(b=>b.onclick=()=>{statusFilter=b.dataset.status;render()});
  document.querySelectorAll("[data-session]").forEach(row=>{
    row.onclick=e=>{if(!e.target.closest("a"))openDrawer(row.dataset.session)};
    row.onkeydown=e=>{if(!e.target.closest("a")&&(e.key==="Enter"||e.key===" ")){e.preventDefault();openDrawer(row.dataset.session)}};
  });
  icon();
}
function openWelcome(){
  welcomePage=0;
  welcomeStarted=false;
  const end=shanghaiDay(new Date());
  $("#welcome-end").value=end;
  $("#welcome-start").value=shanghaiDay(new Date(Date.parse(`${end}T12:00:00Z`)-6*86400000));
  $("#welcome-start").max=end;
  $("#welcome-end").max=end;
  $("#welcome-projects").innerHTML=catalog.projects.map(p=>`<label><input type="checkbox" value="${esc(p.id)}" ${p.capture_excluded?"disabled":"checked"}><span>${esc(p.name)}${p.capture_excluded?" · 已加白":""}</span></label>`).join("")||'<p>Codex 项目暂不可用，稍后可在管理面板采集。</p>';
  $("#welcome-error").hidden=true;
  renderWelcome();
  $("#welcome-dialog").showModal();
}
function renderWelcome(){
  const [title,description]=welcomePages[welcomePage];
  $("#welcome-title").textContent=title;
  $("#welcome-description").textContent=description;
  $("#welcome-step").textContent=`${welcomePage+1} / ${welcomePages.length}`;
  $("#welcome-form").hidden=welcomePage!==0;
  $("#welcome-back").hidden=welcomePage===0;
  $("#welcome-next").textContent=welcomePage===0?"开始采集":welcomePage===welcomePages.length-1?"完成":"下一步";
  $("#welcome-skip").textContent=welcomePage===0?"跳过采集":"结束引导";
  icon();
}
function closeWelcome(){
  $("#welcome-dialog").close();
  localStorage.setItem(welcomeKey,"done");
}
async function nextWelcome(){
  if(welcomePage===0&&!welcomeStarted){
    const form=$("#welcome-form");
    if(!form.reportValidity())return;
    const project_ids=[...$("#welcome-projects").querySelectorAll("input:checked")].map(input=>input.value);
    if(!project_ids.length){$("#welcome-error").textContent="请选择至少一个未加白项目；也可以跳过并稍后采集。";$("#welcome-error").hidden=false;return}
    $("#welcome-next").disabled=true;
    try{
      await api("/api/management/history",{start:$("#welcome-start").value,end:$("#welcome-end").value,project_ids});
      welcomeStarted=true;
      $("#welcome-error").hidden=true;
      notify("历史采集已启动，候选将在管理面板等待人工确认");
    }catch(e){$("#welcome-error").textContent=e.message;$("#welcome-error").hidden=false;return}
    finally{$("#welcome-next").disabled=false}
  }
  if(welcomePage===welcomePages.length-1){closeWelcome();if(welcomeStarted)switchWorkspace("management");return}
  welcomePage++;
  renderWelcome();
}
function renderDetail(force=false){
  const s=sessions().find(s=>s.id===selected);
  if(!s)return closeDrawer();
  const r=s.report, p=project(s.project_id),url=threadUrl(s.id);
  const signature=JSON.stringify([s,p,skills]);
  if(!force&&signature===detailSignature)return;
  detailSignature=signature;
  $("#detail").innerHTML=`<div class="drawer-head"><strong>会话详情</strong><button id="detail-close" type="button" title="关闭详情" aria-label="关闭详情"><i data-lucide="x"></i></button></div><div class="detail-content"><div class="detail-title"><h2>${esc(s.title)}</h2>${url?`<a href="${esc(url)}" title="在 Codex 打开会话"><i data-lucide="external-link"></i>打开会话</a>`:""}</div><p class="subtitle">${esc(p?.name)} · ${s.archived?"已归档":"活跃"} · 最近活动 ${date(s.updated_at_ms)}</p>
    <div class="detail-grid"><div><dt>当前状态</dt><dd><span class="badge ${esc(s.state)}">${labels[s.state]||"待核实"}</span></dd></div><div><dt>工作流 / 阶段</dt><dd>${esc(r?`${r.workflow} / ${r.stage}`:"尚无本轮回报")}</dd></div><div><dt>任务标识</dt><dd>${esc(r?.task_key||"未确认")}</dd></div><div><dt>状态来源</dt><dd>${esc(r?.source||"本机元数据")}</dd></div></div>
    <h3>最近一次关键信息</h3><p class="body-text">${esc(r?.summary||"仅检测到会话输入；不能据此判断任务进度。")}</p>
    ${r?.reason?`<h3>原因</h3><p class="body-text">${esc(r.reason)}</p>`:""}
    ${r?.next_action?`<h3>下一步</h3><p class="body-text">${esc(r.next_action)}</p>`:""}
    ${r?.evidence?.length?`<h3>证据引用</h3><p class="body-text">${r.evidence.map(esc).join("<br>")}</p>`:""}
    ${s.previous_report?`<p class="muted">上一份回报已过期，不能作为当前输入的进度依据。</p>`:""}
    <p class="muted">${r?.skill_id==="historical-review"?"历史复核只反映当时对话结论，未独立认证当前运行状态。":"回报由 skill 声明，未独立认证。"}审批与生产操作不会在此自动执行。</p>
    ${renderSkills(p)}</div>`;
  $("#detail-close").onclick=closeDrawer;
  const save=$("#save-skills");
  if(save)save.onclick=saveSkills;
  $("#detail").querySelectorAll("[data-skill]").forEach(input=>input.onchange=()=>{
    skillDraft={projectId:p.id,ids:[...$("#detail").querySelectorAll("[data-skill]:checked")].map(n=>n.dataset.skill)};
  });
  icon();
}
function renderSkills(p){
  if(!p)return "";
  const available=skills.filter(s=>s.project_id===p.id);
  const selectedIds=skillDraft?.projectId===p.id?skillDraft.ids:p.session_skills||[];
  return `<section class="skills"><h3>项目专属 skill</h3>${available.length?available.map(s=>`<label><input type="checkbox" data-skill="${esc(s.id)}" ${selectedIds.includes(s.id)?"checked":""}><span>${esc(s.name)} <span class="muted">(${esc(s.id)})</span></span></label>`).join("")+'<button id="save-skills">保存配置</button>':'<p class="muted">暂无可用专属 skill。可以通过仓库的 create-session-skill 生成。</p>'}</section>`;
}
async function saveSkills(){
  const p=project(sessions().find(s=>s.id===selected)?.project_id);
  if(!p)return;
  try{
    await api("/api/catalog",{id:p.id,revision:p.revision,session_skills:[...$("#detail").querySelectorAll("[data-skill]:checked")].map(n=>n.dataset.skill)});
    skillDraft=null;await load();notify("项目 skill 配置已保存");
  }catch(e){notify(e.message)}
}
$("#search").oninput=e=>{query=e.target.value.trim().toLowerCase();render()};
$("#project-filter").onchange=e=>{projectId=e.target.value;closeDrawer();render()};
document.querySelectorAll("[data-sort]").forEach(b=>b.onclick=()=>{
  if(sortKey===b.dataset.sort)sortDirection*=-1;
  else{sortKey=b.dataset.sort;sortDirection=sortKey==="updated"?-1:1}
  render();
});
document.querySelectorAll("[data-workspace]").forEach(b=>b.onclick=()=>switchWorkspace(b.dataset.workspace));
$("#drawer-backdrop").onclick=closeDrawer;
document.addEventListener("keydown",e=>{if(e.key==="Escape"&&drawerOpen)closeDrawer()});
$("#reconcile").onclick=async()=>{try{await api("/api/board/reconcile",{});await load()}catch(e){notify(e.message)}};
$("#show-guide").onclick=openWelcome;
$("#welcome-close").onclick=closeWelcome;
$("#welcome-skip").onclick=closeWelcome;
$("#welcome-next").onclick=nextWelcome;
$("#welcome-back").onclick=()=>{welcomePage--;renderWelcome()};
$("#welcome-form").onsubmit=e=>{e.preventDefault();nextWelcome()};
$("#welcome-dialog").oncancel=e=>{e.preventDefault();closeWelcome()};
(async()=>{try{token=(await(await fetch("/api/session")).json()).token;await load();setInterval(()=>load().catch(e=>notify(e.message)),15000)}catch(e){notify(e.message)}})();
