/* Management drafts stay local while polling refreshes job status. */
const localDay=d=>new Intl.DateTimeFormat("en-CA",{timeZone:"Asia/Shanghai",year:"numeric",month:"2-digit",day:"2-digit"}).format(d);
let managerTab="history", managerChats=[], managerChatsLoaded=false, managerBusy=false;
let managerStart=localDay(new Date()), managerEnd=managerStart, managerKind="日报", managerScope="projects";
let managerHistoryProjects=new Set(), managerReportIds=new Set(), managerReport;
let managerSignature="";
const candidateDrafts=new Map();
const reportHtml=markdown=>markdown.split("\n").map(line=>{
  if(line.startsWith("### "))return `<h4>${esc(line.slice(4))}</h4>`;
  if(line.startsWith("## "))return `<h3>${esc(line.slice(3))}</h3>`;
  if(line.startsWith("# "))return `<h2>${esc(line.slice(2))}</h2>`;
  if(line.startsWith("- "))return `<p class="report-entry">${esc(line.slice(2))}</p>`;
  return line?`<p>${esc(line)}</p>`:"";
}).join("");
const checkList=(entries,chosen,name)=>entries.map(e=>`<label class="manager-choice"><input type="checkbox" name="${name}" value="${esc(e.id)}" ${chosen.has(e.id)?"checked":""} ${e.disabled?"disabled":""}><span>${esc(e.name||e.title)}${e.disabled?' <small>已加白</small>':""}</span></label>`).join("")||'<p class="muted">暂无可选记录</p>';

function renderManagement(force=false){
  const signature=JSON.stringify([management,catalog,items.map(i=>[i.id,i.version]),managerTab,managerChatsLoaded]);
  if(!force&&(signature===managerSignature||$("#management").contains(document.activeElement)))return;
  managerSignature=signature;
  $("#management").innerHTML=`<div class="manager-tabs" role="tablist">${[["history","历史采集","history"],["exclusions","采集加白","shield-minus"],["reports","进度报告","notebook-pen"]].map(([key,label,icon])=>`<button role="tab" aria-selected="${managerTab===key}" data-manager-tab="${key}" class="${managerTab===key?"active":""}"><i data-lucide="${icon}"></i>${label}</button>`).join("")}</div><div id="manager-content"></div>`;
  const content=$("#manager-content");
  const dates=`<div class="manager-dates"><label>开始日期<input type="date" id="manager-start" required value="${esc(managerStart)}"></label><label>结束日期<input type="date" id="manager-end" required value="${esc(managerEnd)}"></label><div class="manager-presets"><button type="button" data-period="today">今天</button><button type="button" data-period="week">本周</button><button type="button" data-period="lastweek">上周</button></div></div>`;
  if(managerTab==="history"){
    content.innerHTML=`<form id="history-form"><h2>历史会话采集</h2>${dates}<h3>项目</h3><div class="manager-select">${checkList(catalog.projects.map(p=>({...p,disabled:!!p.capture_excluded})),managerHistoryProjects,"history-project")}</div><div class="manager-command"><span class="muted">本地规则初筛 · 非 AI 分析 · 人工确认录入 · 不消耗模型 token</span><button class="primary" ${managerBusy?"disabled":""}><i data-lucide="scan-search"></i>一键采集</button></div></form><section class="manager-jobs"><h3>采集任务</h3>${management.jobs.map(j=>`<div class="manager-job"><div><strong>${esc(j.start)} 至 ${esc(j.end)}</strong> ${badge(j.state)}<p class="muted">${j.processed} / ${j.total} 条输入 · 新候选 ${j.added} · 跳过 ${j.skipped}</p>${j.error?`<p class="manager-error">${esc(j.error)}</p>`:""}</div>${["排队中","采集中"].includes(j.state)?`<button data-cancel-history="${j.id}" title="取消采集"><i data-lucide="square"></i></button>`:""}</div>`).join("")||'<p class="muted">暂无采集任务</p>'}</section><section class="manager-candidates"><h3>待审阅 · ${management.candidates.filter(c=>c.state==="待确认").length}</h3>${management.candidates.filter(c=>c.state==="待确认").map(c=>{
      const draft=candidateDrafts.get(c.id)||{decision:"create",title:"",description:"",item_id:""};
      const linked=items.filter(i=>i.origin==="local"&&i.project_id===c.project_id&&i.coordination?.thread?.id===c.thread_id);
      const excluded=catalog.projects.find(p=>p.id===c.project_id)?.capture_excluded;
      return `<form class="manager-candidate" data-candidate-form="${c.id}"><div class="evidence-head"><strong>${esc(c.thread_title)}</strong>${badge(excluded?"项目已加白":c.triage)}</div><p class="muted">${esc(catalog.projects.find(p=>p.id===c.project_id)?.name||"项目已移除")} · ${date(c.created_at_ms)}</p>${excluded?'<p class="muted">已停止采集与录入</p>':`<blockquote>${esc(c.excerpt)}</blockquote>`}<div class="manager-candidate-fields"><label>处理方式<select name="decision">${[["create","新增需求"],["update","修改已关联需求"],["archive","归档已关联需求"]].map(([v,l])=>`<option value="${v}" ${draft.decision===v?"selected":""}>${l}</option>`).join("")}</select></label><label>已有需求<select name="item_id"><option value="">选择已确认关联的需求</option>${linked.map(i=>`<option value="${i.id}" ${draft.item_id===i.id?"selected":""}>${esc(titleOf(i))}</option>`).join("")}</select></label><label>需求标题<input name="title" maxlength="160" value="${esc(draft.title)}"></label><label>需求描述<textarea name="description" rows="2" maxlength="4000">${esc(draft.description)}</textarea></label></div><div class="manager-command"><a href="${esc(threadUrl({id:c.thread_id}))}">原会话</a><button type="button" data-ignore-candidate="${c.id}"><i data-lucide="x"></i>忽略</button><button class="primary" ${excluded||managerBusy?"disabled":""}><i data-lucide="inbox"></i>确认录入</button></div></form>`;
    }).join("")||'<p class="muted">暂无待审阅候选</p>'}</section>`;
    $$('[name="history-project"]').forEach(n=>n.onchange=()=>n.checked?managerHistoryProjects.add(n.value):managerHistoryProjects.delete(n.value));
    $("#history-form").onsubmit=e=>{e.preventDefault();managerRun(()=>api("/api/management/history",{start:managerStart,end:managerEnd,project_ids:[...managerHistoryProjects]}),"采集任务已启动");};
    $$("[data-cancel-history]").forEach(b=>b.onclick=()=>managerRun(()=>api(`/api/management/jobs/${b.dataset.cancelHistory}/cancel`,{}),"已取消；已采集候选保留"));
    $$("[data-ignore-candidate]").forEach(b=>b.onclick=()=>managerRun(()=>api(`/api/management/candidates/${b.dataset.ignoreCandidate}`,{decision:"ignore"}),"已忽略"));
    $$("[data-candidate-form]").forEach(f=>{
      const remember=()=>candidateDrafts.set(f.dataset.candidateForm,Object.fromEntries(new FormData(f)));
      f.oninput=remember;f.onchange=remember;
      f.onsubmit=e=>{e.preventDefault();remember();const d=candidateDrafts.get(f.dataset.candidateForm),item=items.find(i=>i.id===d.item_id);
        managerRun(()=>api("/api/management/candidates/"+f.dataset.candidateForm,{...d,version:item?.version}),"已录入需求平台");};
    });
  }
  if(managerTab==="exclusions"){
    content.innerHTML=`<h2>项目采集加白</h2><p class="muted">加白 = 停止自动采集与历史采集 · 已有需求保留</p><div class="exclusion-list">${catalog.projects.map(p=>`<div class="exclusion-row"><strong>${esc(p.name)}</strong><label class="manager-choice"><input type="checkbox" role="switch" aria-label="${esc(p.name)}停止采集" data-exclude-project="${esc(p.id)}" ${p.capture_excluded?"checked":""} ${managerBusy?"disabled":""}><span>${p.capture_excluded?"已加白 · 停止采集":"正常采集"}</span></label></div>`).join("")}</div>`;
    $$("[data-exclude-project]").forEach(n=>n.onchange=()=>{
      const p=catalog.projects.find(p=>p.id===n.dataset.excludeProject);
      managerRun(()=>api("/api/catalog",{id:p.id,revision:p.revision,capture_excluded:n.checked}),n.checked?"已加白，停止采集":"已恢复采集，不自动回填加白期间的输入");
    });
  }
  if(managerTab==="reports"){
    const entries=managerScope==="projects"?catalog.projects:managerScope==="chats"?managerChats.map(c=>({...c,title:`${c.title||"未命名会话"} · ${catalog.projects.find(p=>p.id===c.project_id)?.name||"未关联项目"}`})):items.map(i=>({...i,title:titleOf(i)}));
    content.innerHTML=`<form id="report-form"><h2>进度汇总</h2>${dates}<div class="form-row"><label>报告类型<select id="report-kind">${["日报","周报","自定义报告"].map(v=>`<option ${v===managerKind?"selected":""}>${v}</option>`).join("")}</select></label><label>汇总对象<select id="report-scope">${[["projects","项目"],["chats","会话"],["requirements","需求"]].map(([v,l])=>`<option value="${v}" ${v===managerScope?"selected":""}>${l}</option>`).join("")}</select></label></div>${managerScope==="chats"&&!managerChatsLoaded?'<p class="muted">会话目录未加载</p><button type="button" id="load-report-chats"><i data-lucide="refresh-cw"></i>加载会话</button>':""}<div class="manager-select">${checkList(entries,managerReportIds,"report-object")}</div><div class="manager-command"><span class="muted">期间记录 + 当前状态 · 不自动回读正文</span><button class="primary" ${managerBusy?"disabled":""}><i data-lucide="file-text"></i>生成报告</button></div></form><section class="manager-report-list"><h3>历史报告</h3>${management.reports.map(r=>`<button data-open-report="${r.id}">${esc(r.kind)} · ${esc(r.start)} 至 ${esc(r.end)}</button>`).join("")||'<p class="muted">暂无报告</p>'}</section>${managerReport?`<section class="manager-report"><div class="evidence-head"><h3>${esc(managerReport.kind)}</h3><button id="download-report" title="下载 Markdown"><i data-lucide="download"></i>Markdown</button></div><article class="report-preview">${reportHtml(managerReport.markdown)}</article></section>`:""}`;
    $("#report-kind").onchange=e=>managerKind=e.target.value;
    $("#report-scope").onchange=e=>{managerScope=e.target.value;managerReportIds.clear();renderManagement(true);};
    $$('[name="report-object"]').forEach(n=>n.onchange=()=>n.checked?managerReportIds.add(n.value):managerReportIds.delete(n.value));
    if($("#load-report-chats"))$("#load-report-chats").onclick=()=>managerRun(async()=>{managerChats=await api("/api/management/chats");managerChatsLoaded=true;},"会话目录已加载");
    $("#report-form").onsubmit=e=>{e.preventDefault();managerRun(async()=>{managerReport=await api("/api/management/report",{kind:managerKind,scope:managerScope,ids:[...managerReportIds],start:managerStart,end:managerEnd});},"报告已生成");};
    $$("[data-open-report]").forEach(b=>b.onclick=()=>{managerReport=management.reports.find(r=>r.id===b.dataset.openReport);renderManagement(true);});
    if($("#download-report"))$("#download-report").onclick=()=>{
      const url=URL.createObjectURL(new Blob([managerReport.markdown],{type:"text/markdown;charset=utf-8"})),a=document.createElement("a");
      a.href=url;a.download=`${managerReport.kind}-${managerReport.start}-${managerReport.end}.md`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
    };
  }
  $$("[data-manager-tab]").forEach(b=>b.onclick=()=>{managerTab=b.dataset.managerTab;renderManagement(true);});
  if($("#manager-start"))$("#manager-start").onchange=e=>managerStart=e.target.value;
  if($("#manager-end"))$("#manager-end").onchange=e=>managerEnd=e.target.value;
  $$("[data-period]").forEach(b=>b.onclick=()=>{
    const today=localDay(new Date()),d=new Date(today+"T00:00:00+08:00");
    if(b.dataset.period==="today")managerStart=managerEnd=today;
    else{
      const iso=new Date(today+"T12:00:00Z"),offset=(iso.getUTCDay()+6)%7+(b.dataset.period==="lastweek"?7:0);
      d.setTime(d.getTime()-offset*86400000);managerStart=localDay(d);
      managerEnd=b.dataset.period==="lastweek"?localDay(new Date(d.getTime()+6*86400000)):today;
    }
    renderManagement(true);
  });
  icons();
}
async function managerRun(work,message){
  if(managerBusy)return;
  managerBusy=true;
  $$("#management button").forEach(b=>b.disabled=true);
  try{await work();await load();toast(message);}
  catch(e){toast(e.message,true);}
  finally{managerBusy=false;managerSignature="";renderManagement(true);}
}
setInterval(async()=>{
  if(view!=="management"||managerBusy||!management.jobs.some(j=>["排队中","采集中"].includes(j.state)))return;
  try{management=await api("/api/management");renderManagement();}catch(e){toast(e.message,true);}
},1500);
