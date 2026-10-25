// Local UI checks with Node 22+ and an installed Chrome; no npm dependencies or remote services.
import assert from "node:assert/strict";
import {spawn} from "node:child_process";
import {existsSync} from "node:fs";
import {mkdtemp, rm, writeFile} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {fileURLToPath, pathToFileURL} from "node:url";

const chrome = process.env.CHROME || ["/opt/google/chrome/chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser", "/usr/bin/google-chrome"].find(existsSync);
assert.ok(chrome, "Set CHROME to an installed Chrome/Chromium executable.");
const profile = await mkdtemp(join(tmpdir(), "tacto-atlas-browser-"));
const browser = spawn(chrome, ["--headless=new", "--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage", "--disable-background-networking", "--disable-component-update", "--disable-sync", "--disable-extensions", "--no-first-run", "--no-default-browser-check", "--host-resolver-rules=MAP * ~NOTFOUND", "--remote-debugging-port=0", `--user-data-dir=${profile}`, "about:blank"], {env: {PATH: "/usr/bin:/bin", LANG: "C.UTF-8"}, stdio: ["ignore", "ignore", "pipe"]});
let socket, next = 0, session, stderr = "";
const pending = new Map(), exceptions = [], requests = [];

function command(method, params = {}, scoped = true) {
  const id = ++next;
  return new Promise((resolve, reject) => {
    const timeout = setTimeout(() => { pending.delete(id); reject(new Error(`Timeout: ${method}`)); }, 10000);
    pending.set(id, {resolve, reject, timeout});
    socket.send(JSON.stringify({id, method, params, ...(scoped && session ? {sessionId: session} : {})}));
  });
}

async function evaluate(expression) {
  const result = await command("Runtime.evaluate", {expression, returnByValue: true, awaitPromise: true});
  if (result.exceptionDetails) throw new Error(result.exceptionDetails.exception?.description || result.exceptionDetails.text);
  return result.result.value;
}

async function until(expression) {
  for (let i = 0; i < 100; i++) {
    if (await evaluate(expression)) return;
    await new Promise(resolve => setTimeout(resolve, 30));
  }
  throw new Error(`Condition failed: ${expression}`);
}

async function select(id) {
  await evaluate(`location.hash = '#file=' + encodeURIComponent(${JSON.stringify(id)})`);
  await until(`document.querySelector('#inspector .file-path')?.textContent.includes(${JSON.stringify(id)})`);
}

try {
  const endpoint = await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(`Chrome did not start: ${stderr.slice(-1500)}`)), 15000);
    browser.once("error", error => { clearTimeout(timer); reject(error); });
    browser.once("exit", code => { clearTimeout(timer); reject(new Error(`Chrome exited ${code}: ${stderr.slice(-1500)}`)); });
    browser.stderr.on("data", data => {
      stderr += data.toString();
      const match = stderr.match(/DevTools listening on (ws:\/\/\S+)/);
      if (match) { clearTimeout(timer); resolve(match[1]); }
    });
  });
  socket = new WebSocket(endpoint);
  await new Promise((resolve, reject) => { socket.addEventListener("open", resolve, {once: true}); socket.addEventListener("error", reject, {once: true}); });
  socket.addEventListener("message", event => {
    const message = JSON.parse(event.data);
    if (message.id && pending.has(message.id)) {
      const entry = pending.get(message.id); pending.delete(message.id); clearTimeout(entry.timeout);
      if (message.error) entry.reject(new Error(JSON.stringify(message.error)));
      else entry.resolve(message.result);
    }
    if (message.method === "Runtime.exceptionThrown") exceptions.push(message.params.exceptionDetails);
    if (message.method === "Network.requestWillBeSent") requests.push(message.params.request.url);
  });
  const target = await command("Target.createTarget", {url: "about:blank"}, false);
  session = (await command("Target.attachToTarget", {targetId: target.targetId, flatten: true}, false)).sessionId;
  await command("Runtime.enable");
  await command("Page.enable");
  await command("Network.enable");
  await command("Network.setBlockedURLs", {urls: ["http://*", "https://*"]});
  await command("Emulation.setDeviceMetricsOverride", {width: 1440, height: 1080, deviceScaleFactor: 1, mobile: false});
  const url = pathToFileURL(fileURLToPath(new URL("index.html", import.meta.url))).href;
  await command("Page.navigate", {url});
  await until("document.querySelector('#guide h1')?.textContent === 'Your first run, from start to finish'");
  assert.equal(await evaluate("document.querySelector('[data-tab=\"BEGINNER_GUIDE.md\"]').getAttribute('aria-current')"), "page");
  assert.ok(await evaluate("document.querySelector('#guide').textContent.includes('30 matches')"));
  await evaluate("document.querySelector('#guide-toc a:nth-of-type(4)').click()");
  await until("document.activeElement.id === 'section-4'");
  assert.ok(await evaluate("location.hash.endsWith('&section=section-4')"));
  await command("Page.reload");
  await until("document.activeElement.id === 'section-4'");
  await evaluate("location.hash = 'home'");
  await until("document.querySelectorAll('.map-node').length > 10");
  assert.equal(await evaluate("document.querySelectorAll('[data-file]').length"), await evaluate("window.ATLAS.stats.files"));
  assert.equal(await evaluate("document.querySelector('#graph-title').textContent"), "Project source map");
  assert.equal(await evaluate("document.querySelectorAll('.map-node').length"), await evaluate("window.ATLAS.map_ids.length"));
  assert.ok(await evaluate("[...document.querySelectorAll('.map-node')].every(node => window.ATLAS.map_ids.includes(node.dataset.node))"));
  assert.ok(await evaluate("window.ATLAS.nodes.every(node => !['docs/', 'materials/', 'starter_kit/'].some(prefix => node.id.startsWith(prefix)))"));
  assert.ok(await evaluate("window.ATLAS.nodes.filter(node => window.ATLAS.map_ids.includes(node.id) && node.kind === 'file').every(node => node.id.startsWith('src/tacto_negotiator/') && (node.id.endsWith('.py') || node.id.startsWith('src/tacto_negotiator/web_ui/')))"));
  await evaluate("document.querySelector('#collapse-all').click()");
  assert.equal(await evaluate("document.querySelectorAll('#tree details[open]').length"), 0);
  await evaluate("document.querySelector('#expand-all').click()");
  assert.equal(await evaluate("document.querySelectorAll('#tree details[open]').length"), await evaluate("document.querySelectorAll('#tree details').length"));

  await evaluate("document.querySelector('#search').value = 'ArenaClient'; document.querySelector('#search').dispatchEvent(new Event('input'))");
  assert.ok(await evaluate("document.querySelectorAll('[data-file]').length < window.ATLAS.stats.files"));
  const client = "src/tacto_negotiator/client.py";
  await evaluate(`document.querySelector('[data-file="${client}"]').click()`);
  await until("document.querySelector('#inspector h2')?.textContent === 'client.py'");
  assert.ok(await evaluate("document.querySelector('#inspector').textContent.includes('Stored attributes:')"));
  assert.ok(await evaluate("document.querySelector('[data-node=\"system:arena\"]') !== null"));
  assert.ok(await evaluate("document.querySelector('#relationships').textContent.includes('INCOMING') && document.querySelector('#relationships').textContent.includes('OUTGOING')"));
  await evaluate("document.querySelector('#full-explanations').click()");
  assert.ok(await evaluate("document.querySelector('.tree-explanation .symbol-list') !== null"));
  assert.ok(await evaluate("document.querySelector('.tree-explanation .fields') !== null"));

  const beforeZoom = await evaluate("document.querySelector('#graph').getAttribute('viewBox')");
  await evaluate("document.querySelector('#zoom-in').click()");
  assert.notEqual(await evaluate("document.querySelector('#graph').getAttribute('viewBox')"), beforeZoom);
  await evaluate("document.querySelector('#fit').click()");
  assert.equal(await evaluate("document.querySelector('#graph').getAttribute('viewBox')"), beforeZoom);
  const box = await evaluate("(() => {const r = document.querySelector('#graph').getBoundingClientRect(); return {x:r.x+8,y:r.y+8}})()");
  await command("Input.dispatchMouseEvent", {type: "mousePressed", x: box.x, y: box.y, button: "left", clickCount: 1});
  await command("Input.dispatchMouseEvent", {type: "mouseMoved", x: box.x + 45, y: box.y + 30, button: "left", buttons: 1});
  await command("Input.dispatchMouseEvent", {type: "mouseReleased", x: box.x + 45, y: box.y + 30, button: "left", clickCount: 1});
  assert.notEqual(await evaluate("document.querySelector('#graph').getAttribute('viewBox')"), beforeZoom);

  await evaluate("document.querySelector('[data-node=\"system:arena\"]').focus()");
  await command("Input.dispatchKeyEvent", {type: "keyDown", key: "Enter", code: "Enter", windowsVirtualKeyCode: 13});
  await until("document.querySelector('#inspector h2')?.textContent === 'Hosted arena'");
  await evaluate("history.back()");
  await until("document.querySelector('#inspector h2')?.textContent === 'client.py'");

  await select("data/data/invoices.csv");
  assert.ok(await evaluate("document.querySelector('#inspector').textContent.includes('paid_date')"));
  assert.ok(await evaluate("document.querySelector('#relationships').textContent.includes('declared foreign key')"));
  await select("data/correspondence/20260911-70110-P01.md");
  assert.ok(await evaluate("document.querySelector('#inspector').textContent.includes('doc:20260911-70110-P01')"));

  await evaluate("location.hash = 'home'");
  await until("document.querySelector('#graph-title').textContent === 'Project source map'");
  await evaluate("document.querySelector('#map-mode').value = 'groups'; document.querySelector('#map-mode').dispatchEvent(new Event('change'))");
  await evaluate("document.querySelector('.map-node').dispatchEvent(new MouseEvent('click'))");
  await until("location.hash.startsWith('#group=')");
  assert.ok(await evaluate("document.querySelector('#inspector').textContent.includes('LOGICAL SUBGROUP')"));
  await evaluate("document.querySelector('#reset-map').click()");
  await until("location.hash === '#home'");
  await evaluate("document.querySelector('#map-mode').value = 'source'; document.querySelector('#map-mode').dispatchEvent(new Event('change'))");
  assert.equal(await evaluate("document.querySelectorAll('.map-node').length"), await evaluate("window.ATLAS.map_ids.length"));

  await evaluate("document.querySelector('[data-tab=\"TUTORIAL.md\"]').click()");
  await until("document.querySelector('#guide h1')?.textContent === 'Your first negotiation, step by step'");
  assert.equal(await evaluate("document.querySelector('[data-tab=\"TUTORIAL.md\"]').getAttribute('aria-current')"), "page");
  assert.ok(await evaluate("document.querySelector('#guide').textContent.includes('class TutorialBuyer(NaiveAgent):')"));
  assert.ok(await evaluate("document.querySelector('#guide').textContent.includes('Step 8 — Modify the atlas itself')"));
  assert.ok(await evaluate("document.querySelector('#guide ol[start=\"4\"]') !== null"), "Steps must keep their numbering after code examples");
  await evaluate("document.querySelector('[data-tab=\"FINAL_READINESS.md\"]').click()");
  await until("document.querySelector('#guide h1')?.textContent === 'Sunday: finish five eight-minute waves reliably'");
  assert.ok(await evaluate("document.querySelector('#guide').textContent.includes('150 unique final sessions')"));
  await evaluate("document.querySelector('#guide a[href=\"#guide=MODEL_SELECTION.md\"]').click()");
  await until("document.querySelector('#guide h1')?.textContent === 'Choose a model for the final'");
  assert.ok(await evaluate("document.querySelector('#guide').textContent.includes('LLM_EFFORT has no effect on its OpenAI-compatible branch')"));
  await evaluate("document.querySelector('[data-tab=\"ROUTING.md\"]').click()");
  await until("document.querySelector('#guide h1')?.textContent === 'Choose scripts or the model for each decision'");
  assert.ok(await evaluate("document.querySelector('#guide').textContent.includes('RoutedAgent.last_decision')"));
  assert.ok(await evaluate("document.querySelector('#guide').textContent.includes('LLM_STRONG_MODEL')"));
  await evaluate("document.querySelector('[data-tab=\"deals\"]').click()");
  await until("!document.querySelector('#deals-view').hidden && document.querySelector('#deals-view h1')?.textContent === 'Every negotiation, as it happened.'");
  assert.equal(await evaluate("document.querySelector('#guide-layout').hidden && document.querySelector('#workspace').hidden"), true);
  assert.equal(await evaluate("document.querySelectorAll('.deal-table tbody tr').length"), await evaluate("window.DEALS.deals.length"));
  await evaluate("document.querySelector('.deal-table tbody a').click()");
  await until("document.querySelectorAll('.chat-bubble').length > 0 && document.querySelectorAll('.steps > li').length > 0");
  assert.ok(await evaluate("document.querySelectorAll('.decision-tree li.visited').length > 1"));
  await evaluate("document.querySelector('.step-head').click()");
  await until("document.querySelectorAll('.decision-tree li.on-path').length >= 2");
  assert.ok(await evaluate("[...document.querySelectorAll('.chat-meta time')].every(t => t.dateTime)"), "Every message has a time");
  await evaluate("document.querySelector('[data-tab=\"DEALS.md\"]').click()");
  await until("document.querySelector('#deals-view').hidden && document.querySelector('#guide h1')?.textContent === 'Reading a deal'");
  await evaluate("document.querySelector('[data-tab=\"COMPETITION.md\"]').click()");
  await until("location.hash === '#guide=COMPETITION.md' && document.querySelector('#guide h1') !== null");
  assert.equal(await evaluate("document.querySelector('[data-tab=\"COMPETITION.md\"]').getAttribute('aria-current')"), "page");
  assert.ok(await evaluate("document.querySelector('#guide').textContent.includes('scenario')"));
  assert.ok(await evaluate("document.querySelector('#guide a[href=\"results/offline.json\"]') !== null"));
  assert.ok(await evaluate("window.ATLAS.reports['docs/results/offline.json'].match_count > 0"));
  await evaluate("document.querySelector('[data-tab=\"FAQ_ADVANTAGE.md\"]').click()");
  await until("document.querySelector('#guide h1')?.textContent === 'Turn the official FAQ into a better buyer'");
  assert.ok(await evaluate("document.querySelector('#guide').textContent.includes('264 tests')"));
  assert.ok(await evaluate("document.querySelector('#guide a[href=\"https://arena.tacto.ai/faq\"]') !== null"));
  await evaluate("document.querySelector('[data-tab=\"WEB_UI.md\"]').click()");
  await until("document.querySelector('#guide h1')?.textContent === 'Explore negotiation results locally'");
  assert.ok(await evaluate("document.querySelector('#guide').textContent.includes('.venv/bin/tacto-ui')"));
  await evaluate("document.querySelector('[data-tab=\"IMPLEMENTATION.md\"]').click()");
  await until("!document.querySelector('#guide').hidden");
  await until("location.hash === '#guide=IMPLEMENTATION.md' && document.querySelector('#guide').textContent.includes('src/tacto_negotiator')");
  assert.ok(await evaluate("document.querySelectorAll('#guide table').length > 0"));
  assert.ok(await evaluate("!document.querySelector('#guide').innerHTML.includes('<script')"));
  await command("Page.reload");
  await until("document.querySelector('#full-explanations')?.checked === true && document.querySelector('#guide h1')");

  await command("Emulation.setDeviceMetricsOverride", {width: 390, height: 844, deviceScaleFactor: 1, mobile: true});
  assert.equal(await evaluate("getComputedStyle(document.querySelector('#sidebar')).display"), "none");
  await evaluate("document.querySelector('#nav-toggle').click()");
  assert.equal(await evaluate("document.querySelector('#nav-toggle').getAttribute('aria-expanded')"), "true");
  await evaluate("document.querySelector('[data-tab=\"BEGINNER_GUIDE.md\"]').click()");
  await until("document.querySelector('#guide h1')?.textContent === 'Your first run, from start to finish'");
  assert.equal(await evaluate("document.querySelector('#nav-toggle').getAttribute('aria-expanded')"), "false");
  await evaluate("document.querySelector('#nav-toggle').click(); document.querySelector('[data-tab=\"BEGINNER_GUIDE.md\"]').click()");
  assert.equal(await evaluate("document.querySelector('#nav-toggle').getAttribute('aria-expanded')"), "false", "Selecting the current guide also closes the menu");
  assert.ok(await evaluate("document.documentElement.scrollWidth <= innerWidth"), "Beginner guide fits mobile");
  await command("Input.dispatchKeyEvent", {type: "keyDown", key: "/", code: "Slash", windowsVirtualKeyCode: 191});
  assert.equal(await evaluate("document.activeElement.id"), "search");
  await command("Input.dispatchKeyEvent", {type: "keyDown", key: "Escape", code: "Escape", windowsVirtualKeyCode: 27});
  assert.equal(await evaluate("document.activeElement.id"), "nav-toggle");
  await evaluate("location.hash = 'home'; document.querySelector('#search').value=''; document.querySelector('#search').dispatchEvent(new Event('input')); document.querySelector('#full-explanations').click(); document.querySelector('#collapse-all').click()");
  await until("document.querySelector('#graph-title').textContent === 'Project source map'");
  assert.ok(await evaluate("document.documentElement.scrollWidth <= innerWidth"), "Mobile page must not overflow horizontally");
  await evaluate("document.querySelector('#search').value='no-match-xyz'; document.querySelector('#search').dispatchEvent(new Event('input'))");
  assert.ok(await evaluate("document.querySelector('#tree').textContent.includes('No matching files')"));
  await evaluate("document.querySelector('#search').value=''; document.querySelector('#search').dispatchEvent(new Event('input'))");

  // Verify untrusted source text is rendered, never executed by the Markdown subset.
  assert.ok(await evaluate("markdown('<img src=x onerror=alert(1)> [bad](javascript:alert)', 'docs/README.md').includes('&lt;img')"));
  assert.ok(await evaluate("!markdown('[bad](javascript:alert)', 'docs/README.md').includes('href=')"));
  assert.equal(exceptions.length, 0, JSON.stringify(exceptions));
  assert.ok(requests.every(request => !/^https?:/.test(request)), "The page must not request remote assets");
  if (process.env.ATLAS_SCREENSHOT) {
    await command("Emulation.setDeviceMetricsOverride", {width: 1440, height: 1080, deviceScaleFactor: 1, mobile: false});
    await evaluate("document.querySelector('#expand-all').click(); scrollTo(0,0)");
    const screenshot = await command("Page.captureScreenshot", {format: "png"});
    await writeFile(process.env.ATLAS_SCREENSHOT, Buffer.from(screenshot.data, "base64"));
    await evaluate("location.hash = 'guide=BEGINNER_GUIDE.md'");
    await until("document.querySelector('#guide h1')?.textContent === 'Your first run, from start to finish'");
    await writeFile(process.env.ATLAS_SCREENSHOT.replace(/\.png$/, "-guide.png"), Buffer.from((await command("Page.captureScreenshot", {format:"png"})).data, "base64"));
    await command("Emulation.setDeviceMetricsOverride", {width:390, height:844, deviceScaleFactor:1, mobile:true});
    await writeFile(process.env.ATLAS_SCREENSHOT.replace(/\.png$/, "-mobile.png"), Buffer.from((await command("Page.captureScreenshot", {format:"png"})).data, "base64"));
  }
  console.log("Browser checks passed: tree, search, file details, schemas, graph neighborhoods/groups, source-focused overview, pan/zoom, keyboard, history, guides, saved settings, mobile and no remote requests.");
} finally {
  if (socket?.readyState === WebSocket.OPEN) {
    try { await command("Browser.close", {}, false); } catch { /* Browser may close the socket before its reply. */ }
    socket.close();
  }
  browser.kill();
  await rm(profile, {recursive: true, force: true, maxRetries: 3, retryDelay: 100});
}
