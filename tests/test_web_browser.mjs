// End-to-end dashboard checks using Node 22+ and installed Chrome, with no npm dependencies.
import assert from "node:assert/strict";
import {spawn} from "node:child_process";
import {existsSync} from "node:fs";
import {mkdtemp, mkdir, rm, writeFile} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {fileURLToPath} from "node:url";

const root = fileURLToPath(new URL("../", import.meta.url));
const chrome = process.env.CHROME || ["/opt/google/chrome/chrome", "/usr/bin/chromium", "/usr/bin/google-chrome"].find(existsSync);
assert.ok(chrome, "Set CHROME to an installed Chrome/Chromium executable.");
const temp = await mkdtemp(join(tmpdir(), "tacto-dashboard-")), reports = join(temp, "reports");
await mkdir(reports);
const action = {supplier_id:"sup:A", action:"send", term_sheets:[{unit_price:10, payment_days:60}]};
const step = {route:"script", reason:"bounded_concession", decision_wall_s:.003, budget_s:25, model_attempts:0, wave_seconds_left:460, thread_seconds_left:50, proposed_action:action, sent_action:action, standing_prices:[{supplier_id:"sup:A",unit_price:12}], supplier_reply:{supplier_id:"sup:A",status:"offer",unit_price:11}};
const current = {status:"completed",config:{scoring_version:2,waves:5,wave_seconds:480,workers:8},matches:Array.from({length:35},(_,i) => ({variant_id:i%2 ? "close" : "scripts", scenario:i%2 ? "simple_price" : "rfq", seed:i, wave:i%5+1, case_id:`case-${i}`, valid:i!==0, deal:i!==0, cost_eur:i===0 ? null : 1200, reference_cost_eur:1000, regret_eur:i===0 ? null : 200, score:i===0 ? 0 : .8, terms:i===0 ? {} : {unit_price:11,payment_days:60}, model_attempts:0, virtual_elapsed_s:20+i, supplier_delay_s:20, thread_timeouts:0, invalid_moves:0, end_reason:i===0 ? "no_deal" : "agreement", decisions:[step,{...step,reason:"deadline_close",deadline_override:true,sent_action:{supplier_id:"sup:A",action:"walk_away"},supplier_reply:{status:"walked_away"}}]}))};
await writeFile(join(reports,"current.json"),JSON.stringify(current));
await writeFile(join(reports,"historical.json"),JSON.stringify({excluded_from_comparison:true,matches:[{case_id:"old-case",variant_id:"old",scenario:"legacy",valid:true,deal:true,cost_eur:10,score:.7,decisions:[{route:"fast",reason:"semantic_decision",sent_action:action}]}]}));
await writeFile(join(reports,"hosted.json"),JSON.stringify({source:"hosted_practice",team_name:"winsortf",status:"completed",models:{fast:"configured-model"},config:{set:"practice",workers:4},matches:[{...current.matches[1],wave:undefined,seed:undefined,elapsed_s:90.5,rounds:4,replay_url:"https://arena.tacto.ai/replay/session-1",cost_breakdown:{price:100,fallback_risk:5}}]}));
const malicious = '<img src=x onerror="window.injected=true">';
const imported = join(temp,"imported.json"), invalid = join(temp,"invalid.json");
await writeFile(imported,JSON.stringify({matches:[{case_id:malicious,scenario:malicious,variant_id:malicious,terms:{unit_price:malicious},decisions:[{reason:malicious,route:malicious}]}]}));
await writeFile(invalid,'{"not_a_report":true}');
const env = {PATH:"/usr/bin:/bin",LANG:"C.UTF-8",PYTHONDONTWRITEBYTECODE:"1"};
const server = spawn(join(root,".venv/bin/python"),["-m","tacto_negotiator.web","--port","0","--reports",reports],{cwd:temp,env,stdio:["ignore","pipe","pipe"]});
let browser, socket, session, next = 0;
const pending = new Map(), exceptions = [], requests = [], consoleErrors = [];
function endpoint(child, stream, pattern) {
  return new Promise((resolve,reject) => {
    let output = "";
    const timer = setTimeout(() => reject(new Error(`Startup timeout: ${output}`)),15000);
    child.once("error", error => {clearTimeout(timer);reject(error);});
    child.once("exit", code => {clearTimeout(timer);reject(new Error(`Early exit ${code}: ${output}`));});
    child[stream].on("data", chunk => {output+=chunk; const match=output.match(pattern); if(match){clearTimeout(timer);resolve(match[1]);}});
  });
}
function command(method,params={},scoped=true) {
  const id=++next;
  return new Promise((resolve,reject) => {
    const timeout=setTimeout(() => {pending.delete(id);reject(new Error(`Timeout: ${method}`));},10000);
    pending.set(id,{resolve,reject,timeout});
    socket.send(JSON.stringify({id,method,params,...(scoped&&session ? {sessionId:session} : {})}));
  });
}
async function evaluate(expression) {
  const result=await command("Runtime.evaluate",{expression,returnByValue:true,awaitPromise:true});
  if(result.exceptionDetails) throw new Error(result.exceptionDetails.exception?.description || result.exceptionDetails.text);
  return result.result.value;
}
async function until(expression) {
  for(let i=0;i<150;i++) {if(await evaluate(expression)) return; await new Promise(resolve=>setTimeout(resolve,30));}
  const context = await evaluate("({url:location.href,status:document.querySelector('#status')?.textContent,body:document.body?.textContent.slice(0,300)})");
  throw new Error(`Condition failed: ${expression}\n${JSON.stringify({context,exceptions,consoleErrors})}`);
}
async function select(id,value) {await evaluate(`document.getElementById(${JSON.stringify(id)}).value=${JSON.stringify(value)}; document.getElementById(${JSON.stringify(id)}).dispatchEvent(new Event('change'))`);}
async function upload(path) {
  const {root:document}=await command("DOM.getDocument");
  const {nodeId}=await command("DOM.querySelector",{nodeId:document.nodeId,selector:"#import"});
  await command("DOM.setFileInputFiles",{nodeId,files:[path]});
}
try {
  const port=await endpoint(server,"stdout",/http:\/\/localhost:(\d+)/);
  browser=spawn(chrome,["--headless=new","--no-sandbox","--disable-gpu","--disable-dev-shm-usage","--disable-background-networking","--disable-component-update","--disable-sync","--disable-extensions","--no-first-run","--no-default-browser-check","--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1","--remote-debugging-port=0",`--user-data-dir=${join(temp,"profile")}`,"about:blank"],{env,stdio:["ignore","ignore","pipe"]});
  socket=new WebSocket(await endpoint(browser,"stderr",/DevTools listening on (ws:\/\/\S+)/));
  await new Promise((resolve,reject) => {socket.addEventListener("open",resolve,{once:true});socket.addEventListener("error",reject,{once:true});});
  socket.addEventListener("message",event => {
    const message=JSON.parse(event.data);
    if(message.id&&pending.has(message.id)) {const p=pending.get(message.id);pending.delete(message.id);clearTimeout(p.timeout);if(message.error)p.reject(new Error(JSON.stringify(message.error)));else p.resolve(message.result);}
    if(message.method==="Runtime.exceptionThrown") exceptions.push(message.params.exceptionDetails);
    if(message.method==="Network.requestWillBeSent") requests.push(message.params.request.url);
    if(message.method==="Log.entryAdded"&&message.params.entry.level==="error") consoleErrors.push(message.params.entry.text);
  });
  const {targetId}=await command("Target.createTarget",{url:"about:blank"},false);
  session=(await command("Target.attachToTarget",{targetId,flatten:true},false)).sessionId;
  for(const domain of ["Runtime","Page","Network","Log"]) await command(`${domain}.enable`);
  await command("Emulation.setDeviceMetricsOverride",{width:1440,height:1080,deviceScaleFactor:1,mobile:false});
  await command("Page.navigate",{url:`http://127.0.0.1:${port}`});
  await until("document.querySelector('#status')?.textContent.includes('35 matches')");
  assert.equal(await evaluate("document.querySelector('#report').value"),"current.json");
  assert.equal(await evaluate("document.querySelector('#notice').hidden"),true);
  assert.equal(await evaluate("document.querySelectorAll('#matches tr').length"),30);
  assert.equal(await evaluate("document.querySelectorAll('.metric strong')[2].textContent"),"77.7");
  assert.equal(await evaluate("document.querySelector('#scores [data-value=\"close\"] .bar-value').textContent"),"80");
  assert.ok(await evaluate("document.querySelector('#detail').textContent.includes('Unknown')"),"Null costs must not become zero");
  assert.ok(await evaluate("document.querySelectorAll('.quote-chart circle').length > 0"));
  await evaluate("document.querySelector('#next').click()");
  assert.equal(await evaluate("document.querySelectorAll('#matches tr').length"),5);
  await evaluate("document.querySelector('#previous').click(); document.querySelector('[data-step=\"1\"]').click()");
  assert.ok(await evaluate("document.querySelector('#detail').textContent.includes('Deadline override')"));
  assert.ok(await evaluate("document.querySelector('#detail').textContent.includes('walk_away') && document.querySelector('#detail').textContent.includes('payment_days')"));
  await evaluate("document.querySelector('#scores [data-value=\"scripts\"]').click()");
  assert.equal(await evaluate("document.querySelector('#strategy').value"),"scripts");
  assert.ok(await evaluate("document.querySelector('#match-count').textContent.includes('18')"));
  await select("wave","2");
  assert.ok(await evaluate("[...document.querySelectorAll('.case-button')].every(e=>e.textContent.includes('wave 2'))"));
  await evaluate("document.querySelector('#refresh').click()");
  await until("!document.querySelector('#refresh').disabled");
  assert.equal(await evaluate("document.querySelector('#wave').value"),"2");
  await evaluate("document.querySelector('#reset').click(); document.querySelector('#search').value='no-such-case'; document.querySelector('#search').dispatchEvent(new Event('input'))");
  assert.ok(await evaluate("document.querySelector('#matches').textContent.includes('No matches')"));
  await evaluate("document.querySelector('#reset').click()");
  await select("scenario","simple_price");
  await select("sort","time");
  await evaluate("document.querySelector('.case-button').focus()");
  await command("Input.dispatchKeyEvent",{type:"keyDown",key:"Enter",code:"Enter",windowsVirtualKeyCode:13});
  assert.ok(await evaluate("document.querySelector('#detail').textContent.includes('Simple price') || document.querySelector('#detail').textContent.includes('simple price')"));
  await select("report","historical.json");
  await until("document.querySelector('#status')?.textContent.includes('historical.json ·')");
  assert.ok(await evaluate("document.querySelector('#notice').textContent.includes('Historical evaluator') && document.querySelector('#notice').textContent.includes('Excluded')"));
  assert.ok(await evaluate("document.querySelector('#detail').textContent.includes('no standing-quote snapshots')"));
  await select("report","hosted.json");
  await until("document.querySelector('#status')?.textContent.includes('hosted.json ·')");
  assert.ok(await evaluate("document.querySelector('#notice').textContent.includes('Hosted practice') && !document.querySelector('#notice').textContent.includes('Historical evaluator')"));
  assert.ok(await evaluate("document.querySelector('#run-meta').textContent.includes('winsortf') && document.querySelector('#wave').disabled"));
  assert.ok(await evaluate("document.querySelector('#detail').textContent.includes('Wall time 90.5s') && !document.querySelector('#detail').textContent.includes('seed')"));
  assert.equal(await evaluate("document.querySelector('#detail a').href"),"https://arena.tacto.ai/replay/session-1");
  assert.ok(await evaluate("document.querySelector('#detail').textContent.includes('fallback risk')"));
  assert.equal(await evaluate("replayLink('javascript:alert(1)') + replayLink('https://attacker.example/replay/a') + replayLink('https://arena.tacto.ai@attacker.example/replay/a')"),"");
  await upload(imported);
  await until("document.querySelector('#status')?.textContent.includes('opened in this browser only')");
  assert.ok(await evaluate("document.querySelector('#detail').textContent.includes('<img')"));
  assert.equal(await evaluate("document.querySelectorAll('.metric strong')[3].textContent"),"—","Missing attempt counts stay unknown");
  assert.equal(await evaluate("Boolean(window.injected) || !!document.querySelector('#detail img')"),false);
  await upload(invalid);
  await until("document.querySelector('#status')?.textContent.includes('Could not open JSON')");
  assert.ok(await evaluate("document.querySelector('#detail').textContent.includes('<img')"),"Bad imports preserve the previous run");
  await command("Emulation.setDeviceMetricsOverride",{width:390,height:844,deviceScaleFactor:1,mobile:true});
  assert.ok(await evaluate("document.documentElement.scrollWidth <= innerWidth"),"Mobile page must not overflow");
  await select("report","current.json");
  await until("document.querySelector('#status')?.textContent.includes('35 matches')");
  await writeFile(join(reports,"empty.json"),'{"config":{"scoring_version":2},"matches":[]}');
  await evaluate("document.querySelector('#refresh').click()");
  await until("!document.querySelector('#refresh').disabled");
  await select("report","empty.json");
  await until("document.querySelector('#status')?.textContent.includes('0 matches')");
  assert.ok(await evaluate("document.querySelector('#matches').textContent.includes('No matches')"));
  assert.equal(exceptions.length,0,JSON.stringify(exceptions));
  assert.equal(consoleErrors.length,0,consoleErrors.join("\n"));
  assert.ok(requests.every(url=>!/^https?:/.test(url)||new URL(url).hostname==="127.0.0.1"),"No remote requests");
  if(process.env.DASHBOARD_SCREENSHOT) {
    await command("Emulation.setDeviceMetricsOverride",{width:1440,height:1080,deviceScaleFactor:1,mobile:false});
    if(process.env.DASHBOARD_URL) {await command("Page.navigate",{url:process.env.DASHBOARD_URL});await until("document.querySelector('#status')?.textContent.includes('saved local report')");}
    else {await select("report","current.json");await until("document.querySelector('#status')?.textContent.includes('35 matches')");}
    await evaluate("scrollTo(0,0)");
    await writeFile(process.env.DASHBOARD_SCREENSHOT,Buffer.from((await command("Page.captureScreenshot",{format:"png"})).data,"base64"));
  }
  console.log("Dashboard browser checks passed: reports, charts, filters, pagination, keyboard, traces, deadlines, legacy data, local import, escaping, errors, mobile and loopback-only requests.");
} finally {
  if(socket?.readyState===WebSocket.OPEN) {try {await command("Browser.close",{},false);}catch {} socket.close();}
  browser?.kill(); server.kill();
  await rm(temp,{recursive:true,force:true,maxRetries:10,retryDelay:100});
}
