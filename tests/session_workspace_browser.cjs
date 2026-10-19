const {chromium}=require("playwright"),{spawn}=require("child_process");
const fs=require("fs"),os=require("os"),path=require("path"),assert=require("assert");
const root=path.resolve(__dirname,".."),data=fs.mkdtempSync(path.join(os.tmpdir(),"session-workspace-"));
const server=spawn("python3",[path.join(root,"tests/management_ui_server.py"),"--port","8897","--data",data,"--source",path.join(data,"empty")],{env:{...process.env,CODEX_HOME:data}});
const ids=["00000000-0000-4000-8000-000000000001","00000000-0000-4000-8000-000000000002","00000000-0000-4000-8000-000000000003"];
const projects=[{id:"p1",name:"合成甲",revision:1,session_skills:[]},{id:"p2",name:"合成乙",revision:1,session_skills:[]}];
const sessions=[
  {id:ids[0],project_id:"p1",title:"数据库修复",state:"blocked",updated_at_ms:10000,report:{workflow:"开发",stage:"测试",summary:"隔离测试失败",reason:"合成失败",next_action:"修复并复测"}},
  {id:ids[1],project_id:"p2",title:"接口开发",state:"completed",updated_at_ms:20000,report:{workflow:"开发",stage:"完成",summary:"合成已验收"}},
  {id:ids[2],project_id:"p1",title:"文档更新",state:"in_progress",updated_at_ms:30000,report:{workflow:"文档",stage:"编写",summary:"合成进展"}}
];
let browser;
(async()=>{
  let runtime;
  for(let n=0;n<100;n++){
    if(fs.existsSync(path.join(data,"runtime.json"))){runtime=JSON.parse(fs.readFileSync(path.join(data,"runtime.json")));break;}
    await new Promise(r=>setTimeout(r,100));
  }
  assert(runtime,"synthetic server starts");
  browser=await chromium.launch({headless:true,executablePath:process.env.CHROME_PATH||chromium.executablePath()});
  const page=await browser.newPage({viewport:{width:1360,height:900}}),errors=[];
  let collected;
  page.on("pageerror",e=>errors.push(e.message));
  await page.route("**/api/catalog",route=>route.request().frame()===page.mainFrame()?route.fulfill({json:{projects,collections:[]}}):route.continue());
  await page.route("**/api/board",route=>route.fulfill({json:{sessions,health:"运行中",last_checked:0,interval_seconds:600}}));
  await page.route("**/api/management/history",route=>{
    collected=route.request().postDataJSON();
    return route.fulfill({json:{id:"synthetic-job",state:"排队中"}});
  });
  await page.goto(runtime.url+"/");
  await page.locator("#welcome-dialog[open]").waitFor();
  assert.equal(await page.locator("#welcome-projects input:checked").count(),2);
  const range=await page.evaluate(()=>({start:document.querySelector("#welcome-start").value,end:document.querySelector("#welcome-end").value}));
  assert.equal(Math.round((Date.parse(range.end)-Date.parse(range.start))/86400000),6);
  fs.mkdirSync(path.join(root,"docs/images"),{recursive:true});
  await page.screenshot({path:path.join(root,"docs/images/first-run.png")});
  await page.locator("#welcome-start").fill("2026-10-10");
  await page.locator("#welcome-end").fill("2026-10-12");
  await page.locator("#welcome-projects input").nth(1).uncheck();
  await page.locator("#welcome-next").click();
  assert.deepEqual(collected,{start:"2026-10-10",end:"2026-10-12",project_ids:["p1"]});
  for(let n=1;n<6;n++)await page.locator("#welcome-next").click();
  assert(await page.locator("#welcome-dialog").isHidden());
  assert(await page.locator("#management-frame").isVisible());
  await page.locator('[data-workspace="sessions"]').click();
  await page.reload();
  assert(await page.locator("#welcome-dialog").isHidden());
  await page.locator("#show-guide").click();
  await page.locator("#welcome-dialog[open]").waitFor();
  await page.locator("#welcome-skip").click();
  await page.locator("#sessions tr[data-session]").first().waitFor();
  await page.screenshot({path:path.join(root,"docs/images/session-board.png")});
  assert.equal(await page.locator("#sessions tr[data-session]").count(),3);
  assert.equal(await page.locator("#sessions tr[data-session]").first().getAttribute("data-session"),ids[2]);
  assert((await page.locator('[data-status="blocked"]').innerText()).includes("1"));
  await page.locator('[data-status="blocked"]').click();
  assert.equal(await page.locator("#sessions tr[data-session]").count(),1);
  await page.locator("#sessions tr[data-session]").click();
  assert((await page.locator("#detail").innerText()).includes("合成失败"),JSON.stringify({detail:await page.locator("#detail").innerText(),state:await page.evaluate(()=>({drawerOpen,selected})),errors}));
  await page.keyboard.press("Escape");
  assert(await page.locator("#detail").isHidden());
  await page.locator('[data-status=""]').click();
  await page.locator("#project-filter").selectOption("p2");
  assert.equal(await page.locator("#sessions tr[data-session]").count(),1);
  assert.equal(await page.locator(".action-cell a").getAttribute("href"),`codex://threads/${ids[1]}`);
  await page.locator("#project-filter").selectOption("");
  await page.locator('[data-sort="title"]').click();
  assert.equal(await page.locator(".session-table th").first().getAttribute("aria-sort"),"ascending");
  await page.locator('[data-sort="title"]').click();
  assert.equal(await page.locator(".session-table th").first().getAttribute("aria-sort"),"descending");
  await page.locator('[data-sort="project"]').click();
  assert.equal(await page.locator(".session-table th").nth(2).getAttribute("aria-sort"),"ascending");
  await page.locator('[data-sort="state"]').click();
  assert.equal(await page.locator(".session-table th").nth(1).getAttribute("aria-sort"),"ascending");
  await page.locator("#search").fill("文档");
  assert.equal(await page.locator("#sessions tr[data-session]").count(),1);
  await page.locator("#search").fill("");
  const url=page.url();
  await page.locator('[data-workspace="management"]').click();
  await page.frameLocator("#management-frame").locator('[data-manager-tab="history"]').waitFor();
  assert(await page.frameLocator("#management-frame").locator(".sidebar").isHidden());
  await page.frameLocator("#management-frame").locator("#manager-start").fill("2026-10-16");
  fs.mkdirSync(path.join(root,"artifacts"),{recursive:true});
  await page.screenshot({path:path.join(root,"artifacts/session-management-desktop.png")});
  await page.screenshot({path:path.join(root,"docs/images/management.png")});
  assert.equal(page.url(),url);
  assert(await page.locator("#session-workspace").isHidden());
  await page.locator('[data-workspace="ledger"]').click();
  await page.frameLocator("#ledger-frame").locator("#view-title").waitFor();
  assert(await page.frameLocator("#ledger-frame").locator(".sidebar .brand").isHidden());
  await page.screenshot({path:path.join(root,"artifacts/session-ledger-desktop.png")});
  assert.equal(page.url(),url);
  await page.locator('[data-workspace="management"]').click();
  assert.equal(await page.locator("#management-frame").getAttribute("src"),"/legacy?embedded=1#management");
  assert.equal(await page.frameLocator("#management-frame").locator("#manager-start").inputValue(),"2026-10-16");
  await page.locator('[data-workspace="sessions"]').click();
  await page.locator('[data-project="p1"]').click();
  assert.equal(await page.locator("#sessions tr[data-session]").count(),2);
  for(const [name,width,height] of [["desktop",1360,900],["mobile",390,844]]){
    await page.setViewportSize({width,height});
    await page.screenshot({path:path.join(root,`artifacts/session-workspace-${name}.png`)});
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),`${name} horizontal overflow`);
    if(name==="mobile")assert(await page.locator(".action-cell a").first().isVisible(),"mobile Codex action visible");
  }
  assert.deepEqual(errors,[]);
  console.log("Session workspace: tabs, table, detail, sort/filter, Codex link, embedded panels and responsive layout passed");
})().catch(e=>{console.error(e);process.exitCode=1}).finally(async()=>{
  if(browser)await browser.close();
  server.kill("SIGTERM");
  if(server.exitCode===null)await new Promise(r=>server.once("exit",r));
  fs.rmSync(data,{recursive:true,force:true});
});
