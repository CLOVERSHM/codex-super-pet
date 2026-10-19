let token, snapshot={cursor:0,sessions:[]}, selected="", approvalRevision=null;
const $=s=>document.querySelector(s);
const esc=s=>String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
async function api(path,body){
  const response=await fetch(path,{method:body?"POST":"GET",headers:{"Authorization":"Bearer "+token,"Content-Type":"application/json"},...(body?{body:JSON.stringify(body)}:{})});
  const value=await response.json();if(!response.ok)throw new Error(value.error);return value;
}
function render(){
  if(!snapshot.sessions.some(s=>s.id===selected))selected=snapshot.sessions[0]?.id||"";
  $("#sessions").innerHTML=snapshot.sessions.map(s=>`<option value="${esc(s.id)}">${esc(s.title)}</option>`).join("")||'<option value="">暂无会话</option>';
  $("#sessions").value=selected;
  const session=snapshot.sessions.find(s=>s.id===selected);
  $("#projection").innerHTML=session?`<div class="code">${esc(session.id)}</div><p class="revision">任务版本 ${session.revision}</p><h3>目标</h3><p class="objective">${esc(session.objective)||"未设置"}</p><h3>用户输入</h3>${session.inputs.map(i=>`<p class="input-text">${esc(i.text)}</p>`).join("")||'<p class="empty">暂无输入</p>'}`:'<p class="empty">暂无会话</p>';
  $("#receipts").innerHTML=(session?.commands||[]).filter(c=>c.receipt).map(c=>`<p>合成宿主已接收<div class="code">${esc(c.receipt.thread_id)}<br>${esc(c.receipt.command_id)}</div></p>`).join("")||'<p class="empty">暂无回执</p>';
  $("#commands").innerHTML=(session?.commands||[]).map(c=>`<div class="command" data-command="${esc(c.id)}"><strong>${esc(c.state)}</strong> · 版本 ${c.revision}${c.stale?'<span class="stale"> · 审批已失效</span>':""}<div class="code">${esc(c.id)}</div><p>${esc(c.step)}</p>${c.state==="pending"?`<button data-send="${esc(c.id)}" ${c.stale?"disabled":""} title="发送到合成原会话"><i data-lucide="send"></i>发送到原会话</button>`:""}</div>`).join("");
  $("#approval button").disabled=!session;
  document.querySelectorAll("[data-send]").forEach(b=>b.onclick=()=>act(async()=>{await api("/api/dispatch",{command_id:b.dataset.send});}));
  lucide.createIcons();
}
function acceptSnapshot(value){if(value.cursor>snapshot.cursor||!$("#projection").hasChildNodes()){snapshot=value;render();}}
async function refresh(){acceptSnapshot(await api("/api/snapshot"));}
async function act(fn){$("#error").textContent="";try{await fn();await refresh();}catch(e){$("#error").textContent=e.message;}}
$("#sessions").onchange=()=>{selected=$("#sessions").value;approvalRevision=null;render();};
$("#approval").addEventListener("input",()=>{if(approvalRevision===null)approvalRevision=snapshot.sessions.find(s=>s.id===selected)?.revision;});
$("#create").onsubmit=e=>{e.preventDefault();act(async()=>{const result=await api("/api/synthetic/session",Object.fromEntries(new FormData(e.target)));selected=result.id;approvalRevision=null;e.target.reset();});};
for(const kind of ["input","goal"])$("#"+kind).onsubmit=e=>{e.preventDefault();act(async()=>{await api("/api/synthetic/event",{thread_id:selected,kind,value:new FormData(e.target).get("value")});e.target.reset();});};
$("#approval").onsubmit=e=>{e.preventDefault();act(async()=>{
  const session=snapshot.sessions.find(s=>s.id===selected);
  await api("/api/approve",{thread_id:selected,revision:approvalRevision??session.revision,...Object.fromEntries(new FormData(e.target))});
  approvalRevision=null;e.target.reset();
});};
(async()=>{
  try{
    token=(await api("/api/session")).token;await refresh();$("#connection").textContent="实时连接";
    while(true){
      try{acceptSnapshot(await api("/api/snapshot?after="+snapshot.cursor+"&wait=20"));$("#connection").textContent="实时连接";}
      catch(e){$("#connection").textContent="连接中断";await new Promise(r=>setTimeout(r,2000));}
    }
  }catch(e){$("#error").textContent=e.message;}
})();
