"use strict";
// The deal journal view (#deals, #deal=<id>): every hosted negotiation as a dated chat, with the path the
// buyer program took through its decision tree at each step. Data: deals.js (index, written by
// src/tacto_negotiator/deals.py after every move) and deals/<id>.js (one record, loaded on demand).

window.DealsView = (() => {
  const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
  const records = new Map();
  const view = {route: null, thread: "all", step: null, node: null, query: "", source: "all", auto: false, timer: null};
  const el = () => document.querySelector("#deals-view");
  const index = () => window.DEALS || {deals: [], tree: []};
  const tree = () => new Map(index().tree.map(node => [node.id, node]));
  const fmtTime = iso => iso ? new Date(iso).toLocaleString(undefined, {year: "numeric", month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit"}) : "—";
  const fmtClock = iso => iso ? new Date(iso).toLocaleTimeString(undefined, {hour: "2-digit", minute: "2-digit", second: "2-digit"}) : "";
  const fmtDay = iso => iso ? new Date(iso).toLocaleDateString(undefined, {weekday: "long", year: "numeric", month: "long", day: "numeric"}) : "Undated";
  const score = value => typeof value === "number" ? value.toFixed(3) : "—";
  const euro = value => typeof value === "number" ? "€" + value.toLocaleString(undefined, {maximumFractionDigits: 0}) : "—";
  const shortId = sid => String(sid || "").replace(/^sup:/, "");

  window.tactoDeal = record => {
    records.set(record.id, record);
    if (view.route === "deal=" + record.id) render(view.route, true);
  };

  function load(src) {
    const script = document.createElement("script");
    script.src = src + (src.includes("?") ? "&" : "?") + "t=" + Date.now();
    script.onload = script.onerror = () => script.remove();
    document.head.append(script);
    return script;
  }

  function refresh() {
    const script = load("deals.js");
    script.addEventListener("load", () => {
      const id = view.route?.startsWith("deal=") ? view.route.slice(5) : null;
      const summary = id && index().deals.find(deal => deal.id === id);
      if (id && (!records.has(id) || summary?.updated_utc !== records.get(id).updated_utc)) load(`deals/${encodeURIComponent(id)}.js`);
      else render(view.route, true);
    });
  }

  function setAuto(on) {
    view.auto = on;
    clearInterval(view.timer);
    if (on) view.timer = setInterval(refresh, 5000);
  }

  function badge(status) {
    return `<span class="deal-badge ${esc(status)}">${esc(status || "unknown")}</span>`;
  }

  // ---------- list ----------
  function listHTML() {
    const query = view.query.toLowerCase();
    const deals = index().deals.filter(deal => (view.source === "all" || (deal.source || "").startsWith(view.source))
      && (!query || `${deal.case_id} ${deal.title} ${deal.run} ${deal.session_id} ${Object.values(deal.suppliers || {}).join(" ")}`.toLowerCase().includes(query)));
    const days = new Map();
    deals.forEach(deal => {
      const day = (deal.started_utc || "").slice(0, 10);
      if (!days.has(day)) days.set(day, []);
      days.get(day).push(deal);
    });
    const live = index().deals.filter(deal => deal.status === "live").length;
    const scored = deals.filter(deal => typeof deal.score === "number");
    const mean = scored.length ? scored.reduce((sum, deal) => sum + deal.score, 0) / scored.length : null;
    const rows = [...days].map(([day, items]) => `<section class="deal-day"><h2>${esc(fmtDay(items[0].started_utc))} <span class="muted">${items.length} negotiation${items.length === 1 ? "" : "s"}</span></h2>
      <div class="table-scroll"><table class="deal-table"><thead><tr><th scope="col">Started</th><th scope="col">Case</th><th scope="col">Status</th><th scope="col">Score</th><th scope="col">Deal</th><th scope="col">Messages</th><th scope="col">Model / fallbacks</th><th scope="col">Run</th></tr></thead><tbody>
      ${items.map(deal => `<tr><td><a href="#deal=${encodeURIComponent(deal.id)}">${esc(fmtClock(deal.started_utc))}</a></td><td><a href="#deal=${encodeURIComponent(deal.id)}"><strong>${esc((deal.case_id || "").replace("case:", ""))}</strong> ${esc(deal.title || "")}</a></td><td>${badge(deal.status)}</td><td>${score(deal.score)}</td><td>${deal.deal ? `${esc(deal.suppliers?.[deal.supplier_id] || shortId(deal.supplier_id))}${deal.unit_price != null ? ` · €${esc(deal.unit_price)}` : ""}` : esc(deal.end_reason || (deal.status === "live" ? "negotiating…" : "no deal"))}</td><td>${deal.messages}</td><td>${deal.model_calls} / ${deal.fallbacks}</td><td class="muted">${esc(deal.run || deal.source || "")}</td></tr>`).join("")}
      </tbody></table></div></section>`).join("");
    return `<p class="breadcrumb">DEAL JOURNAL</p><h1>Every negotiation, as it happened.</h1>
      <p class="intro">Each hosted case is stored while it runs: the full conversation with every supplier, the time of each message, and the path the buyer program took at every decision. Open a deal to read it like a chat.</p>
      <div class="stats"><div class="stat"><strong>${index().deals.length}</strong><span>Negotiations stored</span></div><div class="stat"><strong>${live}</strong><span>Live now</span></div><div class="stat"><strong>${score(mean)}</strong><span>Mean score (shown)</span></div><div class="stat"><strong>${esc(index().generated_utc ? fmtClock(index().generated_utc) : "—")}</strong><span>Index updated</span></div></div>
      <div class="deal-controls"><label>Find <input id="deal-search" type="search" value="${esc(view.query)}" placeholder="Case, title, supplier, run…"></label>
      <label>Source <select id="deal-source"><option value="all">All</option><option value="hosted (backfilled)" ${view.source === "hosted (backfilled)" ? "selected" : ""}>Backfilled practice</option><option value="hosted" ${view.source === "hosted" ? "selected" : ""}>Hosted (all)</option></select></label>
      <label class="setting"><input id="deal-auto" type="checkbox" ${view.auto ? "checked" : ""}> Auto-refresh every 5 s</label></div>
      ${rows || `<p class="empty">No negotiations recorded yet. Hosted runs (<code>tacto play</code>, <code>tacto run</code>) write them to <code>docs/deals/</code>; <code>tacto deals docs/results/live-practice-*.json</code> backfills saved practice runs.</p>`}`;
  }

  // ---------- one deal ----------
  function pathLabels(path) {
    const nodes = tree();
    return path.map(id => nodes.get(id)?.label || id.replace(/^error:/, "Error: ").replace(/^reason:/, "Reason: "));
  }

  function leafWhy(step) {
    const nodes = tree();
    const leaf = [...step.path].reverse().find(id => nodes.has(id));
    return nodes.get(leaf)?.why || "";
  }

  function sheetsHTML(sheets, highlight) {
    if (!sheets?.length) return "";
    const fields = [...new Set(sheets.flatMap(sheet => Object.keys(sheet.terms || sheet)))].filter(f => f !== "index_clause" || sheets.some(s => (s.terms || s)[f]));
    const order = ["unit_price", ...fields.filter(f => f !== "unit_price")];
    const cell = value => value && typeof value === "object" ? `${esc(value.series || "")} ${value.material_share != null ? Math.round(value.material_share * 100) + "%" : ""}` : esc(value ?? "—");
    return `<table class="sheet-table"><thead><tr><th scope="col">Sheet</th>${order.map(f => `<th scope="col">${esc(f.replace(/_/g, " "))}</th>`).join("")}</tr></thead><tbody>${sheets.map((sheet, i) => {
      const terms = sheet.terms || sheet;
      return `<tr class="${highlight === sheet.id ? "accepted" : ""}"><td>${esc(sheet.id ? sheet.id.replace(/^offer:/, "") : "#" + (i + 1))}</td>${order.map(f => `<td>${cell(terms[f])}</td>`).join("")}</tr>`;
    }).join("")}</tbody></table>`;
  }

  function stepFor(record, message) {
    if (message.from !== "buyer") return null;
    const exact = record.decisions.findIndex(step => step.move?.supplier_id === message.supplier_id && step.sent_utc && step.sent_utc === message.at
      && (message.closing ? step.move?.action !== "send" : step.move?.action === "send"));
    if (exact >= 0 || !record.times) return exact;
    // Backfilled records have no send times: the k-th buyer message of a thread is its k-th sent decision.
    const mine = record.decisions.map((step, i) => ({step, i})).filter(({step}) => step.move?.supplier_id === message.supplier_id);
    if (message.closing) return mine.filter(({step}) => step.move?.action && step.move.action !== "send").pop()?.i ?? -1;
    const sends = mine.filter(({step}) => step.move?.action === "send" && step.reply);
    const order = record.messages.filter(m => m.supplier_id === message.supplier_id && m.from === "buyer" && !m.closing).indexOf(message);
    return sends[order]?.i ?? -1;
  }

  function chatHTML(record) {
    const suppliers = record.suppliers || {};
    const messages = record.messages.filter(m => view.thread === "all" || m.supplier_id === view.thread)
      .slice().sort((a, b) => (a.at || "").localeCompare(b.at || "") || (a.supplier_id || "").localeCompare(b.supplier_id || "") || a.index - b.index);
    const accepted = record.closing?.action?.term_sheet_id;
    let day = "";
    const bubbles = messages.map(message => {
      const name = message.from === "buyer" ? "Our buyer" : (suppliers[message.supplier_id] || message.from_name || message.supplier_id);
      const step = stepFor(record, message);
      const thisDay = (message.at || "").slice(0, 10);
      const divider = thisDay && thisDay !== day ? `<div class="chat-day">${esc(fmtDay(message.at))}</div>` : "";
      day = thisDay || day;
      const claims = (message.claim_results || []).map(c => `<span class="claim ${esc(c.result)}">claim ${esc(c.index)}: ${esc(c.result)}</span>`).join("");
      const action = message.action ? `<p class="chat-action">${message.action.type === "accept" ? "✔ Accepted " + esc((message.action.term_sheet_id || "").replace("offer:", "offer ")) : message.action.type === "walk_away" ? "✖ Walked away" : esc(message.action.type)}</p>` : "";
      const to = view.thread === "all" && Object.keys(suppliers).length > 1 ? `<span class="chat-thread">${message.from === "buyer" ? "to" : "thread"} ${esc(shortId(message.supplier_id))}</span>` : "";
      return `${divider}<div class="chat-row ${message.from}"><div class="chat-bubble ${message.closing ? "closing" : ""}">
        <div class="chat-meta"><strong>${esc(name)}</strong> ${to} <time datetime="${esc(message.at || "")}">${esc(fmtClock(message.at))}</time>${message.status ? ` ${badge(message.status)}` : ""}${step >= 0 && step !== null ? ` <a class="step-link" href="#deal=${encodeURIComponent(record.id)}&step=${step + 1}">step ${step + 1} ›</a>` : ""}</div>
        ${message.body ? `<div class="chat-body">${esc(message.body).replace(/\n/g, "<br>")}</div>` : ""}${action}
        ${sheetsHTML(message.term_sheets, accepted)}${claims ? `<div class="claims">${claims}</div>` : ""}${message.attachments?.length ? `<div class="muted">📎 ${message.attachments.map(esc).join(", ")}</div>` : ""}
      </div></div>`;
    }).join("");
    const tabs = ["all", ...Object.keys(suppliers)].map(sid => `<button type="button" class="thread-tab" data-thread="${esc(sid)}" aria-pressed="${view.thread === sid}">${sid === "all" ? "All threads" : esc(suppliers[sid] || sid)}</button>`).join("");
    return `<div class="thread-tabs" role="group" aria-label="Supplier threads">${tabs}</div><div class="chat">${bubbles || '<p class="empty">No messages yet.</p>'}${record.status === "live" ? '<p class="chat-typing">Negotiation in progress…</p>' : ""}</div>`;
  }

  function treeHTML(record) {
    const nodes = index().tree;
    const counts = new Map();
    record.decisions.forEach(step => step.path.forEach(id => counts.set(id, (counts.get(id) || 0) + 1)));
    const selected = view.step != null ? new Set(record.decisions[view.step]?.path || []) : null;
    const extra = new Map();
    record.decisions.forEach(step => step.path.filter(id => !nodes.some(n => n.id === id)).forEach(id => extra.set(id, step.path[step.path.length - 2])));
    const children = parent => [...nodes.filter(n => n.parent === parent), ...[...extra].filter(([, p]) => p === parent).map(([id, p]) => ({id, parent: p, label: id.replace(/^error:/, "Error: ").replace(/^reason:/, "Reason: "), why: "Not a named branch: see the step for details."}))];
    const branch = parent => {
      const items = children(parent);
      if (!items.length) return "";
      return `<ul>${items.map(node => {
        const count = counts.get(node.id) || 0;
        const cls = [count ? "visited" : "unvisited", selected?.has(node.id) ? "on-path" : "", view.node === node.id ? "focus" : ""].join(" ");
        return `<li class="${cls}"><button type="button" class="tree-node" data-node="${esc(node.id)}" title="${esc(node.why)}"><span>${esc(node.label)}</span>${count ? `<b>${count}</b>` : ""}</button>${branch(node.id)}</li>`;
      }).join("")}</ul>`;
    };
    const root = nodes.find(n => !n.parent);
    return root ? `<div class="decision-tree"><ul><li class="visited ${selected ? "on-path" : ""}"><button type="button" class="tree-node" data-node="${esc(root.id)}" title="${esc(root.why)}"><span>${esc(root.label)}</span><b>${record.decisions.length}</b></button>${branch(root.id)}</li></ul></div>` : "";
  }

  function stepsHTML(record) {
    const steps = record.decisions.map((step, i) => ({step, i})).filter(({step}) => !view.node || step.path.includes(view.node));
    return `<ol class="steps">${steps.map(({step, i}) => {
      const move = step.move || {};
      const prices = (move.term_sheets || []).map(t => t.unit_price).filter(p => p != null);
      const what = move.action && move.action !== "send" ? `${move.action === "accept" ? "Accept" : move.action === "walk_away" ? "Walk away" : move.action} ${esc(shortId(move.supplier_id))}${move.term_sheet_id ? " · " + esc(move.term_sheet_id) : ""}`
        : `Message to ${esc(record.suppliers?.[move.supplier_id] || shortId(move.supplier_id))}${prices.length ? ` · ${prices.length} sheet${prices.length === 1 ? "" : "s"} at €${prices.map(esc).join(" / ")}` : " · no term sheet"}`;
      const sent = step.sent_utc ? `sent ${esc(fmtClock(step.sent_utc))}` : record.source?.includes("backfilled") ? "" : '<span class="warn">not sent (deadline passed or superseded)</span>';
      const reply = step.reply ? `<div class="muted">Reply: ${esc(step.reply.status)}${step.reply.unit_price != null ? " · €" + esc(step.reply.unit_price) : ""}</div>` : "";
      const proposed = step.proposed ? `<details><summary>Proposed before the gate</summary><pre>${esc(JSON.stringify(step.proposed, null, 2))}</pre></details>` : "";
      return `<li id="step-${i + 1}" class="${view.step === i ? "selected" : ""}"><button type="button" class="step-head" data-step="${i}"><span class="step-no">${i + 1}</span><span class="route ${esc(step.route)}">${esc(step.route)}</span>${step.fallback ? '<span class="route fallback">fallback</span>' : ""}${step.concurrent ? '<span class="route">concurrent</span>' : ""}<time>${esc(fmtClock(step.at))}</time></button>
        <div class="crumbs">${pathLabels(step.path).map(esc).join(" <span>›</span> ")}</div>
        <p class="why">${esc(leafWhy(step))}</p>
        <div><strong>${what}</strong></div>
        <div class="muted">${step.decision_s != null ? `decided in ${Number(step.decision_s).toFixed(1)} s` : ""}${step.error ? ` · ${esc(step.error)}` : ""} ${sent}</div>${reply}${proposed}</li>`;
    }).join("")}</ol>`;
  }

  function dealHTML(record) {
    const result = record.result || {};
    const breakdown = result.cost_breakdown ? Object.entries(result.cost_breakdown).filter(([, v]) => v).map(([k, v]) => `<span class="chip">${esc(k.replace(/_/g, " "))} ${euro(v)}</span>`).join("") : "";
    return `<p class="breadcrumb"><a href="#deals">DEAL JOURNAL</a> <span>/</span> ${esc(fmtDay(record.started_utc))}</p>
      <h1>${esc((record.case_id || "").replace("case:", ""))} · ${esc(record.title || "")}</h1>
      <div class="deal-head">${badge(record.status)}<span>Started ${esc(fmtTime(record.started_utc))}</span>${record.finished_utc ? `<span>Ended ${esc(fmtTime(record.finished_utc))}</span>` : ""}${record.goal ? `<span>Goal: ${esc(record.goal)}</span>` : ""}${record.session_id ? `<span class="muted">session ${esc(record.session_id)}</span>` : ""}${record.run ? `<span class="muted">${esc(record.run)}</span>` : ""}${result.replay_url ? `<a href="${esc(result.replay_url)}" target="_blank" rel="noreferrer">Arena replay ↗</a>` : ""}</div>
      ${record.times ? `<p class="muted">Times are ${esc(record.times)}.</p>` : ""}
      <div class="stats"><div class="stat"><strong>${score(result.score)}</strong><span>Score</span></div><div class="stat"><strong>${result.deal ? esc(record.suppliers?.[result.supplier_id] || shortId(result.supplier_id)) : esc(result.end_reason || (record.status === "live" ? "Negotiating" : "No deal"))}</strong><span>${result.deal ? `Deal${result.terms?.unit_price != null ? " at €" + esc(result.terms.unit_price) : ""}` : "Outcome"}</span></div><div class="stat"><strong>${euro(result.cost_eur ?? result.c_deal)}</strong><span>Total cost C (opening ${euro(result.opening_cost_eur ?? result.c_open)}, best ${euro(result.reference_cost_eur ?? result.c_best)})</span></div><div class="stat"><strong>${record.decisions.length}</strong><span>Decisions (${record.decisions.filter(d => d.route === "fast" || d.route === "strong").length} by the model)</span></div></div>
      ${breakdown ? `<div class="chips">${breakdown}</div>` : ""}${record.error ? `<p class="warn">${esc(record.error)}</p>` : ""}
      <div class="deal-layout"><section class="deal-chat" aria-label="Conversation">${chatHTML(record)}</section>
      <aside class="deal-path" aria-label="Decision path"><h2>Decision path</h2><p class="muted">How the program chose each move. Counts show how often each branch was taken in this deal; select a branch to filter the steps, or a step to trace its path.</p>${treeHTML(record)}
      ${view.node ? `<p class="muted">Steps through <strong>${esc(tree().get(view.node)?.label || view.node)}</strong> · <button type="button" class="link-button" data-node="">show all</button></p>` : ""}${stepsHTML(record)}</aside></div>
      <p class="muted"><label class="setting"><input id="deal-auto" type="checkbox" ${view.auto ? "checked" : ""}> Auto-refresh every 5 s</label></p>`;
  }

  function bind() {
    const root = el();
    root.querySelector("#deal-search")?.addEventListener("input", event => { view.query = event.target.value; const pos = event.target.selectionStart; render(view.route, true); const input = root.querySelector("#deal-search"); input.focus(); input.setSelectionRange(pos, pos); });
    root.querySelector("#deal-source")?.addEventListener("change", event => { view.source = event.target.value; render(view.route, true); });
    root.querySelector("#deal-auto")?.addEventListener("change", event => setAuto(event.target.checked));
    root.querySelectorAll("[data-thread]").forEach(button => button.addEventListener("click", () => { view.thread = button.dataset.thread; render(view.route, true); }));
    root.querySelectorAll("[data-node]").forEach(button => button.addEventListener("click", () => { view.node = button.dataset.node && view.node !== button.dataset.node ? button.dataset.node : null; render(view.route, true); }));
    root.querySelectorAll("[data-step]").forEach(button => button.addEventListener("click", () => { const i = Number(button.dataset.step); view.step = view.step === i ? null : i; render(view.route, true); }));
  }

  function render(route, keepScroll = false, section = null) {
    const [base, stepPart] = route.split("&step=");  // #deal=<id>&step=<n> from a chat bubble
    route = base;
    if (stepPart) section = "step=" + stepPart;
    const changed = route !== view.route;
    view.route = route;
    if (changed) { view.thread = "all"; view.node = null; view.step = null; }
    const stepMatch = /(?:^|&)step=(\d+)/.exec(section || "");
    if (stepMatch) view.step = Number(stepMatch[1]) - 1;
    const root = el();
    if (route === "deals") {
      root.innerHTML = listHTML();
      document.title = "Deals · Tacto";
    } else {
      const id = route.slice(5);
      const record = records.get(id);
      const known = index().deals.find(deal => deal.id === id);
      if (!record) {
        root.innerHTML = `<p class="breadcrumb"><a href="#deals">DEAL JOURNAL</a></p><h1>${known ? esc(known.title || id) : "Deal not found"}</h1><p class="empty">${known ? "Loading the conversation…" : "This deal is not in the journal index."}</p>`;
        if (known) load(`deals/${encodeURIComponent(id)}.js`);
      } else {
        root.innerHTML = dealHTML(record);
        document.title = `${(record.case_id || "").replace("case:", "")} deal · Tacto`;
      }
    }
    bind();
    if (!keepScroll) {
      const target = view.step != null && document.getElementById(`step-${view.step + 1}`);
      if (target) target.scrollIntoView({block: "center"}); else window.scrollTo(0, 0);
    }
  }

  return {render, refresh};
})();
