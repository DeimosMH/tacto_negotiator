"use strict";

const atlas = window.ATLAS;
const byId = new Map(atlas.nodes.map(node => [node.id, node]));
const files = atlas.nodes.filter(node => node.kind === "file");
const mapIds = new Set(atlas.map_ids);
const $ = selector => document.querySelector(selector);
const escapeHTML = value => String(value ?? "").replace(/[&<>"']/g, char => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[char]));
const jsonText = value => typeof value === "string" ? value : JSON.stringify(value, null, 2);
const subgroupId = node => `group:${node.group}/${node.subgroup}`;
const state = {
  selected: null, group: null, guide: null, search: "", full: false, mode: "source",
  expanded: new Set(["src", "src/tacto_negotiator", "src/tacto_negotiator/agents"]),
  bounds: [0, 0, 1000, 620], view: [0, 0, 1000, 620],
};
try {
  const saved = JSON.parse(localStorage.getItem("tacto-atlas-display") || "{}");
  state.full = saved.full === true;
} catch { /* file:// storage is optional; preferences stay in memory. */ }

function saveSettings() {
  try { localStorage.setItem("tacto-atlas-display", JSON.stringify({full: state.full})); }
  catch { /* Browsing still works when storage is unavailable. */ }
}

function routeFor(id) {
  return id.startsWith("group:") ? `#group=${encodeURIComponent(id)}` : `#file=${encodeURIComponent(id)}`;
}

function nodeLink(id, label) {
  return `<a class="node-link" href="${routeFor(id)}">${escapeHTML(label || byId.get(id)?.label || id)}</a>`;
}

function originalLink(id) {
  const encoded = id.split("/").map(encodeURIComponent).join("/");
  if (id.startsWith("docs/")) return encoded.slice(5);
  return location.protocol === "file:" || /\/docs\/(?:index.html)?$/.test(location.pathname) ? `../${encoded}` : null;
}

function fieldsHTML(node) {
  if (!node.fields?.length) return "";
  return `<div class="detail-block"><h3>Internal data / schema</h3><table class="fields"><thead><tr><th scope="col">Name</th><th scope="col">Value or type</th></tr></thead><tbody>${node.fields.map(item => `<tr><td>${escapeHTML(item.name)}</td><td>${typeof item.value === "object" && item.value !== null ? `<pre>${escapeHTML(jsonText(item.value))}</pre>` : escapeHTML(item.value)}${item.description ? `<small>${escapeHTML(item.description)}</small>` : ""}</td></tr>`).join("")}</tbody></table></div>`;
}

function symbolsHTML(node) {
  if (!node.symbols?.length) return "";
  return `<div class="detail-block"><h3>Classes & functions <span class="muted">${node.symbols.length}</span></h3><ul class="symbol-list">${node.symbols.map(symbol => `<li><code>${escapeHTML(symbol.name)}</code> <span class="muted">${escapeHTML(symbol.kind)} · line ${symbol.line}</span><p>${escapeHTML(symbol.description)}</p>${symbol.signature !== symbol.name ? `<div class="state-list">${escapeHTML(symbol.signature)}</div>` : ""}${symbol.state?.length ? `<div class="state-list">Stored attributes: ${escapeHTML(symbol.state.join(", "))}</div>` : ""}</li>`).join("")}</ul></div>`;
}

function explanationHTML(node) {
  return `<p>${escapeHTML(node.role)}</p><p class="why"><strong>Why it exists.</strong> ${escapeHTML(node.why)}</p>${symbolsHTML(node)}${fieldsHTML(node)}`;
}

function renderTree() {
  const query = state.search.toLowerCase().trim();
  const matches = files.filter(node => !query || `${node.id} ${node.role} ${node.why} ${node.symbols.map(symbol => symbol.name).join(" ")} ${JSON.stringify(node.fields)}`.toLowerCase().includes(query));
  const root = {dirs: new Map(), files: []};
  for (const node of matches) {
    let branch = root;
    const parts = node.id.split("/");
    parts.pop();
    parts.forEach((name, index) => {
      if (!branch.dirs.has(name)) branch.dirs.set(name, {path: parts.slice(0, index + 1).join("/"), dirs: new Map(), files: []});
      branch = branch.dirs.get(name);
    });
    branch.files.push(node);
  }
  function branchHTML(branch) {
    const directories = [...branch.dirs].sort(([a], [b]) => a.localeCompare(b)).map(([name, child]) => {
      const count = matches.filter(node => node.id.startsWith(child.path + "/")).length;
      return `<li><details data-directory="${escapeHTML(child.path)}" ${query || state.expanded.has(child.path) ? "open" : ""}><summary>${escapeHTML(name)}<span class="folder-count">${count}</span></summary>${branchHTML(child)}</details></li>`;
    }).join("");
    const leaves = branch.files.sort((a, b) => a.label.localeCompare(b.label)).map(node => {
      const ext = node.label.includes(".") ? node.label.split(".").pop().slice(0, 3) : "·";
      return `<li><button type="button" class="file-button" data-file="${escapeHTML(node.id)}" aria-current="${state.selected === node.id}"><span class="file-icon" aria-hidden="true">${escapeHTML(ext)}</span><span>${escapeHTML(node.label)}</span></button>${state.full ? `<div class="tree-explanation">${explanationHTML(node)}</div>` : ""}</li>`;
    }).join("");
    return `<ul>${directories}${leaves}</ul>`;
  }
  $("#tree").innerHTML = matches.length ? branchHTML(root) : '<p class="empty">No matching files. Try a class name, supplier ID or “cost”.</p>';
  $("#search-status").textContent = query ? `${matches.length} of ${files.length} files match` : "";
  $("#tree").querySelectorAll("details").forEach(detail => detail.addEventListener("toggle", () => {
    if (state.search || !detail.isConnected) return;
    if (detail.open) state.expanded.add(detail.dataset.directory);
    else state.expanded.delete(detail.dataset.directory);
  }));
}

function highlightTree() {
  $("#tree").querySelectorAll("[data-file]").forEach(button => {
    const selected = button.dataset.file === state.selected;
    button.setAttribute("aria-current", String(selected));
    if (selected) {
      let parent = button.parentElement;
      while (parent && parent !== $("#tree")) {
        if (parent.tagName === "DETAILS") { parent.open = true; state.expanded.add(parent.dataset.directory); }
        parent = parent.parentElement;
      }
    }
  });
}

function inlineMarkdown(text, source) {
  const token = /`([^`]+)`|\[([^\]]+)\]\(([^)]+)\)|\*\*([^*]+)\*\*/g;
  let result = "", previous = 0;
  for (const match of text.matchAll(token)) {
    result += escapeHTML(text.slice(previous, match.index));
    if (match[1]) result += `<code>${escapeHTML(match[1])}</code>`;
    else if (match[4]) result += `<strong>${escapeHTML(match[4])}</strong>`;
    else {
      const label = escapeHTML(match[2]);
      let href = "";
      try {
        const url = new URL(match[3], `https://atlas.invalid/${source}`);
        if (url.origin === "https://atlas.invalid") {
          const id = decodeURIComponent(url.pathname.slice(1));
          if (atlas.guides[id]) href = `#guide=${encodeURIComponent(id.slice(5))}`;
          else if (byId.has(id)) href = routeFor(id);
          else if (id === "docs/index.html") href = url.hash || "#home";
          else if (atlas.reports[id] || id.startsWith("materials/")) {
            const original = originalLink(id);
            if (original) href = original + url.hash;
          }
        } else if (["http:", "https:"].includes(url.protocol)) href = url.href;
      } catch { /* Malformed source links are displayed as text. */ }
      result += href ? `<a href="${escapeHTML(href)}" ${href.startsWith("http") ? 'target="_blank" rel="noreferrer"' : ""}>${label}</a>` : label;
    }
    previous = match.index + match[0].length;
  }
  return result + escapeHTML(text.slice(previous));
}

function markdown(text, source) {
  // shortcut: renders the headings, paragraphs, lists, tables and fences used in these guides; use a full parser if authoring grows beyond this subset.
  const lines = text.split("\n"), out = [];
  let i = 0;
  const inline = value => inlineMarkdown(value, source);
  const special = line => /^(#{1,6} |```|\s*[-*] |\d+\. |\|)/.test(line);
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) { i++; continue; }
    if (line.startsWith("```")) {
      const code = []; i++;
      while (i < lines.length && !lines[i].startsWith("```")) code.push(lines[i++]);
      i++;
      out.push(`<pre><code>${escapeHTML(code.join("\n"))}</code></pre>`);
    } else if (/^#{1,6} /.test(line)) {
      const level = line.match(/^#+/)[0].length;
      out.push(`<h${level}>${inline(line.slice(level + 1))}</h${level}>`); i++;
    } else if (line.startsWith("|") && /^\|[\s:|\-]+$/.test(lines[i + 1] || "")) {
      const cells = row => row.trim().replace(/^\||\|$/g, "").split("|").map(cell => cell.trim());
      const head = cells(line); i += 2;
      const rows = [];
      while (i < lines.length && lines[i].startsWith("|")) rows.push(cells(lines[i++]));
      out.push(`<div class="table-scroll"><table><thead><tr>${head.map(cell => `<th scope="col">${inline(cell)}</th>`).join("")}</tr></thead><tbody>${rows.map(row => `<tr>${row.map(cell => `<td>${inline(cell)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`);
    } else if (/^(\s*[-*] |\d+\. )/.test(line)) {
      const ordered = /^\d+\./.test(line), items = [], pattern = ordered ? /^\d+\. / : /^\s*[-*] /;
      while (i < lines.length && pattern.test(lines[i])) items.push(`<li>${inline(lines[i++].replace(pattern, ""))}</li>`);
      const tag = ordered ? "ol" : "ul";
      const start = ordered ? Number(line.match(/^\d+/)[0]) : 1;
      out.push(`<${tag}${ordered && start !== 1 ? ` start="${start}"` : ""}>${items.join("")}</${tag}>`);
    } else {
      const paragraph = [line]; i++;
      while (i < lines.length && lines[i].trim() && !special(lines[i])) paragraph.push(lines[i++]);
      out.push(`<p>${inline(paragraph.join(" "))}</p>`);
    }
  }
  return out.join("\n");
}

function renderInspector() {
  const node = byId.get(state.selected);
  if (node) {
    const connections = atlas.edges.filter(edge => edge.source === node.id || edge.target === node.id);
    const original = originalLink(node.id);
    $("#inspector").innerHTML = `<div class="inspector-card"><span class="eyebrow">${node.kind === "file" ? "FILE EXPLAINED" : "COMPONENT EXPLAINED"}</span><h2>${escapeHTML(node.label)}</h2><div class="tags"><span class="tag ${node.status}">${node.status}</span><span class="tag">${escapeHTML(atlas.groups[node.group].label)}</span></div><div class="file-path">${escapeHTML(node.id)} · ${escapeHTML(node.subgroup)}</div>${explanationHTML(node)}${node.headings?.length ? `<div class="detail-block"><h3>Document contents</h3><p>${node.headings.map(escapeHTML).join(" · ")}</p></div>` : ""}${node.sample ? `<details class="source-details"><summary>Sample / binary structure${node.rows ? ` · ${node.rows.toLocaleString()} total records` : ""}</summary><pre>${escapeHTML(jsonText(node.sample))}</pre></details>` : ""}${node.content ? `<details class="source-details" ${node.id.includes("/correspondence/") || node.id.includes("/contracts/") ? "open" : ""}><summary>${node.id.endsWith(".md") ? "Read document" : "View source"}</summary>${node.id.endsWith(".md") ? `<div class="prose">${markdown(node.content, node.id)}</div>` : `<pre><code>${escapeHTML(node.content)}</code></pre>`}</details>` : ""}<div class="file-actions">${node.kind === "file" && original ? `<a href="${original}" target="_blank" rel="noreferrer">Open original file ↗</a>` : ""}<span class="muted">${connections.length} relationships${node.bytes !== undefined ? ` · ${node.bytes.toLocaleString()} bytes` : ""}</span></div></div>`;
  } else if (state.group) {
    const members = atlas.nodes.filter(item => mapIds.has(item.id) && subgroupId(item) === state.group);
    $("#inspector").innerHTML = `<div class="inspector-card"><span class="eyebrow">LOGICAL SUBGROUP</span><h2>${escapeHTML(members[0]?.subgroup || "Unknown group")}</h2><p>Every member of this subgroup is shown below. Select one to see its own incoming and outgoing interactions.</p><ul class="relationship-list">${members.map(item => `<li>${nodeLink(item.id)}<span class="relation-label">${escapeHTML(item.role)}</span></li>`).join("")}</ul></div>`;
  } else {
    $("#inspector").innerHTML = `<div class="overview-flow"><section class="flow-card"><span class="step">01 / PREPARE</span><h3>Read the supplier's situation</h3><p>Join the data, open the notes and compute claims with their source IDs.</p>${nodeLink("src/tacto_negotiator/data.py", "Explore evidence access →")}</section><section class="flow-card"><span class="step">02 / NEGOTIATE</span><h3>Trade complete packages</h3><p>Scripts handle known cases. A model handles uncertain decisions; code checks every action and compares total cost.</p>${nodeLink("src/tacto_negotiator/agents/routed.py", "Explore the routed buyer →")}</section><section class="flow-card"><span class="step">03 / CLOSE</span><h3>Accept, or choose the alternative</h3><p>Validate the standing offer, standard terms and must-haves before closing.</p><a href="#guide=IMPLEMENTATION.md">Read the implementation plan →</a></section></div><p class="scope-note">${escapeHTML(atlas.scope)}</p>`;
  }
}

function graphData() {
  if (state.selected) {
    const near = atlas.edges.filter(edge => edge.source === state.selected || edge.target === state.selected);
    const ids = new Set([state.selected, ...near.flatMap(edge => [edge.source, edge.target])]);
    return {nodes: atlas.nodes.filter(node => ids.has(node.id)), edges: near, raw: near};
  }
  const included = atlas.nodes.filter(node => mapIds.has(node.id));
  const edges = atlas.edges.filter(edge => mapIds.has(edge.source) && mapIds.has(edge.target));
  if (state.group) {
    const ids = new Set(included.filter(node => subgroupId(node) === state.group).map(node => node.id));
    const near = edges.filter(edge => ids.has(edge.source) || ids.has(edge.target));
    const visible = new Set([...ids, ...near.flatMap(edge => [edge.source, edge.target])]);
    return {nodes: included.filter(node => visible.has(node.id)), edges: near, raw: near};
  }
  if (state.mode === "source") return {nodes: included, edges, raw: edges};
  const groups = new Map();
  included.forEach(node => {
    const id = subgroupId(node);
    if (!groups.has(id)) groups.set(id, {id, label: node.subgroup, group: node.group, subgroup: node.subgroup, kind: "group", status: node.status, count: 0});
    groups.get(id).count++;
  });
  const links = new Map();
  for (const edge of edges) {
    const source = subgroupId(byId.get(edge.source)), target = subgroupId(byId.get(edge.target));
    if (source === target) continue;
    const key = `${source}→${target}`;
    if (!links.has(key)) links.set(key, {source, target, count: 0, relation: "", evidence: "aggregated source relationships"});
    const aggregate = links.get(key);
    aggregate.count++;
    aggregate.relation = `${aggregate.count} relationships; select subgroup to inspect`;
  }
  return {nodes: [...groups.values()], edges: [...links.values()], raw: edges};
}

function layoutGraph(nodes) {
  // Deterministic cluster layout keeps groups readable and avoids a force-layout dependency.
  const clustered = new Map();
  for (const node of nodes) {
    if (!clustered.has(node.group)) clustered.set(node.group, []);
    clustered.get(node.group).push(node);
  }
  const boxes = [], points = new Map();
  if (nodes.every(node => node.kind === "group")) {
    const columns = [20, 20, 20], width = 380, gap = 20;
    [...clustered].sort((a, b) => b[1].length - a[1].length).forEach(([group, members]) => {
      const column = columns.indexOf(Math.min(...columns));
      const x = 20 + column * (width + gap), y = columns[column];
      const height = 45 + Math.ceil(members.length / 2) * 48;
      boxes.push({group, x, y, width, height});
      members.sort((a, b) => a.label.localeCompare(b.label)).forEach((node, index) => {
        points.set(node.id, {x: x + 22 + (index % 2) * 180, y: y + 58 + Math.floor(index / 2) * 48, node});
      });
      columns[column] += height + gap;
    });
    return {points, boxes, bounds: [0, 0, 1220, Math.max(...columns)]};
  }
  const order = Object.keys(atlas.groups).filter(group => clustered.has(group));
  const columns = nodes.length < 12 ? 2 : 3;
  const cellWidth = 225, rowHeight = 70, padding = 30, titleHeight = 48, gap = 28;
  let x = padding, y = padding, rowMax = 0, maxX = 0;
  order.forEach((group, index) => {
    const members = clustered.get(group).sort((a, b) => a.subgroup.localeCompare(b.subgroup) || a.label.localeCompare(b.label));
    const innerColumns = members.length > 30 ? 3 : members.length > 5 ? 2 : 1;
    const width = innerColumns * cellWidth + padding;
    const height = Math.ceil(members.length / innerColumns) * rowHeight + titleHeight + 12;
    if (index && index % columns === 0) { x = padding; y += rowMax + gap; rowMax = 0; }
    boxes.push({group, x, y, width, height});
    members.forEach((node, i) => points.set(node.id, {x: x + 27 + (i % innerColumns) * cellWidth, y: y + titleHeight + 20 + Math.floor(i / innerColumns) * rowHeight, node}));
    x += width + gap;
    maxX = Math.max(maxX, x);
    rowMax = Math.max(rowMax, height);
  });
  return {points, boxes, bounds: [0, 0, maxX, y + rowMax + padding]};
}

function svgElement(name, attrs = {}, text) {
  const element = document.createElementNS("http://www.w3.org/2000/svg", name);
  Object.entries(attrs).forEach(([key, value]) => element.setAttribute(key, value));
  if (text !== undefined) element.textContent = text;
  return element;
}

function renderRelationships(edges) {
  const selected = state.selected;
  const sorted = [...edges].sort((a, b) => (a.source === selected ? -1 : 1) - (b.source === selected ? -1 : 1) || a.relation.localeCompare(b.relation));
  $("#relationships-title").textContent = `${selected ? "Incoming & outgoing relationships" : "Connections in this view"} (${edges.length})`;
  $("#relationships").innerHTML = edges.length ? `<ul class="relationship-list">${sorted.map(edge => `<li>${selected ? `<span class="tag">${edge.source === selected ? "OUTGOING" : "INCOMING"}</span> ` : ""}${nodeLink(edge.source)} <span aria-label="interacts with">→</span> ${nodeLink(edge.target)}<span class="relation-label">${escapeHTML(edge.relation)}</span><span class="evidence">${escapeHTML(edge.evidence)}</span></li>`).join("")}</ul>` : '<p class="muted">No direct relationships recorded for this file in the current catalogue.</p>';
}

function renderGraph() {
  const data = graphData(), layout = layoutGraph(data.nodes), graph = $("#graph");
  graph.querySelectorAll(":scope > :not(title):not(desc)").forEach(child => child.remove());
  const defs = svgElement("defs");
  const marker = svgElement("marker", {id: "arrow", viewBox: "0 0 10 10", refX: 8, refY: 5, markerWidth: 5, markerHeight: 5, orient: "auto-start-reverse"});
  marker.append(svgElement("path", {d: "M 0 0 L 10 5 L 0 10 z", fill: "#78929e"}));
  defs.append(marker); graph.append(defs);
  const clusters = svgElement("g", {"aria-hidden": "true"});
  layout.boxes.forEach(box => {
    clusters.append(svgElement("rect", {class: "map-cluster", x: box.x, y: box.y, width: box.width, height: box.height, rx: 16}));
    clusters.append(svgElement("text", {class: "map-cluster-label", x: box.x + 17, y: box.y + 25}, atlas.groups[box.group].label));
  });
  graph.append(clusters);
  const edgeGroup = svgElement("g", {"aria-hidden": "true"});
  data.edges.forEach(edge => {
    const a = layout.points.get(edge.source), b = layout.points.get(edge.target);
    if (!a || !b) return;
    const dx = b.x - a.x, dy = b.y - a.y, length = Math.hypot(dx, dy) || 1;
    const endX = b.x - dx / length * 13, endY = b.y - dy / length * 13;
    const path = svgElement("path", {class: "map-edge", d: `M ${a.x} ${a.y} Q ${(a.x + b.x) / 2 + dy * .08} ${(a.y + b.y) / 2 - dx * .08} ${endX} ${endY}`, "marker-end": "url(#arrow)"});
    path.append(svgElement("title", {}, `${a.node.label} → ${b.node.label}: ${edge.relation}`));
    edgeGroup.append(path);
  });
  graph.append(edgeGroup);
  const nodesGroup = svgElement("g");
  layout.points.forEach(({x, y, node}) => {
    const color = atlas.groups[node.group].color;
    const group = svgElement("g", {class: `map-node ${state.selected === node.id ? "selected" : ""}`, transform: `translate(${x},${y})`, tabindex: "0", role: "button", "aria-label": `${node.kind === "group" ? "Explore subgroup" : "Explain"} ${node.label}${node.count ? `, ${node.count} items` : ""}`, "data-node": node.id});
    group.append(svgElement("circle", {class: "node-halo", r: 14, stroke: color}));
    group.append(svgElement("circle", {r: node.kind === "group" ? 8 : 6, fill: color}));
    const limit = node.kind === "group" ? 22 : 27;
    const label = node.label.length > limit ? node.label.slice(0, limit - 2) + "…" : node.label;
    group.append(svgElement("text", {x: 20, y: 3}, label));
    const subtitle = node.count ? `${node.count} items` : node.subgroup;
    group.append(svgElement("text", {class: "node-subtitle", x: 20, y: 20}, subtitle.length > 33 ? subtitle.slice(0, 31) + "…" : subtitle));
    group.append(svgElement("title", {}, `${node.id}\n${node.role || `${node.count} files / entities`}`));
    group.addEventListener("click", () => { if (!suppressClick) location.hash = routeFor(node.id); });
    group.addEventListener("keydown", event => {
      if (event.key === "Enter" || event.key === " ") { event.preventDefault(); location.hash = routeFor(node.id); }
    });
    nodesGroup.append(group);
  });
  graph.append(nodesGroup);
  state.bounds = layout.bounds;
  fitGraph();
  $("#graph-count").textContent = `${data.nodes.length} nodes · ${data.edges.length} links`;
  $("#graph-title").textContent = state.selected ? `${byId.get(state.selected).label} · neighborhood` : state.group ? "Subgroup and its connections" : "Project source map";
  $("#map-mode").disabled = Boolean(state.selected || state.group);
  $("#legend").innerHTML = Object.entries(atlas.groups).filter(([id]) => data.nodes.some(node => node.group === id)).map(([, group]) => `<span><i style="background:${group.color}"></i>${escapeHTML(group.label)}</span>`).join("") + '<span>→ interaction direction</span>';
  renderRelationships(data.raw);
}

function setView() {
  $("#graph").setAttribute("viewBox", state.view.join(" "));
}

function fitGraph() {
  state.view = [...state.bounds]; setView();
}

function zoomGraph(factor) {
  const [x, y, w, h] = state.view;
  const ratio = w / state.bounds[2];
  if ((factor < 1 && ratio < .07) || (factor > 1 && ratio > 5)) return;
  state.view = [x + w * (1 - factor) / 2, y + h * (1 - factor) / 2, w * factor, h * factor];
  setView();
}

function readRoute() {
  state.selected = state.group = state.guide = null;
  let hash;
  try { hash = decodeURIComponent(location.hash.slice(1)); } catch { hash = "home"; }
  const [route, section] = hash.split("&section=");
  hash = route || "guide=BEGINNER_GUIDE.md";
  const dealsRoute = hash === "deals" || hash.startsWith("deal=");
  $("#deals-view").hidden = !dealsRoute;
  if (dealsRoute) {
    for (const id of ["#workspace", "#guide", "#guide-layout", "#stats"]) $(id).hidden = true;
    $("#page-heading").innerHTML = "";
    $("#source-explorer").open = false;
    $(".page-tabs").querySelectorAll("a").forEach(link => {
      if (link.dataset.tab === "deals") link.setAttribute("aria-current", "page");
      else link.removeAttribute("aria-current");
    });
    window.DealsView.render(hash, false, section);
    setNavigation(false);
    return;
  }
  if (hash.startsWith("file=") && byId.has(hash.slice(5))) state.selected = hash.slice(5);
  if (hash.startsWith("group=") && atlas.nodes.some(node => mapIds.has(node.id) && subgroupId(node) === hash.slice(6))) state.group = hash.slice(6);
  if (hash.startsWith("guide=") && atlas.guides["docs/" + hash.slice(6)]) state.guide = hash.slice(6);
  const node = byId.get(state.selected), isGuide = Boolean(state.guide);
  $("#workspace").hidden = isGuide;
  $("#guide").hidden = !isGuide;
  $("#guide-layout").hidden = !isGuide;
  $("#source-explorer").open = !isGuide;
  $("#stats").hidden = Boolean(isGuide || state.selected || state.group);
  $("#workspace").classList.toggle("has-selection", Boolean(state.selected));
  $(".page-tabs").querySelectorAll("a").forEach(link => {
    if (link.dataset.tab === (state.guide || "home")) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  });
  if (isGuide) {
    const guide = atlas.guides["docs/" + state.guide];
    $("#page-heading").innerHTML = '<p class="breadcrumb">Documentation <span>/</span> ' + escapeHTML($("[aria-current=page]")?.textContent.replace("Start here", "").trim() || "Reference") + '</p>';
    $("#guide").innerHTML = markdown(guide.content, guide.id);
    const headings = [...$("#guide").querySelectorAll("h2")];
    headings.forEach((heading, index) => { heading.id = `section-${index + 1}`; heading.tabIndex = -1; });
    $("#guide-toc").innerHTML = `<details ${window.matchMedia("(min-width:1151px)").matches ? "open" : ""}><summary>On this page</summary><div>${headings.map(heading => `<a href="#guide=${encodeURIComponent(state.guide)}&section=${heading.id}" ${section === heading.id ? 'aria-current="location"' : ""}>${escapeHTML(heading.textContent)}</a>`).join("")}<a class="toc-source" href="${escapeHTML(originalLink(guide.id))}">Read Markdown ↗</a></div></details>`;
    document.title = `${$("#guide h1")?.textContent || state.guide} · Tacto docs`;
  } else {
    $("#page-heading").innerHTML = node ? `<p class="breadcrumb">${escapeHTML(node.id)}</p><h1>${escapeHTML(node.label)}</h1><p class="intro">${escapeHTML(node.role)}</p>` : state.group ? `<p class="breadcrumb">LOGICAL GROUP / ${escapeHTML(state.group.slice(6))}</p><h1>Follow the connections.</h1><p class="intro">The selected subgroup, its members and the files or systems they interact with.</p>` : '<p class="breadcrumb">TACTO NEGOTIATION ARENA / SYSTEM FIELD GUIDE</p><h1>Understand the system.<br>Follow every connection.</h1><p class="intro">From a supplier’s email to a defensible deal. Explore the active buyer modules, their runtime dependencies, and the evidence behind each decision.</p>';
    if (!node && !state.group) $("#page-heading").insertAdjacentHTML("beforeend", '<p class="intro"><a href="#guide=BEGINNER_GUIDE.md">New here? Start with the beginner guide →</a></p>');
    renderGraph(); renderInspector();
    document.title = `${node?.label || "Project atlas"} · Tacto`;
  }
  highlightTree();
  setNavigation(false);
  if (section) {
    const heading = document.getElementById(section);
    if (heading && $("#guide").contains(heading)) { heading.scrollIntoView(); heading.focus({preventScroll:true}); }
  } else window.scrollTo(0, 0);
}

function setNavigation(open) {
  if (!open && window.matchMedia("(max-width:760px)").matches && $("#sidebar").contains(document.activeElement)) $("#main").focus({preventScroll:true});
  document.body.classList.toggle("nav-open", open);
  $("#nav-toggle").setAttribute("aria-expanded", String(open));
}

let drag = null, suppressClick = false;
$("#graph").addEventListener("pointerdown", event => {
  if (event.target.closest(".map-node") || event.button !== 0) return;
  const matrix = $("#graph").getScreenCTM();
  if (!matrix) return;
  drag = {x: event.clientX, y: event.clientY, scale: matrix.a, view: [...state.view]};
  suppressClick = false;
  $("#graph").setPointerCapture(event.pointerId);
});
$("#graph").addEventListener("pointermove", event => {
  if (!drag) return;
  const dx = event.clientX - drag.x, dy = event.clientY - drag.y;
  if (Math.abs(dx) + Math.abs(dy) > 4) suppressClick = true;
  state.view = [drag.view[0] - dx / drag.scale, drag.view[1] - dy / drag.scale, drag.view[2], drag.view[3]];
  setView();
});
$("#graph").addEventListener("pointerup", () => { drag = null; setTimeout(() => { suppressClick = false; }, 0); });
$("#graph").addEventListener("pointercancel", () => { drag = null; suppressClick = false; });
$("#graph").addEventListener("wheel", event => {
  if (!event.ctrlKey) return;
  event.preventDefault(); zoomGraph(event.deltaY < 0 ? .85 : 1.18);
}, {passive: false});
$("#zoom-in").addEventListener("click", () => zoomGraph(.8));
$("#zoom-out").addEventListener("click", () => zoomGraph(1.25));
$("#fit").addEventListener("click", fitGraph);
$("#reset-map").addEventListener("click", () => { state.mode = "source"; $("#map-mode").value = "source"; if (location.hash === "#home") readRoute(); else location.hash = "home"; });
$("#map-mode").addEventListener("change", event => { state.mode = event.target.value; renderGraph(); });
$("#full-explanations").checked = state.full;
$("#full-explanations").addEventListener("change", event => { state.full = event.target.checked; saveSettings(); renderTree(); });
$("#search").addEventListener("input", event => { state.search = event.target.value; renderTree(); });
$("#nav-toggle").addEventListener("click", () => setNavigation($("#nav-toggle").getAttribute("aria-expanded") !== "true"));
$(".page-tabs").addEventListener("click", event => { if (event.target.closest("a")) setNavigation(false); });
window.matchMedia("(min-width:1151px)").addEventListener("change", event => {
  const contents = $("#guide-toc details");
  if (contents) contents.open = event.matches;
});
$("#tree").addEventListener("click", event => { const button = event.target.closest("[data-file]"); if (button) location.hash = routeFor(button.dataset.file); });
$("#expand-all").addEventListener("click", () => { $("#tree").querySelectorAll("details").forEach(detail => { detail.open = true; state.expanded.add(detail.dataset.directory); }); });
$("#collapse-all").addEventListener("click", () => { state.expanded.clear(); $("#tree").querySelectorAll("details").forEach(detail => { detail.open = false; }); });
document.addEventListener("keydown", event => {
  if (event.key === "/" && !["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName)) { event.preventDefault(); setNavigation(true); $("#source-explorer").open = true; $("#search").focus(); }
  if (event.key === "Escape" && document.body.classList.contains("nav-open")) { setNavigation(false); $("#nav-toggle").focus(); }
});
window.addEventListener("hashchange", readRoute);
$("#file-count").textContent = `${files.length} files`;
$("#stats").innerHTML = [[atlas.stats.modules, "Source files"], [atlas.stats.map_edges, "Source map links"], [atlas.stats.tables, "Evidence tables"], [atlas.stats.files, "Explained files"]].map(([value, label]) => `<div class="stat"><strong>${value}</strong><span>${label}</span></div>`).join("");
renderTree();
readRoute();
