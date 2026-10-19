const {chromium}=require("playwright");
const {spawn}=require("child_process");
const assert=require("assert");
const path=require("path");
const fs=require("fs");
const root=path.resolve(__dirname,"..");
const output=path.join(root,"artifacts");
fs.mkdirSync(output,{recursive:true});
const server=spawn("python3",[path.join(root,"sync_prototype.py"),"--port","0"]);
let browser;
const checks=[];
async function check(name,fn){await fn();checks.push(name);}
(async()=>{
  const url=await new Promise((resolve,reject)=>{
    let text="";
    server.stdout.on("data",chunk=>{text+=chunk;const match=text.match(/http:\/\/127\.0\.0\.1:\d+(?=\s)/);if(match)resolve(match[0]);});
    server.once("exit",code=>reject(new Error("prototype exited: "+code)));
    server.once("error",reject);
  });
  browser=await chromium.launch({headless:true,executablePath:process.env.CHROME_PATH||chromium.executablePath()});
  const host=await browser.newPage({viewport:{width:1440,height:960}});
  const web=await browser.newPage({viewport:{width:1440,height:960}});
  const errors=[];
  for(const page of [host,web])page.on("pageerror",e=>{errors.push(e.message);console.error(e.message);});
  await Promise.all([host.goto(url),web.goto(url)]);
  assert(await host.locator("#connection").count(),await host.locator("body").innerText());
  await host.waitForFunction(()=>document.querySelector("#connection")?.textContent==="实时连接");
  await host.locator("#create input").fill("合成双向验收");
  await host.locator("#create button").click();
  await check("new session appears in second page",async()=>{
    await web.locator("#sessions option").filter({hasText:"合成双向验收"}).waitFor({state:"attached"});
  });
  await host.locator("#input textarea").fill("新的任务 <script>not executed</script>");
  await host.locator("#input button").click();
  await check("submitted input automatically projected",async()=>{
    await web.locator(".input-text").filter({hasText:"新的任务"}).waitFor();
    assert.equal(await web.locator("#projection script").count(),0);
  });
  await host.locator("#goal textarea").fill("合成目标A");
  await host.locator("#goal button").click();
  await check("goal automatically projected",async()=>{
    await web.locator(".objective").getByText("合成目标A",{exact:true}).waitFor();
  });
  await web.locator("#approval textarea").fill("仅合成只读验收");
  await web.locator("#approval input").fill("人工批准");
  await web.locator("#approval button").click();
  await web.locator("[data-send]").waitFor();
  await host.locator("#goal textarea").fill("合成目标B");
  await host.locator("#goal button").click();
  await check("changed goal invalidates pending approval",async()=>{
    await web.locator(".stale").waitFor();
    assert(await web.locator("[data-send]").isDisabled());
  });
  await web.locator("#approval textarea").fill("合成新版本只读验收");
  await web.locator("#approval input").fill("人工重新批准");
  await web.locator("#approval button").click();
  const active=web.locator("[data-send]:not([disabled])");
  await active.waitFor();
  await active.click();
  await check("web approval received by original synthetic session",async()=>{
    await host.locator("#receipts").getByText("合成宿主已接收").waitFor();
    const selected=await host.locator("#sessions").inputValue();
    assert((await host.locator("#receipts").innerText()).includes(selected));
  });
  await check("command echo does not create another user task",async()=>{
    assert.equal(await web.locator(".input-text").count(),1);
    assert(!(await web.locator("#projection").innerText()).includes("[delivery-command:"));
  });
  await web.reload();
  await check("reload recovers snapshot and receipt",async()=>{
    await web.locator("#receipts").getByText("合成宿主已接收").waitFor();
  });
  await check("desktop non-overlapping layout",async()=>{
    assert(await web.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  });
  await web.screenshot({path:path.join(output,"sync-desktop.png"),fullPage:true});
  await web.setViewportSize({width:390,height:844});
  await check("mobile non-overlapping layout",async()=>{
    assert(await web.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  });
  await web.screenshot({path:path.join(output,"sync-mobile.png")});
  await web.locator(".web").scrollIntoViewIfNeeded();
  await check("mobile sections have distinct stable bounds",async()=>{
    const bounds=await web.evaluate(()=>{
      const host=document.querySelector(".host").getBoundingClientRect();
      const projection=document.querySelector(".web").getBoundingClientRect();
      return {bottom:host.bottom,top:projection.top};
    });
    assert(bounds.top>=bounds.bottom);
  });
  await web.locator("#approval").scrollIntoViewIfNeeded();
  await web.screenshot({path:path.join(output,"sync-mobile-approval.png")});
  await check("explicit synthetic status and loaded visual asset",async()=>{
    await web.getByText("真实 Codex 桌面：未连接",{exact:true}).waitFor();
    assert(await web.locator("header img").evaluate(e=>e.complete&&e.naturalWidth>0));
  });
  await check("no JavaScript errors",async()=>assert.deepEqual(errors,[]));
  fs.writeFileSync(path.join(output,"sync-browser-report.json"),JSON.stringify({passed:true,checks},null,2));
  console.log(JSON.stringify({passed:true,checks},null,2));
})().catch(e=>{console.error(e);process.exitCode=1;}).finally(async()=>{
  if(browser)await browser.close();
  const exited=new Promise(resolve=>server.once("exit",resolve));
  if(server.exitCode===null){server.kill("SIGTERM");await exited;}
});
