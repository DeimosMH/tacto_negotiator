"use strict";

const $ = (id) => document.getElementById(id);
const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const numeric = (value) => typeof value === "number" && Number.isFinite(value);
const list = (value) => Array.isArray(value) ? value : [];
const label = (value) => String(value ?? "Unknown").replaceAll("_", " ");
const number = (value, digits = 0) => numeric(value) ? value.toLocaleString("en-GB", {maximumFractionDigits:digits}) : "—";
const euro = (value) => numeric(value) ? `€${number(value, 2)}` : "Unknown";
const seconds = (value) => numeric(value) ? `${number(value, value < 1 ? 3 : 2)}s` : "Not recorded";
const mean = (values) => { const known = values.filter(numeric); return known.length ? known.reduce((a,b) => a+b, 0) / known.length : null; };
const sum = (values) => values.filter(numeric).reduce((a,b) => a+b, 0);
const elapsed = (row) => row.elapsed_s ?? row.virtual_elapsed_s;
const caseContext = (row) => report?.source === "hosted_practice" ? `Practice · ${number(row.rounds)} rounds` : `wave ${row.wave ?? "?"} · seed ${row.seed ?? "?"}`;
const outcomes = {deal:"Valid deal", walk:"Valid walk-away", invalid:"Invalid outcome", error:"Run error", unknown:"Unknown"};
const outcome = (row) => row.error ? "error" : row.valid === false ? "invalid" : row.valid === true ? (row.deal === true ? "deal" : row.deal === false ? "walk" : "unknown") : "unknown";
const reasons = {
  simple_opening_counter:"The opening fits the supported price-only pattern, so a script makes the counteroffer.",
  enumerated_opening:"A script compares a bounded set of complete packages, including non-price costs, and proposes a supported package.",
  must_have_repair:"The standing package fails a required constraint. The script proposes terms that repair it.",
  rfq_package_comparison:"The script compares supplier packages using full cost and selects the next bidder to negotiate with.",
  bounded_concession:"The script advances the offer within its validated cost and concession limits.",
  agreed_package:"The supplier has agreed to a package that passes the buyer's checks, so the agent can close it.",
  small_cost_gap:"The cost gap is small enough under the policy's threshold to accept the eligible standing offer.",
  round_reserve:"Few rounds remain. The policy keeps a closing opportunity instead of another ordinary counteroffer.",
  quote_plateau:"Recorded quotes have stopped improving. The script switches to its plateau policy.",
  missing_quote_terms:"The quote lacks terms needed for reliable evaluation. The policy uses its missing-evidence path.",
  terminal_offers:"No ordinary negotiating path remains for the available supplier offers. The policy checks its closing options.",
  deadline_close:"The remaining time triggers the closing policy, which checks the available offers and walk-away option.",
  semantic_decision:"The case contains meaning or constraints outside the supported deterministic patterns. The router requests model help.",
  complex_tradeoffs:"The router classifies the unresolved trade-offs as complex and chooses its stronger configured model tier.",
  complex_fast_budget:"The situation is complex, but the remaining time favors the faster model tier.",
  strong_unconfigured:"The stronger tier is not configured. The router uses the available faster tier.",
  agent_policy:"This baseline agent supplied the action; it did not record a more specific routing reason.",
};
let report = null, rows = [], filtered = [], currentName = new URLSearchParams(location.search).get("report") || "", importedName = "", selected = null, stepIndex = 0, page = 0, loadId = 0;
const PAGE_SIZE = 30;

function status(message, error = false) { $("status").textContent = message; $("status").classList.toggle("error", error); }
function savedAt(value) {
  if (typeof value !== "string" || !value) return "not recorded";
  const date = new Date(value);
  return Number.isFinite(date.getTime()) ? `${date.toLocaleString("en-GB", {day:"2-digit", month:"short", year:"numeric", hour:"2-digit", minute:"2-digit", second:"2-digit", timeZone:"UTC"})} UTC` : value;
}
async function json(url) { const response = await fetch(url); if (!response.ok) throw new Error(`Cannot open saved data (HTTP ${response.status}).`); return response.json(); }
function validate(data) {
  if (!data || !Array.isArray(data.matches) || data.matches.length > 50000) throw new Error("Choose a competition JSON report with a matches array (up to 50,000 matches).");
  if (data.matches.some((row) => !row || typeof row !== "object" || Array.isArray(row) ||
      (row.decisions !== undefined && (!Array.isArray(row.decisions) || row.decisions.some((d) => !d || typeof d !== "object" || Array.isArray(d)))))) {
    throw new Error("This report contains an invalid match or decision record.");
  }
  return data;
}
function options(id, values, title, preserve) {
  const previous = preserve ? $(id).value : "";
  $(id).innerHTML = `<option value="">${esc(title)}</option>` + [...new Set(values.map(String))].sort((a,b) => a.localeCompare(b, "en", {numeric:true})).map((v) => `<option value="${esc(v)}">${esc(label(v))}</option>`).join("");
  if ([...$(id).options].some((o) => o.value === previous)) $(id).value = previous;
}
function setReport(data, name, preserve = false) {
  report = validate(data); currentName = name;
  // Competition reports store scores on 0–1; the UI consistently displays points out of 100.
  rows = report.matches.map((row, id) => ({...row, score:numeric(row.score) ? row.score*100 : null, _id:id}));
  if (!preserve) { selected = null; stepIndex = 0; $("search").value = ""; $("outcome").value = ""; }
  options("strategy", rows.map((r) => r.variant_id ?? "Unknown"), "All strategies", preserve);
  options("scenario", rows.map((r) => r.scenario ?? "Unknown"), "All scenarios", preserve);
  options("wave", rows.map((r) => r.wave ?? "Unknown"), "All waves", preserve);
  const config = report.config || {};
  const hosted = report.source === "hosted_practice";
  $("wave").disabled = hosted;
  const notices = [];
  if (hosted) notices.push("Hosted practice results. Practice is not the final ranking: the final uses unseen suppliers. Scores come from the arena; elapsed time is real wall time.");
  else if (!numeric(config.scoring_version) || config.scoring_version < 2) notices.push("Historical evaluator: these scores predate scoring version 2. Read them as recorded; do not rank them against current runs.");
  if (report.excluded_from_comparison) notices.push(`Excluded from comparison: ${report.exclusion_reason || "diagnostic pilot"}.`);
  if (report.status && report.status !== "completed") notices.push(`Report status: ${label(report.status)}. Results may be incomplete.`);
  $("notice").hidden = !notices.length; $("notice").textContent = notices.join(" ");
  const metadata = hosted ? [["Scoring", "hosted arena"], ["Team", report.team_name], ["Set", "practice"], ["Model", report.models?.fast], ["Workers", config.workers], ["Saved", report.finished_utc || report.started_utc]] : [["Scoring", config.scoring_version ? `v${config.scoring_version}` : "historical"], ["Waves", config.waves], ["Wave cap", numeric(config.wave_seconds) ? `${config.wave_seconds}s` : "not recorded"], ["Workers", config.workers], ["Cohort", config.holdout ? "holdout" : "standard"], ["Saved", report.finished_utc || report.started_utc || "not recorded"]];
  $("run-meta").innerHTML = metadata.filter(([,v]) => v !== undefined).map(([k,v]) => `<span>${esc(k)} <strong ${k === "Saved" ? `title="${esc(v)}"` : ""}>${esc(k === "Saved" ? savedAt(v) : v)}</strong></span>`).join("");
  status(`${name} · ${number(rows.length)} matches${name === importedName ? " · opened in this browser only" : " · saved local report"}`);
  page = 0; render();
}
async function loadReport(name, preserve = false) {
  const request = ++loadId;
  status(`Loading ${name}…`);
  try {
    const data = validate(await json(`/api/reports/${encodeURIComponent(name)}`));
    if (request !== loadId) return;
    if (importedName) for (const option of [...$("report").options]) if (option.value === importedName) option.remove();
    importedName = ""; setReport(data, name, preserve);
  } catch (error) {
    if (request === loadId) {
      $("report").value = currentName;
      status(`${error.message} ${report ? "The previous run is still shown." : "You can open a JSON report from your computer."}`, true);
    }
  }
}
async function refresh() {
  $("refresh").disabled = true;
  try {
    const reports = await json("/api/reports");
    reports.sort((a,b) => Number(a.excluded)-Number(b.excluded) || (b.scoring_version || 0)-(a.scoring_version || 0) || a.name.localeCompare(b.name));
    $("report").innerHTML = reports.map((r) => `<option value="${esc(r.name)}">${esc(r.name)} · ${number(r.matches)} matches${r.excluded ? " · pilot" : ""}</option>`).join("");
    if (importedName) {
      $("report").add(new Option(`${importedName} · browser import`, importedName)); $("report").value = importedName;
      status("Saved-run list refreshed. Your imported report is still shown.");
    } else if (reports.length) {
      const name = reports.some((r) => r.name === currentName) ? currentName : reports[0].name;
      $("report").value = name; await loadReport(name, name === currentName);
    } else {
      $("report").add(new Option("No saved competition reports", ""));
      status(report ? "No saved reports found. The previously loaded run is still shown." : "No reports yet. Open a competition JSON file, or save a run in the reports directory.");
      if (!report) render();
    }
  } catch (error) { status(`${error.message} You can still open a local JSON file.`, true); }
  finally { $("refresh").disabled = false; }
}

function bar(key, title, value, maximum, action, active = false) {
  const width = numeric(value) && maximum > 0 ? Math.max(0, Math.min(100, value / maximum * 100)) : 0;
  const body = `<span class="bar-label" title="${esc(title)}">${esc(title)}</span><svg viewBox="0 0 100 10" preserveAspectRatio="none" aria-hidden="true"><rect class="bar-bg" width="100" height="10" rx="3"/><rect class="bar-fill ${esc(key)}" width="${width}" height="10" rx="3"/></svg><span class="bar-value">${number(value, action === "strategy" ? 1 : 0)}</span>`;
  return action ? `<button class="bar-row" data-filter="${action}" data-value="${esc(key)}" aria-pressed="${active}">${body}</button>` : `<div class="bar-row route-bar">${body}</div>`;
}
function render() {
  filtered = rows.filter((r) => (!$("strategy").value || String(r.variant_id ?? "Unknown") === $("strategy").value) &&
    (!$("scenario").value || String(r.scenario ?? "Unknown") === $("scenario").value) &&
    (!$("wave").value || String(r.wave ?? "Unknown") === $("wave").value) &&
    (!$("outcome").value || outcome(r) === $("outcome").value) &&
    `${r.case_id ?? ""} ${r.seed ?? ""}`.toLowerCase().includes($("search").value.trim().toLowerCase()));
  const sort = $("sort").value;
  const hasFilters = ["strategy", "scenario", "wave", "outcome", "search"].some((id) => $(id).value.trim());
  $("reset").disabled = !hasFilters;
  $("filter-summary").textContent = `${number(filtered.length)} of ${number(rows.length)} matches${hasFilters ? " · filters applied" : " · all results"}`;
  filtered.sort((a,b) => sort === "case" ? String(a.case_id).localeCompare(String(b.case_id)) :
    sort === "time" ? (elapsed(b) ?? -Infinity)-(elapsed(a) ?? -Infinity) :
    sort === "score-desc" ? (b.score ?? -Infinity)-(a.score ?? -Infinity) : (a.score ?? Infinity)-(b.score ?? Infinity));
  const decisions = filtered.flatMap((r) => list(r.decisions));
  const known = filtered.filter((r) => typeof r.valid === "boolean");
  const valid = known.length ? known.filter((r) => r.valid).length / known.length * 100 : null;
  const attemptCounts = filtered.map((r) => {
    const recorded = list(r.decisions).map((d) => d.model_attempts).filter(numeric);
    return numeric(r.model_attempts) ? r.model_attempts : recorded.length ? sum(recorded) : null;
  }).filter(numeric);
  const fallbackFlags = decisions.filter((d) => typeof d.fallback === "boolean");
  const metrics = [["Matches", number(filtered.length), `${number(rows.length)} in this run`], ["Valid outcomes", `${number(valid, 1)}${valid === null ? "" : "%"}`, `${known.length} with validity recorded`], ["Mean score", number(mean(filtered.map((r) => r.score)), 1), "Recorded evaluator · out of 100"], ["Model attempts", number(attemptCounts.length ? sum(attemptCounts) : null), "Attempts, not billable API calls"], ["Mean decision time", seconds(mean(decisions.map((d) => d.decision_wall_s))), fallbackFlags.length ? `${fallbackFlags.filter((d) => d.fallback).length} recorded fallback steps` : "Fallback status not recorded"]];
  $("metrics").innerHTML = metrics.map(([title,value,note]) => `<article class="metric"><span class="label">${title}</span><strong>${esc(value)}</strong><small>${esc(note)}</small></article>`).join("");
  const variants = [...new Set(filtered.map((r) => r.variant_id ?? "Unknown"))].sort();
  $("scores").innerHTML = variants.map((v) => bar(String(v), label(v), mean(filtered.filter((r) => (r.variant_id ?? "Unknown") === v).map((r) => r.score)), 100, "strategy", $("strategy").value === String(v))).join("") || '<p class="empty">No matching results.</p>';
  $("outcomes").innerHTML = Object.entries(outcomes).map(([key,title]) => bar(key, title, filtered.filter((r) => outcome(r) === key).length, filtered.length, "outcome", $("outcome").value === key)).join("");
  const routes = [...new Set(decisions.map((d) => String(d.route || "unknown")))].sort();
  $("routes").innerHTML = routes.map((key) => bar(["script","fast","strong"].includes(key) ? key : "unknown", label(key), decisions.filter((d) => (d.route || "unknown") === key).length, decisions.length)).join("") || '<p class="empty">No decision traces recorded.</p>';
  $("route-note").textContent = `${number(decisions.length)} recorded decisions across the filtered matches.`;
  page = Math.min(page, Math.max(0, Math.ceil(filtered.length / PAGE_SIZE)-1));
  if (!filtered.some((r) => r._id === selected)) { selected = filtered[0]?._id ?? null; stepIndex = 0; }
  renderTable(); renderDetail();
}
function renderTable() {
  const visible = filtered.slice(page * PAGE_SIZE, (page+1) * PAGE_SIZE);
  $("match-count").textContent = `(${number(filtered.length)})`;
  $("matches").innerHTML = visible.map((r) => `<tr class="${r._id === selected ? "selected" : ""}"><td><button class="case-button" data-case="${r._id}" aria-pressed="${r._id === selected}"><strong>${esc(label(r.scenario))}</strong><span>${esc(label(r.variant_id))} · ${esc(caseContext(r))}</span><span>${esc(r.case_id)}</span></button></td><td><span class="pill ${outcome(r)}">${outcomes[outcome(r)]}</span></td><td class="score-cell">${number(r.score,1)}</td><td>${seconds(elapsed(r))}</td></tr>`).join("") || `<tr><td colspan="4" class="empty"><strong>No matches to show.</strong><br>${rows.length ? 'Try another filter or <button class="text-button" data-clear-filters>reset all filters</button>.' : 'Choose a saved run or use Open JSON to explore a report.'}</td></tr>`;
  $("page").textContent = filtered.length ? `${page*PAGE_SIZE+1}–${Math.min((page+1)*PAGE_SIZE,filtered.length)} of ${number(filtered.length)}` : "0 matches";
  $("previous").disabled = page === 0; $("next").disabled = (page+1)*PAGE_SIZE >= filtered.length;
}
function actionText(action) {
  if (!action || !action.action) return "Action not recorded";
  const prices = list(action.term_sheets).filter((t) => numeric(t?.unit_price)).map((t) => euro(t.unit_price));
  return `${label(action.action)}${action.supplier_id ? ` → ${action.supplier_id}` : ""}${prices.length ? ` · ${prices.join(" / ")} per unit` : ""}${action.term_sheet_id ? ` · offer ${action.term_sheet_id}` : ""}`;
}
function facts(data) {
  return Object.entries(data || {}).map(([key,value]) => `<tr><td>${esc(label(key))}</td><td>${esc(typeof value === "object" ? JSON.stringify(value) : value)}</td></tr>`).join("");
}
function replayLink(value) {
  try {
    const url = new URL(value);
    if (url.origin === "https://arena.tacto.ai" && !url.username && !url.password && /^\/replay\/[\w-]+\/?$/.test(url.pathname)) {
      return `<a href="${esc(url.href)}" target="_blank" rel="noopener noreferrer">Open hosted replay ↗</a>`;
    }
  } catch { /* Missing or untrusted replay URLs are not links. */ }
  return "";
}
function priceChart(decisions) {
  const points = [];
  decisions.forEach((d, i) => {
    list(d.standing_prices).forEach((p) => points.push({x:i-.2, y:p?.unit_price, kind:"supplier", name:p?.supplier_id, step:i}));
    list(d.sent_action?.term_sheets).forEach((t) => points.push({x:i, y:t?.unit_price, kind:"buyer", name:d.sent_action.supplier_id, step:i}));
    if (d.supplier_reply) points.push({x:i+.2, y:d.supplier_reply.unit_price, kind:"reply", name:d.supplier_reply.supplier_id, step:i});
  });
  const known = points.filter((p) => numeric(p.y));
  if (!known.length) return '<p class="hint">No per-step prices recorded in this report.</p>';
  const low = Math.min(...known.map((p) => p.y)), high = Math.max(...known.map((p) => p.y));
  const pad = (high-low)*.15 || Math.max(Math.abs(high)*.05, .1), min = low-pad, max = high+pad;
  const x = (v) => 62+(v+.5)/decisions.length*398, y = (v) => 148-(v-min)/(max-min)*123;
  const ticks = [min,(max+min)/2,max].map((v) => `<line class="grid" x1="60" x2="468" y1="${y(v)}" y2="${y(v)}"/><text x="53" y="${y(v)+4}" text-anchor="end">${number(v,2)}</text>`).join("");
  const dots = known.map((p) => `<circle class="${p.kind}" cx="${x(p.x)}" cy="${y(p.y)}" r="4"><title>Step ${p.step+1} · ${esc(p.name)} · ${p.kind} · ${euro(p.y)} per unit</title></circle>`).join("");
  const axis = decisions.map((_,i) => i === 0 || i === decisions.length-1 || decisions.length < 12 ? `<text x="${x(i)}" y="169" text-anchor="middle">${i+1}</text>` : "").join("");
  return `<svg class="quote-chart" viewBox="0 0 480 185" role="img" aria-label="Unit prices in euros at each decision step"><rect x="${x(stepIndex-.4)}" y="17" width="${398/decisions.length*.8}" height="138" fill="#eef3e8"/>${ticks}${dots}${axis}<text x="265" y="183" text-anchor="middle">Decision step · unit price in EUR</text></svg><div class="legend"><span><i class="dot"></i>Standing quote</span><span><i class="dot buyer"></i>Submitted offer</span><span><i class="dot reply"></i>Supplier reply</span></div>`;
}
function renderDetail() {
  const row = rows.find((r) => r._id === selected);
  if (!row) { $("detail").innerHTML = '<p class="empty">Select a match to explore its decisions.</p>'; return; }
  const decisions = list(row.decisions); stepIndex = Math.min(stepIndex, Math.max(0,decisions.length-1));
  const d = decisions[stepIndex];
  const historical = decisions.length && !decisions.some((step) => list(step.standing_prices).length);
  $("detail").innerHTML = `<a class="back-to-matches" href="#workspace">← Back to matches</a><div class="panel-title"><p class="eyebrow">SELECTED MATCH</p><span class="pill ${outcome(row)}">${outcomes[outcome(row)]}</span></div><h2>${esc(label(row.scenario))}</h2><p class="subhead">${esc(label(row.variant_id))} · ${esc(row.case_id)} · ${esc(caseContext(row))}<br>${replayLink(row.replay_url)}</p>
    <div class="detail-metrics"><div><span>Score / 100</span><strong>${number(row.score,1)}</strong></div><div><span>Total cost</span><strong>${euro(row.cost_eur)}</strong></div><div><span>Feasible reference</span><strong>${euro(row.reference_cost_eur)}</strong></div></div>
    <p class="hint">End: ${esc(label(row.end_reason))}${row.supplier_id ? ` · supplier ${esc(row.supplier_id)}` : ""}. ${report.source === "hosted_practice" ? "Wall time" : "Elapsed"} ${seconds(elapsed(row))}; supplier delay ${seconds(row.supplier_delay_s)}. ${number(row.thread_timeouts)} thread timeouts; ${number(row.invalid_moves)} invalid moves.</p>
    ${row.error ? `<p class="event-alert">Run error: ${esc(row.error)}</p>` : ""}
    <details><summary>Final terms & cost context</summary><p class="hint">Cost above the evaluator’s reference: ${euro(row.regret_eur)}. Full cost includes more than unit price; it is not a sum of the plotted prices.</p>${Object.keys(row.terms || {}).length ? `<table class="facts"><tbody>${facts(row.terms)}</tbody></table>` : '<p class="hint">No final terms recorded.</p>'}${Object.keys(row.cost_breakdown || {}).length ? `<h3>Cost components · EUR</h3><table class="facts"><tbody>${facts(row.cost_breakdown)}</tbody></table>` : ""}</details>
    <section><h3>Price movement</h3>${priceChart(decisions)}${historical ? '<p class="hint">This older report has no standing-quote snapshots. Only recorded actions/replies can be shown.</p>' : ""}</section>
    <section><div class="panel-title"><h3>Decision timeline</h3><span>${decisions.length} steps</span></div><p class="hint">Select a step to see its action and recorded reason.</p><div class="steps">${decisions.map((step,i) => `<button class="step" data-step="${i}" aria-pressed="${i===stepIndex}">${i+1}<span>${esc(step.route || "unknown")}</span></button>`).join("")}</div>
    ${d ? stepDetail(d) : '<p class="empty">This report did not record a decision timeline.</p>'}</section>`;
}
function stepDetail(d) {
  let why = reasons[d.reason] || `No detailed rationale was recorded for “${d.reason || "unknown"}”.`;
  if (d.fallback) why += " The model path failed or its output could not be used; the controller selected a local fallback. The log does not contain a fuller diagnosis.";
  const how = `${label(d.route || "unknown")} route · ${number(d.model_attempts)} model attempts. Decision took ${seconds(d.decision_wall_s)} against a ${seconds(d.budget_s)} budget.`;
  const reply = d.supplier_reply;
  return `<div class="explanation"><h3>Step ${stepIndex+1}</h3><dl><dt>What</dt><dd>${esc(actionText(d.sent_action || d.proposed_action))}${!d.sent_action ? " (proposal only; submission not recorded)" : ""}</dd><dt>Why</dt><dd>${esc(why)}</dd><dt>How</dt><dd>${esc(how)}</dd></dl><p class="step-meta">Reason code: ${esc(d.reason || "not recorded")}<br>Before decision: ${seconds(d.thread_seconds_left)} left on thread · ${seconds(d.wave_seconds_left)} left in wave.</p></div>
    ${d.deadline_override ? '<p class="event-alert">Deadline override: the runner replaced the proposed action before submission. Compare the two records below.</p>' : ""}
    ${d.elapsed_over_budget ? '<p class="event-alert">The decision exceeded its available time budget.</p>' : ""}
    ${d.submission_error ? `<p class="event-alert">Submission rejected: status ${esc(d.submission_error)}. The submitted action did not complete successfully.</p>` : ""}
    ${d.error ? `<p class="event-alert">Recorded error: ${esc(d.error)}</p>` : ""}
    <p class="hint">${reply?.status ? `Supplier reply: ${esc(label(reply.status))}${numeric(reply.unit_price) ? ` · ${euro(reply.unit_price)} per unit` : ""}.` : "No supplier reply was recorded for this step."}</p>
    <details><summary>Proposed action</summary><pre class="action-data">${esc(d.proposed_action ? JSON.stringify(d.proposed_action,null,2) : "Not recorded")}</pre></details>
    <details><summary>Submitted action</summary><pre class="action-data">${esc(d.sent_action ? JSON.stringify(d.sent_action,null,2) : "Not recorded")}</pre></details>`;
}

$("refresh").addEventListener("click", refresh);
$("help").addEventListener("click", () => {
  $("reading-guide").open = true;
  $("reading-guide").scrollIntoView({block:"start"});
  $("reading-guide").querySelector("summary").focus({preventScroll:true});
});
$("report").addEventListener("change", () => { if ($("report").value !== importedName) loadReport($("report").value); });
$("import").addEventListener("change", async (event) => {
  const file = event.target.files[0]; if (!file) return;
  const request = ++loadId;
  try {
    if (file.size > 32*1024*1024) throw new Error("Report exceeds the 32 MB limit.");
    const data = validate(JSON.parse(await file.text()));
    if (request !== loadId) return;
    importedName = `Imported: ${file.name}`;
    // Keep a single import; the browser never sends its contents to the server.
    for (const option of [...$("report").options]) if (option.value.startsWith("Imported: ")) option.remove();
    $("report").add(new Option(importedName, importedName)); $("report").value = importedName;
    setReport(data, importedName);
  } catch (error) { status(`Could not open JSON: ${error.message} ${report ? "The previous run is still shown." : ""}`, true); }
  event.target.value = "";
});
for (const id of ["strategy","scenario","wave","outcome","sort"]) $(id).addEventListener("change", () => {page = 0; render();});
$("search").addEventListener("input", () => {page = 0; render();});
$("reset").addEventListener("click", () => { for (const id of ["strategy","scenario","wave","outcome","search"]) $(id).value = ""; page = 0; render(); });
document.querySelector(".charts").addEventListener("click", (event) => {
  const button = event.target.closest("button[data-filter]"); if (!button) return;
  const select = $(button.dataset.filter); select.value = select.value === button.dataset.value ? "" : button.dataset.value;
  page = 0; render();
});
$("matches").addEventListener("click", (event) => {
  if (event.target.closest("[data-clear-filters]")) { $("reset").click(); $("search").focus({preventScroll:true}); return; }
  const button = event.target.closest("button[data-case]"); if (!button) return;
  selected = Number(button.dataset.case); stepIndex = 0; renderTable(); renderDetail();
  // Preserve keyboard position when replacing the selected table row.
  document.querySelector(`[data-case="${selected}"]`)?.focus({preventScroll:true});
  if (window.matchMedia("(max-width:1050px)").matches) { $("detail").scrollIntoView({block:"start"}); $("detail").focus({preventScroll:true}); }
});
$("detail").addEventListener("click", (event) => {
  const button = event.target.closest("button[data-step]"); if (!button) return;
  stepIndex = Number(button.dataset.step); renderDetail();
  document.querySelector(`[data-step="${stepIndex}"]`)?.focus({preventScroll:true});
});
$("previous").addEventListener("click", () => {page--; renderTable();});
$("next").addEventListener("click", () => {page++; renderTable();});
render(); refresh();
