/* Last Day: front end. Vanilla JS + Cytoscape. */
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const pct = (x) => `${Math.round((x || 0) * 100)}%`;
const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

const TEAM_COLORS = ["#7CF2C3", "#A78BFA", "#FFB86B", "#5CC8FF", "#FF7AB6", "#C3F05A", "#5EEAD4", "#F9E26B", "#93A5FF", "#FF9A7A"];
const STATUS_COLOR = { safe: "#5EE6B0", fragile: "#F5B85A", orphaned: "#FF5C6C", gap: "#A78BFA", inherited: "#FF8A5C", none: "#5B6474" };
const DEFAULT_STATUS = { ...STATUS_COLOR };
const STATUS_VAR = { safe: "--safe", fragile: "--fragile", orphaned: "--orphaned", gap: "--gap", inherited: "--inherited" };

const state = {
  graph: "demo", ov: null, removed: [], mode: "leave", sim: null, selectedAsset: null,
  teamColor: {}, cy: null, interview: null, chat: [], status: null,
};

/* ---------------- API ---------------- */
async function api(path, body) {
  const res = await fetch(path, body === undefined ? {} : {
    method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body),
  });
  const j = await res.json();
  showCypher(j.cypher || []);
  if (!j.ok) throw new Error(j.error || "Request failed");
  return j.data;
}
const gapi = (p, body) => api(`/api/${state.graph}/${p}`, body);

function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(t._h);
  t._h = setTimeout(() => t.classList.remove("show"), 2600);
}

function showCypher(list) {
  $("#qCount").textContent = list.length ? `(${list.length} ${list.length === 1 ? "query" : "queries"})` : "";
  $("#drawerBody").innerHTML = list.map((q) =>
    `<div class="cq">${esc(q.cypher)}\n<span>${q.rows} rows in ${q.ms} ms${Object.keys(q.params || {}).length ? " with " + esc(JSON.stringify(q.params)) : ""}</span></div>`).join("");
}
$("#drawerHandle").onclick = () => {
  const d = $("#drawer");
  d.classList.toggle("open");
  $("#drawerHandle").setAttribute("aria-expanded", d.classList.contains("open"));
  requestAnimationFrame(refit);
};
window.addEventListener("resize", () => requestAnimationFrame(refit));

async function poll(jobId, onLog) {
  for (;;) {
    await new Promise((r) => setTimeout(r, 1200));
    const j = await api(`/api/job/${jobId}`);
    onLog && onLog(j.log || []);
    if (j.status === "done") return j.result;
    if (j.status === "error") throw new Error(j.error);
  }
}

/* ---------------- boot ---------------- */
async function boot() {
  try {
    state.status = await api("/api/status");
  } catch (e) {
    $("#hlBig").textContent = "Can't reach the Last Day server.";
    return;
  }
  renderStatus();
  if (!state.status.falkordb) {
    $("#hlBig").textContent = "FalkorDB isn't running.";
    $("#hlSmall").innerHTML = "Start it with <code>docker compose up -d</code>, then reload.";
    return;
  }
  if (!state.status.graphs.includes("demo")) {
    $("#hlBig").textContent = "Building the Acme Pay demo graph…";
    await api("/api/seed", {});
    state.status = await api("/api/status");
  }
  fillGraphSelect();
  await loadGraph(new URLSearchParams(location.search).get("g") || "demo");
}

function renderStatus() {
  const s = state.status;
  $("#statusDots").innerHTML =
    `<b class="${s.falkordb ? "ok" : "bad"}" title="${esc(s.host)} via ${esc(s.backend)}">FalkorDB</b>` +
    `<b class="${s.llm ? "ok" : ""}" title="${s.llm ? "LLM: " + esc(s.llm) : "No LLM key: answers are extractive, still cited"}">${s.llm ? "LLM on" : "No LLM"}</b>`;
}

function fillGraphSelect() {
  const sel = $("#graphSelect");
  const names = state.status.graphs.filter((g) => !g.startsWith("__"));
  sel.innerHTML = names.map((g) => `<option value="${esc(g)}">${esc(prettyGraph(g))}</option>`).join("");
  sel.value = state.graph;
}
function prettyGraph(g) {
  if (g === "demo") return "Acme Pay (demo company)";
  if (g.startsWith("gh__")) return "GitHub: " + g.slice(4).replace("__", "/");
  if (g.startsWith("bt__")) return "Backtest: " + g.slice(4).split("__").slice(0, 2).join("/");
  if (g.startsWith("co__")) return "Company: " + g.slice(4).replace(/_/g, " ");
  return g;
}
$("#graphSelect").onchange = (e) => loadGraph(e.target.value);

async function loadGraph(name) {
  state.graph = name;
  state.removed = [];
  state.sim = null;
  state.selectedAsset = null;
  state.interview = null;
  state.chat = [];
  $("#graphSelect").value = name;
  history.replaceState(null, "", name === "demo" ? "/" : `/?g=${encodeURIComponent(name)}`);
  state.ov = await gapi("overview");
  state.teamColor = {};
  state.ov.teams.forEach((t, i) => (state.teamColor[t.id] = prefs.team[t.id] || TEAM_COLORS[i % TEAM_COLORS.length]));
  renderPeople();
  buildGraph();
  await runSim();
  renderAsk();
  renderBacktest(state.lastBacktest);
}

/* ---------------- people ---------------- */
function initials(n) { return (n || "?").split(/[\s_-]+/).map((x) => x[0]).slice(0, 2).join("").toUpperCase(); }

function renderPeople() {
  const q = ($("#peopleSearch").value || "").toLowerCase();
  const maxK = Math.max(...state.ov.people.map((p) => p.knowledge || 0), 1);
  const teamName = Object.fromEntries(state.ov.teams.map((t) => [t.id, t.name]));
  const groups = {};
  state.ov.people.filter((p) => !q || (p.name + p.id).toLowerCase().includes(q)).forEach((p) => {
    (groups[p.team || ""] ||= []).push(p);
  });
  const order = Object.keys(groups).sort((a, b) => (teamName[a] || a).localeCompare(teamName[b] || b));
  $("#peopleList").innerHTML = order.map((t) => `
    <div class="team-name">${esc(teamName[t] || t || "No team")}</div>
    ${groups[t].map((p) => {
      const on = state.removed.includes(p.id);
      const bg = p.avatar ? `background-image:url('${esc(p.avatar)}')` : `background:${state.teamColor[p.team] || "#8CC8FF"}`;
      return `<button class="person ${p.active ? "" : "gone"} ${state.mode === "away" ? "away" : ""}" data-id="${esc(p.id)}"
                aria-pressed="${on}" ${p.active ? "" : 'title="Already left"'}>
        <span class="avatar" style="${bg}">${p.avatar ? "" : esc(initials(p.name))}</span>
        <span><span class="p-name">${esc(p.name)}</span>
          <span class="p-meta">${p.active ? esc(p.role || "") : "already left"}
            <span class="kbar" title="Knowledge held"><i style="width:${Math.round(100 * (p.knowledge || 0) / maxK)}%"></i></span></span></span>
      </button>`;
    }).join("")}`).join("");
  $$(".person").forEach((b) => (b.onclick = () => togglePerson(b.dataset.id)));
}
$("#peopleSearch").oninput = renderPeople;
$("#btnClear").onclick = () => { state.removed = []; renderPeople(); runSim(); };
$$(".mode-btn").forEach((b) => (b.onclick = () => {
  state.mode = b.dataset.mode;
  $$(".mode-btn").forEach((x) => { x.classList.toggle("on", x === b); x.setAttribute("aria-checked", x === b); });
  renderPeople();
  runSim();
}));

function togglePerson(id) {
  const p = state.ov.people.find((x) => x.id === id);
  if (p && !p.active) { toast(`${p.name} already left. Their knowledge is already gone from coverage.`); return; }
  state.removed = state.removed.includes(id) ? state.removed.filter((x) => x !== id) : [...state.removed, id];
  renderPeople();
  runSim(true);
}

/* ---------------- graph ---------------- */
// Deterministic starting positions so the layout looks the same on every load (good for demos).
function seedPos(id, r) {
  let h = 2166136261;
  for (const c of id) h = Math.imul(h ^ c.charCodeAt(0), 16777619);
  const a = ((h >>> 0) % 3600) / 3600 * Math.PI * 2, d = r * (0.35 + ((h >>> 12) % 100) / 154);
  return { x: Math.cos(a) * d, y: Math.sin(a) * d };
}

function buildGraph() {
  if (state.cy) state.cy.destroy();
  const els = [];
  state.ov.people.forEach((p) => els.push({ group: "nodes", position: seedPos("p:" + p.id, 500), data: { id: "p:" + p.id, pid: p.id, label: p.name.split(" ")[0], type: "person", team: p.team || "", active: p.active, color: state.teamColor[p.team] || TEAM_COLORS[0] } }));
  state.ov.assets.forEach((a) => els.push({ group: "nodes", position: seedPos("a:" + a.id, 300), data: { id: "a:" + a.id, aid: a.id, label: a.name, type: "asset", kind: a.kind, status: "safe", color: STATUS_COLOR.safe, size: 26 + 26 * (a.business || 0) } }));
  const pset = new Set(state.ov.people.map((p) => p.id));
  state.ov.knows.forEach((k, i) => pset.has(k.source) && els.push({ group: "edges", data: { id: "k" + i, source: "p:" + k.source, target: "a:" + k.target, score: k.score, type: "knows" } }));
  state.ov.depends.forEach((d, i) => els.push({ group: "edges", data: { id: "d" + i, source: "a:" + d.source, target: "a:" + d.target, type: "dep", label: "depends on" } }));

  state.cy = cytoscape({
    container: $("#cy"), elements: els, minZoom: 0.15, maxZoom: 3,
    style: [
      { selector: "node", style: { "font-family": "Inter, Atkinson Hyperlegible, sans-serif", color: "#C9D1DC", "font-size": 10, "text-wrap": "wrap", "text-max-width": 96, "min-zoomed-font-size": 7, "transition-property": "opacity", "transition-duration": "0.2s" } },
      { selector: "node[type='person']", style: { width: 16, height: 16, "background-color": "data(color)", "underlay-color": "data(color)", "underlay-opacity": 0.22, "underlay-padding": 5, "underlay-shape": "ellipse", "border-width": 1.5, "border-color": "#0B0D11", label: "data(label)", "text-valign": "bottom", "text-margin-y": 5, "font-size": 9, color: "#8A93A3" } },
      { selector: "node[type='person'][!active]", style: { opacity: 0.3, "underlay-opacity": 0 } },
      { selector: "node[type='asset']", style: { shape: "ellipse", width: "data(size)", height: "data(size)", "background-color": "data(color)", "underlay-color": "data(color)", "underlay-opacity": 0.28, "underlay-padding": 9, "underlay-shape": "ellipse", "border-width": 2, "border-color": "#0B0D11", label: "data(label)", "text-valign": "bottom", "text-margin-y": 7, "font-weight": 600, color: "#E6E9EF", "text-background-color": "#0B0D11", "text-background-opacity": 0.7, "text-background-padding": 2, "text-background-shape": "round-rectangle" } },
      { selector: "node[kind='customer']", style: { shape: "diamond" } },
      { selector: "node[kind='process']", style: { shape: "hexagon" } },
      { selector: "node[kind='module']", style: { shape: "round-rectangle", "underlay-shape": "round-rectangle" } },
      { selector: "edge[type='knows']", style: { width: "mapData(score, 0.1, 1, 0.5, 2.6)", "line-color": "#3A4150", opacity: "mapData(score, 0.1, 1, 0.3, 0.75)", "curve-style": "haystack" } },
      { selector: "edge[type='dep']", style: { width: 1.2, "line-color": "#5B6474", "target-arrow-shape": "triangle", "target-arrow-color": "#5B6474", "curve-style": "bezier", "arrow-scale": 0.7, label: "data(label)", "font-size": 7, color: "#AEB6C4", "text-rotation": "autorotate", "text-background-color": "#1A1E26", "text-background-opacity": 1, "text-background-shape": "round-rectangle", "text-background-padding": 3, "text-border-color": "#2C323D", "text-border-width": 1, "text-border-opacity": 1, "min-zoomed-font-size": 9 } },
      { selector: ".leaving", style: { "background-color": "#0B0D11", "underlay-color": () => STATUS_COLOR.orphaned, "underlay-opacity": 0.35, "border-width": 2, "border-color": () => STATUS_COLOR.orphaned, color: () => STATUS_COLOR.orphaned } },
      { selector: ".away", style: { "background-color": "#0B0D11", "underlay-color": () => STATUS_COLOR.fragile, "underlay-opacity": 0.35, "border-width": 2, "border-color": () => STATUS_COLOR.fragile, color: () => STATUS_COLOR.fragile } },
      { selector: "edge.cut", style: { "line-color": () => STATUS_COLOR.orphaned, opacity: 0.5, "line-style": "dotted" } },
      { selector: "edge.cascade", style: { "line-color": () => STATUS_COLOR.inherited, "target-arrow-color": () => STATUS_COLOR.inherited, width: 2.4, color: "#0B0D11", "text-background-color": () => STATUS_COLOR.inherited } },
      { selector: "node.inherited", style: { "border-width": 3, "border-color": () => STATUS_COLOR.inherited, "border-style": "dashed" } },
      { selector: "node:selected", style: { "underlay-opacity": 0.5, "underlay-padding": 14 } },
      { selector: ".faded", style: { opacity: 0.1 } },
      { selector: ".hid", style: { display: "none" } },
    ],
    layout: { name: "cose", animate: false, randomize: false, nodeRepulsion: () => 14000, idealEdgeLength: (e) => (e.data("type") === "dep" ? 110 : 80), nodeOverlap: 20, gravity: 0.3, numIter: 2000, padding: 40 },
  });
  state.cy.on("tap", "node[type='person']", (e) => togglePerson(e.target.data("pid")));
  state.cy.on("tap", "node[type='asset']", (e) => selectAsset(e.target.data("aid")));
  state.cy.on("mouseover", "node", (e) => {
    const n = e.target;
    state.cy.elements().not(n.closedNeighborhood()).addClass("faded");
  });
  state.cy.on("mouseout", "node", () => state.cy.elements().removeClass("faded"));
  state.cy.on("render viewport", scheduleMinimap);
  state.cy.on("zoom", updateZoomLabel);
  applyHidden();
  updateZoomLabel();
  renderColorPanel();
}

function paintGraph(animate) {
  const cy = state.cy, sim = state.sim;
  if (!cy || !sim) return;
  const removed = new Set(state.removed);
  cy.batch(() => {
    cy.nodes("[type='person']").forEach((n) => {
      n.removeClass("leaving away");
      if (removed.has(n.data("pid"))) n.addClass(state.mode === "away" ? "away" : "leaving");
    });
    cy.edges("[type='knows']").forEach((e) => e.toggleClass("cut", removed.has(e.source().data("pid"))));
    cy.edges("[type='dep']").removeClass("cascade");
    cy.nodes("[type='asset']").removeClass("inherited");
  });
  const byId = Object.fromEntries(sim.assets.map((a) => [a.id, a]));
  const steps = [];
  cy.nodes("[type='asset']").forEach((n) => {
    const a = byId[n.data("aid")];
    if (!a) return;
    n.data("status", a.status);
    let delay = 0;
    if (a.status === "orphaned") delay = 250;
    else if (a.status === "fragile") delay = 500;
    if (a.inherited) delay = 700 + 380 * a.inherited.hops;
    steps.push({ n, delay, a });
  });
  const go = ({ n, a }) => {
    const color = STATUS_COLOR[a.status] || STATUS_COLOR.safe;
    if (animate && !reduceMotion) n.animate({ style: { "background-color": color, "underlay-color": color } }, { duration: 420, complete: () => { n.data("color", color); n.removeStyle("background-color underlay-color"); scheduleMinimap(); } });
    else n.data("color", color);
    if (a.inherited) {
      n.addClass("inherited");
      const path = a.inherited.path || [];
      for (let i = 0; i < path.length - 1; i++) {
        cy.edges(`[type='dep'][source='a:${cssEsc(path[i])}'][target='a:${cssEsc(path[i + 1])}']`).addClass("cascade");
      }
    }
  };
  steps.forEach((s) => (animate && !reduceMotion ? setTimeout(() => go(s), s.delay) : go(s)));
  applyHidden();
}
const cssEsc = (s) => String(s).replace(/(['\\])/g, "\\$1");

/* ---------------- simulation ---------------- */
async function runSim(animate = false) {
  state.sim = await gapi("simulate", { removed: state.removed });
  paintGraph(animate);
  renderHeadline();
  renderImpact();
  if (!state.selectedAsset || !state.removed.length) {
    const top = state.sim.assets.find((a) => a.status === "orphaned") || state.sim.assets.find((a) => a.status === "fragile");
    state.selectedAsset = top ? top.id : null;
  }
  renderHandover();
  renderInterviewIntro();
  if (!$("#colorPanel").hidden) renderColorPanel();
}

function names(ids) {
  const n = ids.map((id) => (state.ov.people.find((p) => p.id === id) || { name: id }).name);
  return n.length <= 1 ? n.join("") : n.slice(0, -1).join(", ") + " and " + n[n.length - 1];
}

function refit() {
  if (!state.cy) return;
  state.cy.resize();
  state.cy.fit(undefined, 40);
}

function renderHeadline() {
  requestAnimationFrame(refit);
  const s = state.sim.summary;
  const h = $("#health");
  if (!state.removed.length) {
    const gaps = s.gap;
    $("#hlBig").textContent = "Pick someone on the left.";
    $("#hlSmall").textContent = gaps
      ? `${gaps} ${gaps === 1 ? "area is" : "areas are"} already a gap today: only people who already left knew them.`
      : "Last Day removes them from the graph and shows what nobody else knows.";
    h.hidden = true;
    return;
  }
  const who = names(state.removed);
  if (state.mode === "away") {
    const thin = s.orphaned + s.fragile;
    $("#hlBig").textContent = thin
      ? `With ${who} on leave, ${thin} ${thin === 1 ? "area has" : "areas have"} thin cover.`
      : `With ${who} on leave, everything is still covered.`;
  } else {
    const verb = state.removed.length > 1 ? "leave" : "leaves";
    $("#hlBig").textContent = s.orphaned
      ? `If ${who} ${verb}, ${s.orphaned} ${s.orphaned === 1 ? "area is" : "areas are"} left with almost nobody who knows ${s.orphaned === 1 ? "it" : "them"}.`
      : s.fragile ? `If ${who} ${verb}, ${s.fragile} ${s.fragile === 1 ? "area gets" : "areas get"} fragile.`
      : `If ${who} ${verb}, someone else already knows everything they know.`;
  }
  const parts = [];
  if (s.fragile && (s.orphaned || state.mode === "away")) parts.push(`${s.fragile} ${s.fragile === 1 ? "becomes" : "become"} fragile`);
  if (s.inherited) parts.push(`${s.inherited} more inherit the risk through dependencies`);
  s.severed_links.forEach((l) => parts.push(`${teamLabel(l.teams[0])} and ${teamLabel(l.teams[1])} lose ${pct(l.share_lost)} of their connection`));
  $("#hlSmall").textContent = parts.length ? cap(parts.join("; ")) + "." : "";
  h.hidden = false;
  $("#hBefore").textContent = Math.round(s.health_before * 100);
  const after = $("#hAfter");
  after.textContent = Math.round(s.health_after * 100);
  const lost = s.health_before - s.health_after;
  after.className = "h-num after " + (lost < 0.02 ? "good" : lost < 0.06 ? "mid" : "");
}
const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1);
const teamLabel = (id) => (state.ov.teams.find((t) => t.id === id) || { name: id }).name;

function covBar(a) {
  const c = STATUS_COLOR[a.status] || STATUS_COLOR.safe;
  return `<div class="covbar" title="Coverage ${pct(a.before)} → ${pct(a.after)}"><div class="b" style="width:${pct(a.before)}"></div><div class="a" style="width:${pct(a.after)};background:${c}"></div></div>`;
}
function statusChip(a) {
  const label = { orphaned: "Orphaned", fragile: "Fragile", safe: "Covered", gap: "Already a gap" }[a.status];
  let html = `<span class="chip ${a.status}">${label}</span>`;
  if (a.inherited && a.status === "safe") html = `<span class="chip inherited">At risk via ${esc(assetName(a.inherited.via))}</span>`;
  return html;
}
const assetName = (id) => (state.ov.assets.find((a) => a.id === id) || { name: id }).name;

function renderImpact() {
  const el = $("#tab-impact"), s = state.sim;
  if (!state.removed.length) {
    const gaps = s.assets.filter((a) => a.status === "gap");
    const single = s.assets.filter((a) => a.bus_factor_before === 1 && a.status !== "gap").slice(0, 8);
    el.innerHTML = `<h2>${esc(state.ov.meta.name || prettyGraph(state.graph))}</h2>
      <p class="lede">${state.ov.people.length} people, ${state.ov.assets.length} systems, customers and processes, built from
        ${state.ov.evidence_counts.reduce((x, c) => x + c.n, 0).toLocaleString()} pieces of evidence
        (${state.ov.evidence_counts.slice(0, 6).map((c) => `${c.n} ${c.kind.replace("_", " ")}s`).join(", ")}).</p>
      <h3>Areas only one person knows</h3>
      ${single.length ? single.map(assetRow).join("") : '<p class="empty">No single points of failure. Nice.</p>'}
      <h3>Already a gap</h3>
      ${gaps.length ? gaps.map(assetRow).join("") : '<p class="empty">Nothing yet.</p>'}
      <h3>People who connect the company</h3>
      ${s.brokers.slice(0, 5).map((b) => `<div class="link-card"><strong>${esc(b.name)}</strong> <span class="small">${esc(teamLabel(b.team))}: betweenness ${b.betweenness}</span></div>`).join("")}
      <p class="small">Computed with ${esc(s.algorithms.pagerank)} and ${esc(s.algorithms.betweenness)}.</p>`;
  } else {
    const affected = s.assets.filter((a) => a.status === "orphaned" || a.status === "fragile" || a.inherited);
    el.innerHTML = `<h2>${state.mode === "away" ? "While they're away" : "What walks out the door"}</h2>
      <p class="lede">Ranked by how critical the area is (PageRank on dependencies plus business weight) times how much coverage disappears.</p>
      ${affected.length ? affected.map(assetRow).join("") : '<p class="empty">Everything they know is also known by someone else.</p>'}
      ${state.mode === "away" ? '<h3>Who covers</h3><div id="coverList" class="small">Working it out…</div>' : ""}
      ${s.team_links.filter((l) => l.share_lost > 0.25).length ? "<h3>Team connections that run through them</h3>" : ""}
      ${s.team_links.filter((l) => l.share_lost > 0.25).map((l) => `<div class="link-card"><strong>${esc(teamLabel(l.teams[0]))} and ${esc(teamLabel(l.teams[1]))}</strong>
        <div class="small">${pct(l.share_lost)} of the collaboration between these teams runs through ${esc(names(l.carriers))}${l.severed ? ". They are effectively the only link." : "."}</div></div>`).join("")}
      ${state.mode === "leave" ? '<div class="actions"><button class="btn primary" id="goInterview">Write their exit interview</button></div>' : ""}`;
    const gi = $("#goInterview");
    if (gi) gi.onclick = () => { switchTab("interview"); generateInterview(); };
    if (state.mode === "away") renderCovers(affected.slice(0, 6).map((a) => a.id));
  }
  $$(".asset-row", el).forEach((r) => (r.onclick = () => selectAsset(r.dataset.id)));
}

function assetRow(a) {
  const sub = [];
  sub.push(state.removed.length ? `Coverage ${pct(a.before)} → ${pct(a.after)}` : `Coverage ${pct(a.before)}`);
  sub.push(`${a.bus_factor_after} ${a.bus_factor_after === 1 ? "person knows" : "people know"} it${state.removed.length ? " after" : ""}`);
  if (a.doc_cov > 0.05) sub.push(`docs cover ${pct(a.doc_cov)}`);
  if (a.inherited) sub.push(`depends on ${assetName(a.inherited.via)} (${a.inherited.hops} ${a.inherited.hops === 1 ? "hop" : "hops"})`);
  return `<div class="asset-row" data-id="${esc(a.id)}" tabindex="0"><span class="a-name">${esc(a.name)}</span>${statusChip(a)}
    ${covBar(a)}<span class="a-sub">${esc(sub.join("; "))}</span></div>`;
}

async function renderCovers(ids) {
  if (!ids.length) { $("#coverList").textContent = "Nothing needs cover."; return; }
  const res = await gapi("covers", { absent: state.removed, assets: ids });
  $("#coverList").innerHTML = res.map((r) => `<div class="link-card"><strong>${esc(r.name)}</strong>
    <div class="small">${r.uncovered ? "Nobody. Plan the leave around this." : r.covers.map((c) => `${esc(c.name)} (${pct(c.score)})`).join(", ")}</div></div>`).join("");
}

/* ---------------- selection + handover ---------------- */
function selectAsset(id) {
  state.selectedAsset = id;
  state.cy.nodes().unselect();
  state.cy.$id("a:" + id).select();
  switchTab("handover");
  renderHandover();
}

async function renderHandover() {
  const el = $("#tab-handover");
  const id = state.selectedAsset;
  if (!id) { el.innerHTML = '<p class="empty">Select an area on the graph to see who could take it over.</p>'; return; }
  const a = state.sim.assets.find((x) => x.id === id);
  el.innerHTML = `<h2>${esc(a.name)}</h2><p class="lede">${esc(a.description || "")}</p>${assetRow(a)}
    <h3>Who knows it</h3><div id="knowers" class="small">Loading…</div>
    <h3>Best successors</h3><div id="cands" class="small">Loading…</div><div id="ticket"></div>`;
  const [kn, cands] = await Promise.all([
    gapi("knowers", { asset: id }),
    gapi("successors", { asset: id, removed: state.removed, k: 3 }),
  ]);
  $("#knowers").innerHTML = kn.map((k) => {
    const tag = !k.active ? " (already left)" : state.removed.includes(k.id) ? (state.mode === "away" ? " (away)" : " (leaving)") : "";
    return `<div class="link-card"><strong>${esc(k.name)}${tag}</strong> <span class="small">${pct(k.score)} familiarity from ${k.n} pieces of evidence${k.example ? `, e.g. ${esc(k.example.replace("_", " "))}` : ""}</span></div>`;
  }).join("") || "Nobody.";
  $("#cands").innerHTML = cands.length ? cands.map((c, i) => `<div class="cand ${i === 0 ? "pick" : ""}">
      <strong>${esc(c.name)}</strong> <span class="small">${esc(teamLabel(c.team))}, fit ${c.fit}</span>
      <ul>${c.reasons.map((r) => `<li>${esc(r)}</li>`).join("")}</ul>
      <div class="actions"><button class="btn small ${i === 0 ? "primary" : ""}" data-succ="${esc(c.id)}">Draft handover to ${esc(c.name.split(" ")[0])}</button></div>
    </div>`).join("") : '<p class="empty">Nobody is close. This needs a hire or a deliberate learning plan.</p>';
  $$("[data-succ]", el).forEach((b) => (b.onclick = () => draftTicket(id, b.dataset.succ)));
}

async function draftTicket(assetId, succ) {
  const t = await gapi("handover", { asset: assetId, removed: state.removed, successor: succ });
  const repo = state.ov.meta.repo;
  $("#ticket").innerHTML = `<h3>${esc(t.title)}</h3>
    ${t.summary ? `<p>${esc(t.summary)}</p>` : ""}
    <div class="md">${md(t.markdown)}</div>
    <div class="actions">
      <button class="btn primary" id="tkDone">Mark handover done</button>
      <button class="btn" id="tkCopy">Copy as Markdown</button>
      ${repo ? '<button class="btn" id="tkIssue">Open as GitHub issue</button>' : ""}
    </div>`;
  $("#tkCopy").onclick = async () => { await navigator.clipboard.writeText(t.markdown); toast("Copied"); };
  $("#tkDone").onclick = async () => {
    const before = state.sim.assets.find((a) => a.id === assetId);
    const r = await gapi("handover/accept", { asset: assetId, successor: succ, removed: state.removed });
    toast(`${assetName(assetId)}: coverage ${pct(before.after)} → ${pct(r.asset.after)}`);
    await refreshOverview();
    await runSim(true);
  };
  const iss = $("#tkIssue");
  if (iss) iss.onclick = async () => {
    const r = await gapi("handover/issue", { repo, title: t.title, body: t.markdown });
    toast(r.ok ? "Issue created" : r.error);
    if (r.ok && r.url) window.open(r.url, "_blank");
  };
}

async function refreshOverview() {
  state.ov = await gapi("overview");
}

/* ---------------- exit interview ---------------- */
function renderInterviewIntro() {
  const el = $("#tab-interview");
  if (state.interview && state.removed.includes(state.interview.person.id)) return;
  const leaving = state.mode === "leave" ? state.removed : [];
  if (!leaving.length) {
    el.innerHTML = '<h2>Exit interview</h2><p class="empty">Select someone who is leaving. Last Day writes questions only about what will be lost, and every question shows where it came from.</p>';
    return;
  }
  el.innerHTML = `<h2>Exit interview</h2>
    <p class="lede">Questions come from the areas that lose coverage. Answers go back into the graph as written knowledge.</p>
    <label class="small">Person <select id="ivPerson">${leaving.map((id) => `<option value="${esc(id)}">${esc(names([id]))}</option>`).join("")}</select></label>
    <div class="actions"><button class="btn primary" id="ivGo">Write the interview</button></div>`;
  $("#ivGo").onclick = generateInterview;
}

async function generateInterview() {
  const el = $("#tab-interview");
  const person = ($("#ivPerson") && $("#ivPerson").value) || state.removed[0];
  if (!person) return;
  el.innerHTML = '<p class="small">Reading their trail…</p>';
  state.interview = await gapi("interview", { person });
  renderInterview();
}

function renderInterview() {
  const el = $("#tab-interview"), iv = state.interview;
  const done = iv.questions.filter((q) => q.status === "answered").length;
  el.innerHTML = `<h2>${esc(iv.person.name)}'s exit interview</h2>
    <p class="lede">${iv.questions.length} questions about ${iv.assets.length} areas. ${done} answered.</p>
    ${iv.questions.map((q, i) => `<div class="q ${q.status === "answered" ? "answered" : ""}" data-i="${i}">
      <div class="q-asset"><span>${esc(q.asset_name)}</span><span>${esc(q.why)}</span></div>
      <p class="q-text">${esc(q.text)}</p>
      ${q.sources.length ? `<ul class="sources">${q.sources.map((s) => `<li><b>${esc(s.kind.replace("_", " "))}</b> ${esc(s.title)}${s.date ? `, ${esc(s.date)}` : ""}${s.channel ? ` in ${esc(s.channel)}` : ""}</li>`).join("")}</ul>` : ""}
      <textarea aria-label="Answer" placeholder="Their answer">${esc(q.answer || "")}</textarea>
      <div class="row"><span class="delta" id="delta${i}"></span><button class="btn small primary" data-save="${i}">${q.status === "answered" ? "Update answer" : "Save answer"}</button></div>
    </div>`).join("")}`;
  $$("[data-save]", el).forEach((b) => (b.onclick = () => saveAnswer(+b.dataset.save)));
}

async function saveAnswer(i) {
  const q = state.interview.questions[i];
  const box = $(`.q[data-i="${i}"] textarea`);
  const text = box.value.trim();
  if (!text) { box.focus(); return; }
  const before = state.sim.assets.find((a) => a.id === q.asset);
  const r = await gapi("answer", { person: state.interview.person.id, asset: q.asset, question_id: q.id, question: q.text, answer: text });
  q.status = "answered";
  q.answer = text;
  await runSim(true);
  renderInterview();
  const after = r.asset;
  $(`#delta${i}`).textContent = `${q.asset_name}: ${pct(before ? before.after : 0)} → ${pct(after.after)}`;
  $(`#delta${i}`).style.color = STATUS_COLOR[after.status];
}

/* ---------------- ask ---------------- */
function renderAsk() {
  const el = $("#tab-ask");
  const demo = state.graph === "demo";
  const sugg = demo ? [
    "If Arjun and Meera are on leave Diwali week, who covers payments?",
    "Why does recon run at 03:40?",
    "What happens if Priya leaves?",
    "Who knows the fraud engine?",
    "How do I rotate the Unity Bank SFTP key?",
  ] : [
    `Who knows ${state.ov.assets[0] ? state.ov.assets[0].name : "the core"}?`,
    state.ov.people[0] ? `What happens if ${state.ov.people[0].name.split(" ")[0]} leaves?` : "",
  ].filter(Boolean);
  el.innerHTML = `<h2>Ask the company brain</h2>
    <p class="lede">Answers come from the graph and cite their sources, including people who have left.</p>
    <div class="suggest">${sugg.map((s) => `<button>${esc(s)}</button>`).join("")}</div>
    <div class="chat" id="chat">${state.chat.join("")}</div>
    <form class="ask-form" id="askForm"><input id="askInput" placeholder="Ask anything about how the company works" aria-label="Question"><button class="btn primary">Ask</button></form>`;
  $$(".suggest button", el).forEach((b) => (b.onclick = () => doAsk(b.textContent)));
  $("#askForm").onsubmit = (e) => { e.preventDefault(); const v = $("#askInput").value.trim(); if (v) doAsk(v); };
}

async function doAsk(q) {
  state.chat.push(`<div class="msg-q">${esc(q)}</div>`);
  renderAsk();
  $("#askInput").disabled = true;
  try {
    const r = await gapi("ask", { question: q, removed: state.removed });
    let html = `<div class="msg-a">`;
    if (r.ghost_of && r.ghost_of.length) html += `<span class="ghost-tag">Ghost mode: answering from the trail of ${esc(r.ghost_of.join(" and "))}</span>`;
    html += `<div>${md(r.answer)}</div>`;
    if (r.sources && r.sources.length) html += `<ul class="sources">${r.sources.map((s) => `<li><b>[${s.n}]</b> ${esc(s.title)}, ${esc(s.author)}${s.author_status === "left" ? " (left)" : s.author_status === "leaving" ? " (leaving)" : ""}, ${esc(s.date)}</li>`).join("")}</ul>`;
    if (r.who_to_ask && r.who_to_ask.length) html += `<p class="small">Ask next: ${r.who_to_ask.map((w) => esc(w.name)).join(", ")}</p>`;
    html += `</div>`;
    state.chat.push(html);
    if (r.mode === "simulate" && r.removed) {
      state.removed = r.removed.map((p) => p.id);
      state.mode = "leave";
      renderPeople();
      await runSim(true);
    }
  } catch (e) {
    state.chat.push(`<div class="msg-a">${esc(e.message)}</div>`);
  }
  renderAsk();
  const c = $("#tab-ask");
  c.scrollTop = c.scrollHeight;
}

/* ---------------- backtest ---------------- */
function renderBacktest(result) {
  const el = $("#tab-backtest");
  const repoDefault = state.ov.meta.repo || "";
  el.innerHTML = `<h2>Backtest on a real departure</h2>
    <p class="lede">Build the graph from data before someone left, predict what gets orphaned, then check what actually happened to PR response times.</p>
    <form class="bt-form" id="btForm">
      <label>Repository <input id="btRepo" value="${esc(result ? result.repo : repoDefault)}" placeholder="owner/repo" required></label>
      <div class="actions"><button type="button" class="btn" id="btFind">Find who went quiet</button></div>
      <div id="btWho"></div>
      <label>Contributor who left (GitHub login) <input id="btLeaver" value="${esc(result ? result.leaver : "")}" required></label>
      <label>Cutoff date (optional, defaults to their last activity) <input id="btCutoff" type="date"></label>
      <div class="actions"><button class="btn primary">Run the backtest</button></div>
    </form>
    <div class="log" id="btLog" aria-live="polite"></div>
    <div id="btResult"></div>`;
  $("#btFind").onclick = findDepartures;
  $("#btForm").onsubmit = async (e) => {
    e.preventDefault();
    const cutoff = $("#btCutoff").value ? Date.parse($("#btCutoff").value) / 1000 : null;
    $("#btLog").textContent = "Starting…";
    try {
      const { job } = await api("/api/backtest", { repo: $("#btRepo").value, leaver: $("#btLeaver").value.trim(), cutoff });
      const res = await poll(job, (log) => ($("#btLog").textContent = log.slice(-1)[0] || ""));
      $("#btLog").textContent = "";
      showBacktest(res);
    } catch (err) { $("#btLog").textContent = err.message; }
  };
  if (result) showBacktest(result);
}

async function findDepartures() {
  $("#btWho").innerHTML = '<p class="small">Reading contributor history…</p>';
  try {
    const { job } = await api("/api/departures", { repo: $("#btRepo").value });
    const list = await poll(job, (log) => ($("#btLog").textContent = log.slice(-1)[0] || ""));
    $("#btLog").textContent = "";
    $("#btWho").innerHTML = `<table class="bt"><tr><th>Contributor</th><th>Activity</th><th>Last seen</th><th></th></tr>
      ${list.map((d) => `<tr><td>${esc(d.login)}${d.went_quiet ? " <span class='chip orphaned'>went quiet</span>" : ""}</td><td>${d.activity}</td>
      <td>${new Date(d.last * 1000).toISOString().slice(0, 10)}</td><td><button type="button" class="btn small" data-login="${esc(d.login)}">Use</button></td></tr>`).join("")}</table>`;
    $$("[data-login]").forEach((b) => (b.onclick = () => ($("#btLeaver").value = b.dataset.login)));
  } catch (e) { $("#btWho").innerHTML = `<p class="empty">${esc(e.message)}</p>`; }
}

function showBacktest(r) {
  state.lastBacktest = r;
  const G = r.groups, risk = G["at risk"], safe = G["safe"];
  const max = Math.max(...Object.values(G).flatMap((g) => [g.pre_median_h || 0, g.post_median_h || 0]), 1);
  const bar = (v, cls) => `<div class="bar-line"><div class="bar ${cls}" style="width:${Math.max(2, (82 * (v || 0)) / max)}%"></div><span>${v ?? "n/a"}h</span></div>`;
  $("#btResult").innerHTML = `<h3>${esc(r.repo)} after ${esc(r.leaver)} left (${new Date(r.cutoff * 1000).toISOString().slice(0, 10)})</h3>
    <p>Predicted from pre-departure data only: <b>${r.prediction.orphaned}</b> orphaned and <b>${r.prediction.fragile}</b> fragile modules.</p>
    <div class="bars">
      <p class="small">Median hours until a PR got its first human response, year before vs year after</p>
      ${["at risk", "safe", "gap"].filter((k) => G[k]).map((k) => `<div class="bar-row"><span>${k === "at risk" ? "Predicted at risk" : k === "safe" ? "Predicted safe" : "Already a gap"} (${G[k].modules})</span>
        <div class="bar-pair">${bar(G[k].pre_median_h, "pre")}${bar(G[k].post_median_h, "post " + (k === "safe" ? "safe" : ""))}</div></div>`).join("")}
    </div>
    ${risk && safe && risk.slowdown && safe.slowdown ? `<p>Predicted at-risk modules slowed down <b>${risk.slowdown}×</b>; predicted safe modules <b>${safe.slowdown}×</b>.</p>` : ""}
    ${risk ? `<p>PRs with no response within 14 days in at-risk modules: ${pct(risk.pre_unanswered)} → <b>${pct(risk.post_unanswered)}</b>.</p>` : ""}
    ${r.successor_hit_rate != null ? `<p><span class="big-stat">${pct(r.successor_hit_rate)}</span><br><span class="small">of the time, the person who actually took over (most PRs, reviews and comments afterwards) was in our top-3 successors (${r.successor_checked} modules checked)</span></p>` : ""}
    <table class="bt"><tr><th>Module</th><th>Predicted</th><th>Median first response</th><th>Actual successor</th></tr>
    ${r.modules.slice(0, 25).map((m) => `<tr><td>${esc(m.module)}</td><td><span class="chip ${m.status}">${m.status}</span></td>
      <td>${m.pre_median_h ?? "n/a"}h → ${m.post_median_h ?? "n/a"}h</td>
      <td>${esc(m.actual_top_reviewer || "")}${m.successor_hit === true ? " (predicted)" : m.successor_hit === false ? " (missed)" : ""}</td></tr>`).join("")}</table>
    <div class="actions"><button class="btn" id="btOpen">Open the pre-departure graph</button></div>`;
  $("#btOpen").onclick = async () => {
    state.status = await api("/api/status");
    fillGraphSelect();
    state.mode = "leave";
    $$(".mode-btn").forEach((x) => { const on = x.dataset.mode === "leave"; x.classList.toggle("on", on); x.setAttribute("aria-checked", on); });
    await loadGraph(r.graph);
    state.removed = [r.leaver];
    renderPeople();
    await runSim(true);
  };
}

/* ---------------- tabs, dialogs, misc ---------------- */
function switchTab(name) {
  $$(".tab").forEach((t) => { const on = t.dataset.tab === name; t.classList.toggle("on", on); t.setAttribute("aria-selected", on); });
  $$(".tab-body").forEach((b) => (b.hidden = b.id !== "tab-" + name));
}
$$(".tab").forEach((t) => (t.onclick = () => switchTab(t.dataset.tab)));

$("#btnRepo").onclick = () => $("#repoDialog").showModal();
$("#repoGo").onclick = async (e) => {
  e.preventDefault();
  const repo = $("#repoInput").value.trim();
  if (!repo) return;
  $("#repoGo").disabled = true;
  try {
    const { job } = await api("/api/ingest", { repo, since_days: +$("#repoDays").value || 730 });
    const res = await poll(job, (log) => ($("#repoLog").innerHTML = log.map(esc).join("<br>")));
    $("#repoDialog").close();
    state.status = await api("/api/status");
    fillGraphSelect();
    await loadGraph(res.graph);
    toast(`Loaded ${res.people} people, ${res.assets} modules, ${res.evidence} pieces of evidence`);
  } catch (err) {
    $("#repoLog").textContent = err.message;
  } finally { $("#repoGo").disabled = false; }
};
$("#btnReset").onclick = async () => {
  await api("/api/seed", {});
  state.status = await api("/api/status");
  fillGraphSelect();
  await loadGraph("demo");
  toast("Demo company reset");
};

/* ---------------- connections: Slack + Notion ---------------- */
const conn = { state: null, preview: null };
const SRC = { github: "GitHub", slack: "Slack", notion: "Notion" };
const num = (n) => (n || 0).toLocaleString();
const when = (t) => (t ? new Date(t * 1000).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "");

$("#btnConnect").onclick = openConnections;
$("#connClose").onclick = () => $("#connDialog").close();

async function openConnections() {
  $("#connDialog").showModal();
  try {
    conn.state = await api("/api/connections");
  } catch (e) {
    $("#connBody").innerHTML = `<p class="empty">${esc(e.message)}</p>`;
    return;
  }
  renderConnections();
  if (!conn.preview) runPreview();
}

function pill(on, text) {
  return `<span class="pill ${on ? "ok" : ""}">${on ? "Connected" : "Not connected"}${text ? " · " + esc(text) : ""}</span>`;
}

function renderConnections() {
  const s = conn.state, sl = s.slack, no = s.notion;
  const slSum = sl.summary ? `${sl.mode === "export" ? "export" : "bot"}, ${num(sl.summary.channels)} channels, ${num(sl.summary.messages)} messages${sl.summary.skipped ? `, ${sl.summary.skipped} skipped` : ""}` : "";
  const noSum = no.summary ? `${num(no.summary.pages)} pages, ${num(no.summary.users)} people` : "";
  $("#connBody").innerHTML = `
  <section class="conn-sec">
    <h3>Company</h3>
    <div class="grid2">
      <label>Name <input id="cnName" value="${esc(s.name)}" placeholder="Acme" autocomplete="off"></label>
      <label>GitHub repo (optional) <input id="cnRepo" value="${esc(s.github_repo)}" placeholder="owner/repo" autocomplete="off"></label>
    </div>
    <p class="small">With a repo, its folders are the areas, and Slack answers and Notion pages are linked to them${s.github_token ? "" : " (fetching a repo needs <code>GITHUB_TOKEN</code> unless it is already cached)"}. Without one, each Slack channel becomes an area.</p>
  </section>
  <section class="conn-sec">
    <div class="conn-head"><h3>Slack</h3>${pill(sl.connected, slSum)}</div>
    <div class="conn-opts">
      <div class="opt">
        <b>Upload an export</b>
        <p class="small">A workspace admin exports it from Settings &amp; administration, Workspace settings, Import/Export Data. Public channels only. No app needed.</p>
        <input type="file" id="slFile" accept=".zip,application/zip" aria-label="Slack export ZIP">
        <div class="actions"><button type="button" class="btn small" id="slUpload">Upload export</button></div>
      </div>
      <div class="opt">
        <b>Or connect a bot</b>
        <p class="small">Create a Slack app for your workspace with the scopes <code>channels:read</code>, <code>channels:history</code>, <code>users:read</code> and <code>users:read.email</code>, install it, invite it to your channels, and paste its Bot User OAuth Token.</p>
        <input type="password" id="slToken" placeholder="${sl.token_set ? "Saved. Paste a new token to replace it" : "xoxb-…"}" autocomplete="off" aria-label="Slack bot token">
        <div class="actions"><button type="button" class="btn small" id="slConnect">Connect and sync</button></div>
      </div>
    </div>
    <label>Workspace URL, for links back to Slack (optional) <input id="slUrl" value="${esc(sl.workspace_url || "")}" placeholder="acme.slack.com" autocomplete="off"></label>
    ${sl.connected ? `<div class="actions"><button type="button" class="btn small" id="slSync">Sync again</button><button type="button" class="btn small ghost" id="slOff">Disconnect</button><span class="small">Last synced ${esc(when(sl.synced))}</span></div>` : ""}
  </section>
  <section class="conn-sec">
    <div class="conn-head"><h3>Notion</h3>${pill(no.connected, noSum)}</div>
    <ol class="small steps">
      <li>Create an integration at notion.so/profile/integrations with “Read content” and “Read user information including email addresses”.</li>
      <li>In Notion, open each page or teamspace to include: ••• menu, Connections, add your integration.</li>
      <li>Paste the integration's secret here.</li>
    </ol>
    <input type="password" id="noToken" placeholder="${no.token_set ? "Saved. Paste a new token to replace it" : "ntn_…"}" autocomplete="off" aria-label="Notion integration token">
    <div class="actions"><button type="button" class="btn small" id="noConnect">Connect and sync</button>
      ${no.connected ? `<button type="button" class="btn small" id="noSync">Sync again</button><button type="button" class="btn small ghost" id="noOff">Disconnect</button><span class="small">Last synced ${esc(when(no.synced))}</span>` : ""}</div>
  </section>
  <section class="conn-sec">
    <div class="actions">
      <button type="button" class="btn" id="cnPreview">Check people</button>
      <button type="button" class="btn primary" id="cnBuild">Build company graph</button>
      ${s.graph ? `<button type="button" class="btn ghost" id="cnOpen">Open ${esc(prettyGraph(s.graph))}</button>` : ""}
    </div>
    <div class="log" id="cnLog" aria-live="polite"></div>
    <div id="cnResult"></div>
  </section>`;

  $("#cnName").onchange = (e) => saveConn({ name: e.target.value });
  $("#cnRepo").onchange = async (e) => {
    if (await saveConn({ github_repo: e.target.value })) { conn.preview = null; renderPreview(); runPreview(); }
  };
  $("#slUrl").onchange = (e) => saveConn({ workspace_url: e.target.value });
  $("#slUpload").onclick = uploadExport;
  $("#slConnect").onclick = () => connectToken("slack", "#slToken");
  $("#noConnect").onclick = () => connectToken("notion", "#noToken");
  if ($("#slSync")) $("#slSync").onclick = () => syncSource("slack");
  if ($("#noSync")) $("#noSync").onclick = () => syncSource("notion");
  if ($("#slOff")) twoClick($("#slOff"), () => disconnectSource("slack"));
  if ($("#noOff")) twoClick($("#noOff"), () => disconnectSource("notion"));
  $("#cnPreview").onclick = runPreview;
  $("#cnBuild").onclick = buildCompany;
  if ($("#cnOpen")) $("#cnOpen").onclick = async () => { $("#connDialog").close(); await loadGraph(s.graph); };
  renderPreview();
}

const connLog = (msg) => { const l = $("#cnLog"); if (l) l.textContent = msg; };
function setBusy(on) { $$("#connBody button").forEach((b) => (b.disabled = on)); }

async function connJob(start, label, onDone) {
  setBusy(true);
  connLog(label);
  try {
    const { job } = await start;
    const res = await poll(job, (l) => connLog(l.slice(-1)[0] || label));
    connLog("");
    await onDone(res);
  } catch (e) {
    connLog(e.message);
  } finally {
    setBusy(false);
  }
}

async function saveConn(patch) {
  try {
    conn.state = await api("/api/connections/settings", patch);
    connLog("Saved.");
    return true;
  } catch (e) {
    connLog(e.message);
    return false;
  }
}

async function refreshConn() {
  conn.state = await api("/api/connections");
  conn.preview = null;
  renderConnections();
}

async function uploadExport() {
  const f = $("#slFile").files[0];
  if (!f) { connLog("Choose the export .zip first."); return; }
  setBusy(true);
  connLog(`Uploading ${f.name} (${(f.size / 1048576).toFixed(1)} MB) and reading it…`);
  try {
    const res = await fetch("/api/connections/slack/export", { method: "POST", headers: { "content-type": "application/zip" }, body: f });
    const j = await res.json();
    if (!j.ok) throw new Error(j.error || "Upload failed");
    conn.state = j.data;
    conn.preview = null;
    renderConnections();
    toast("Slack export loaded");
    await runPreview();
  } catch (e) {
    connLog(e.message);
    setBusy(false);
  }
}

function connectToken(source, input) {
  const token = $(input).value.trim();
  if (!token) { connLog("Paste the token first."); $(input).focus(); return; }
  connJob(api("/api/connections/token", { source, token }), `Connecting ${SRC[source]} and reading it…`, async () => {
    await refreshConn();
    toast(`${SRC[source]} synced`);
    await runPreview();
  });
}

function syncSource(source) {
  connJob(api("/api/connections/sync", { source }), `Syncing ${SRC[source]}…`, async () => {
    await refreshConn();
    toast(`${SRC[source]} synced`);
    await runPreview();
  });
}

function twoClick(btn, fn) {
  btn.onclick = () => {
    if (btn.dataset.armed) return fn();
    btn.dataset.armed = "1";
    btn.textContent = "Click again to disconnect";
    setTimeout(() => { delete btn.dataset.armed; btn.textContent = "Disconnect"; }, 4000);
  };
}

async function disconnectSource(source) {
  conn.state = await api("/api/connections/disconnect", { source });
  conn.preview = null;
  renderConnections();
  toast(`${SRC[source]} disconnected`);
  runPreview();
}

async function runPreview() {
  const s = conn.state;
  if (!s.slack.connected && !s.github_repo) { conn.preview = null; renderPreview(); return; }
  await connJob(api("/api/connections/preview", {}), "Matching people across tools…", (res) => {
    conn.preview = res;
    renderPreview();
  });
}

function buildCompany() {
  connJob(api("/api/connections/build", {}), "Building the company graph…", async (res) => {
    conn.preview = res;
    conn.state = await api("/api/connections");
    state.status = await api("/api/status");
    fillGraphSelect();
    $("#connDialog").close();
    await loadGraph(res.graph);
    toast(`Built: ${res.counts.people} people, ${res.counts.assets} areas, ${num(res.counts.evidence)} pieces of evidence`);
  });
}

function renderPreview() {
  const el = $("#cnResult"), p = conn.preview;
  if (!el) return;
  if (!p) {
    el.innerHTML = conn.state.slack.connected || conn.state.github_repo ? "" :
      '<p class="empty">Connect Slack (or add a GitHub repo) to start. Notion pages need areas from one of them.</p>';
    return;
  }
  const c = p.counts;
  const kinds = Object.entries(c.by_kind || {}).sort((a, b) => b[1] - a[1]).map(([k, n]) => `${num(n)} ${k.replace("_", " ")}${n === 1 ? "" : "s"}`).join(", ");
  const hasRepo = c.sources.includes("github");
  const kept = p.people.filter((r) => r.kept);
  let html = `<p class="small conn-sum">${num(c.people)} people from ${num(c.accounts)} accounts (${num(c.merged)} matched across tools), ${num(c.assets)} areas and ${num(c.evidence)} pieces of evidence: ${esc(kinds)}.</p>
    <h3>People</h3>
    <p class="small">Accounts are joined by email, by a Slack handle equal to a GitHub login, or by the same full name. Fix anything wrong here: it is saved and used every time you build.</p>
    <div class="tbl-wrap"><table class="bt people-tbl"><tr><th>Person</th><th>Accounts</th><th>Activity</th><th>Fix</th></tr>
    ${p.people.map((r) => personRow(r, kept)).join("")}</table></div>`;
  if (p.channels.length) {
    html += `<h3>Slack channels</h3>
      <p class="small">${hasRepo ? "An answer is linked to an area when it names one. Pick an area for a channel to link all of its answers." : "Every channel with threads is an area, except chatter channels like #general."}</p>
      <div class="tbl-wrap"><table class="bt"><tr><th>Channel</th><th>Answers</th><th>Linked</th>${hasRepo ? "<th>Area</th>" : ""}</tr>
      ${p.channels.map((ch) => channelRow(ch, hasRepo, p.assets)).join("")}</table></div>`;
  }
  if (p.notion && p.notion.pages) {
    const un = p.notion.unlinked || [];
    html += `<h3>Notion</h3><p class="small">${num(p.notion.linked)} of ${num(p.notion.pages)} pages are about an area (${num(p.notion.decisions)} ${p.notion.decisions === 1 ? "decision" : "decisions"}).${un.length ? " Not about any area: " + esc(un.slice(0, 6).join(", ")) + (un.length > 6 ? "…" : "") + "." : ""}</p>`;
  }
  el.innerHTML = html;
  $$("[data-link]", el).forEach((sel) => (sel.onchange = () => sel.value && savePeople({ link: [sel.dataset.link, sel.value] })));
  $$("[data-split]", el).forEach((b) => (b.onclick = () => savePeople({ split: b.dataset.split })));
  $$("[data-ch]", el).forEach((sel) => (sel.onchange = () => saveChannel(sel.dataset.ch, sel.value)));
}

function personRow(r, kept) {
  const solo = r.accounts.length === 1;
  const accs = r.accounts.map((a) => `<span class="acct ${a.source}" title="${esc(a.name + (a.email ? " · " + a.email : ""))}">${SRC[a.source]} <b>${esc(a.label)}</b>${a.matched_by ? ` <i>by ${esc(a.matched_by)}</i>` : ""}${a.matched_by && !solo ? ` <button type="button" class="x" data-split="${esc(a.key)}" title="Not the same person" aria-label="${esc(a.label)} is not ${esc(r.name)}">×</button>` : ""}</span>`).join("");
  const ev = ["github", "slack", "notion"].filter((s) => r.evidence[s]).map((s) => `${SRC[s]} ${num(r.evidence[s])}`).join(", ") || "none";
  const note = !r.kept ? "too little activity to include" : r.left_slack ? "left (Slack account deactivated)" : r.active ? "" : "inactive";
  const others = kept.filter((o) => o.id !== r.id);
  const sug = new Set(r.suggest || []);
  const ordered = [...others.filter((o) => sug.has(o.id)), ...others.filter((o) => !sug.has(o.id))];
  const canLink = !r.accounts.some((a) => a.source === "github") || solo;
  const fix = canLink && ordered.length ? `<select data-link="${esc(r.key)}" aria-label="${esc(r.name)} is the same person as">
      <option value="">Same person as…</option>${ordered.map((o) => `<option value="${esc(o.key)}">${esc(o.name)}${sug.has(o.id) ? " (suggested)" : ""}</option>`).join("")}</select>` : "";
  return `<tr class="${r.kept ? "" : "dim"}"><td><strong>${esc(r.name)}</strong>${note ? `<div class="small">${esc(note)}</div>` : ""}${sug.size ? `<div class="small hint">Maybe ${esc([...sug].join(", "))} on GitHub?</div>` : ""}</td>
    <td>${accs}</td><td class="small">${ev}</td><td>${fix}</td></tr>`;
}

function channelRow(ch, hasRepo, assets) {
  const cur = (conn.state.channel_map[ch.channel] || [])[0] || "";
  return `<tr><td>#${esc(ch.channel)}${ch.skipped ? `<div class="small">${esc(ch.skipped)}</div>` : ""}</td><td>${num(ch.answers)}</td><td>${num(ch.linked)}</td>
    ${hasRepo ? `<td><select data-ch="${esc(ch.channel)}" aria-label="Area for #${esc(ch.channel)}"><option value="">Detect from text</option>
      ${assets.map((a) => `<option value="${esc(a.id)}" ${cur === a.id ? "selected" : ""}>${esc(a.name)}</option>`).join("")}</select></td>` : ""}</tr>`;
}

async function savePeople({ link, split }) {
  const links = { ...(conn.state.links || {}) };
  const separate = new Set(conn.state.separate || []);
  if (link) { links[link[0]] = link[1]; separate.delete(link[0]); }
  if (split) {
    if (links[split]) delete links[split];
    else separate.add(split);
    for (const [k, v] of Object.entries(links)) if (v === split) delete links[k];
  }
  conn.state = await api("/api/connections/people", { links, separate: [...separate] });
  await runPreview();
}

async function saveChannel(ch, assetId) {
  const map = { ...(conn.state.channel_map || {}) };
  if (assetId) map[ch] = [assetId];
  else delete map[ch];
  conn.state = await api("/api/connections/channels", { channel_map: map });
  await runPreview();
}

/* ---------------- colors, filters, minimap, zoom ---------------- */
const PREF_KEY = "lastday.colors.v1";
const prefs = (() => {
  try {
    const p = JSON.parse(localStorage.getItem(PREF_KEY) || "{}");
    return { status: p.status || {}, team: p.team || {}, hidden: p.hidden || {} };
  } catch (e) {
    return { status: {}, team: {}, hidden: {} };
  }
})();
function savePrefs() { try { localStorage.setItem(PREF_KEY, JSON.stringify(prefs)); } catch (e) { /* private window */ } }

function applyStatusColors() {
  for (const k of Object.keys(STATUS_VAR)) {
    STATUS_COLOR[k] = prefs.status[k] || DEFAULT_STATUS[k];
    document.documentElement.style.setProperty(STATUS_VAR[k], STATUS_COLOR[k]);
  }
}
applyStatusColors();

const STATUS_LABEL = { safe: "Covered", fragile: "Fragile", orphaned: "Orphaned", gap: "Already a gap", inherited: "At risk through a dependency" };
const KIND_LABEL = { system: "Systems", module: "Code modules", customer: "Customers", process: "Processes" };
const EYE = '<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z" fill="none" stroke="currentColor" stroke-width="1.8"/><circle cx="12" cy="12" r="3" fill="none" stroke="currentColor" stroke-width="1.8"/></svg>';
const EYE_OFF = '<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><path d="M3 3l18 18M10.6 5.1A10.8 10.8 0 0 1 12 5c6.4 0 10 7 10 7a17 17 0 0 1-3.2 4M6.6 6.6C3.8 8.4 2 12 2 12s3.6 7 10 7c1.6 0 3-.4 4.3-1" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>';

function isHidden(ele) {
  const h = prefs.hidden;
  if (ele.isEdge()) {
    if (h["edge:" + ele.data("type")]) return true;
    return isHidden(ele.source()) || isHidden(ele.target());
  }
  if (ele.data("type") === "person") return !!h["team:" + ele.data("team")];
  return !!(h["kind:" + ele.data("kind")] || h["status:" + ele.data("status")]);
}

function applyHidden() {
  const cy = state.cy;
  if (!cy) return;
  cy.batch(() => {
    cy.nodes().forEach((n) => n.toggleClass("hid", isHidden(n)));
    cy.edges().forEach((e) => e.toggleClass("hid", isHidden(e)));
  });
  scheduleMinimap();
}

function eyeBtn(key, label) {
  const off = !!prefs.hidden[key];
  return `<button type="button" class="eye ${off ? "off" : ""}" data-eye="${esc(key)}" aria-pressed="${!off}" aria-label="${off ? "Show" : "Hide"} ${esc(label)}" title="${off ? "Show" : "Hide"}">${off ? EYE_OFF : EYE}</button>`;
}

function renderColorPanel() {
  const el = $("#colorPanel");
  if (!el || !state.ov) return;
  const counts = {};
  (state.sim ? state.sim.assets : []).forEach((a) => (counts[a.status] = (counts[a.status] || 0) + 1));
  const teamCount = {};
  state.ov.people.forEach((p) => (teamCount[p.team || ""] = (teamCount[p.team || ""] || 0) + 1));
  const kinds = [...new Set(state.ov.assets.map((a) => a.kind))];
  const row = (swatch, label, n, eye) => `<div class="cp-row">${swatch}<span class="cp-label">${esc(label)}</span><span class="cp-n">${n ?? ""}</span>${eye || ""}</div>`;
  const swatch = (attr, color, label) => `<label class="sw" style="--c:${esc(color)}" title="Change the color of ${esc(label)}"><input type="color" ${attr} value="${esc(color)}" aria-label="Color for ${esc(label)}"></label>`;
  el.innerHTML = `
    <div class="cp-head"><b>Colors and filters</b><button type="button" class="cp-x" id="cpClose" aria-label="Close">×</button></div>
    <div class="cp-sec"><div class="cp-title">Areas by status</div>
      ${["safe", "fragile", "orphaned", "gap"].map((k) => row(swatch(`data-status="${k}"`, STATUS_COLOR[k], STATUS_LABEL[k]), STATUS_LABEL[k], counts[k] || 0, eyeBtn("status:" + k, STATUS_LABEL[k]))).join("")}
      ${row(swatch('data-status="inherited"', STATUS_COLOR.inherited, STATUS_LABEL.inherited), "Risk via dependency", "", "")}
    </div>
    <div class="cp-sec"><div class="cp-title">People by team</div>
      ${state.ov.teams.map((t) => row(swatch(`data-team="${esc(t.id)}"`, state.teamColor[t.id] || TEAM_COLORS[0], t.name), t.name, teamCount[t.id] || 0, eyeBtn("team:" + t.id, t.name))).join("")}
    </div>
    <div class="cp-sec"><div class="cp-title">Area types</div>
      ${kinds.map((k) => row(`<span class="shape-key ${({ customer: "di", process: "hx", module: "sq" })[k] || "ci"}"></span>`, KIND_LABEL[k] || k, state.ov.assets.filter((a) => a.kind === k).length, eyeBtn("kind:" + k, KIND_LABEL[k] || k))).join("")}
    </div>
    <div class="cp-sec"><div class="cp-title">Links</div>
      ${row('<span class="ln knows"></span>', "Who knows what", state.ov.knows.length, eyeBtn("edge:knows", "who-knows links"))}
      ${row('<span class="ln dep"></span>', "Depends on", state.ov.depends.length, eyeBtn("edge:dep", "dependency links"))}
    </div>
    <div class="cp-foot"><button type="button" class="btn small ghost" id="cpReset">Reset colors and filters</button></div>`;
  $("#cpClose").onclick = () => toggleColorPanel(false);
  $$("[data-status]", el).forEach((i) => (i.oninput = () => {
    prefs.status[i.dataset.status] = i.value;
    i.parentElement.style.setProperty("--c", i.value);
    applyStatusColors();
    if (state.cy) { state.cy.style().update(); paintGraph(false); }
    scheduleMinimap();
    savePrefs();
  }));
  $$("[data-team]", el).forEach((i) => (i.oninput = () => {
    const t = i.dataset.team;
    prefs.team[t] = state.teamColor[t] = i.value;
    i.parentElement.style.setProperty("--c", i.value);
    if (state.cy) state.cy.nodes(`[type='person'][team='${cssEsc(t)}']`).data("color", i.value);
    scheduleMinimap();
    savePrefs();
  }));
  $$("[data-team]", el).forEach((i) => (i.onchange = () => renderPeople()));
  $$("[data-eye]", el).forEach((b) => (b.onclick = () => {
    const k = b.dataset.eye;
    if (prefs.hidden[k]) delete prefs.hidden[k];
    else prefs.hidden[k] = true;
    savePrefs();
    applyHidden();
    renderColorPanel();
  }));
  $("#cpReset").onclick = () => {
    prefs.status = {}; prefs.team = {}; prefs.hidden = {};
    savePrefs();
    applyStatusColors();
    state.ov.teams.forEach((t, i) => (state.teamColor[t.id] = TEAM_COLORS[i % TEAM_COLORS.length]));
    if (state.cy) state.cy.nodes("[type='person']").forEach((n) => n.data("color", state.teamColor[n.data("team")] || TEAM_COLORS[0]));
    renderPeople();
    if (state.cy) { state.cy.style().update(); paintGraph(false); }
    renderColorPanel();
  };
}

function toggleColorPanel(open) {
  const p = $("#colorPanel"), b = $("#tColors");
  const show = open ?? p.hidden;
  p.hidden = !show;
  b.setAttribute("aria-pressed", show);
  if (show) renderColorPanel();
}
$("#tColors").onclick = () => toggleColorPanel();
$("#tLayout").onclick = () => {
  if (!state.cy) return;
  state.cy.layout({ name: "cose", animate: !reduceMotion, animationDuration: 600, randomize: true, nodeRepulsion: () => 14000, idealEdgeLength: (e) => (e.data("type") === "dep" ? 110 : 80), gravity: 0.3, numIter: 1500, padding: 40 }).run();
};

function zoomBy(f) {
  const cy = state.cy;
  if (!cy) return;
  cy.animate({ zoom: { level: Math.min(cy.maxZoom(), Math.max(cy.minZoom(), cy.zoom() * f)), renderedPosition: { x: cy.width() / 2, y: cy.height() / 2 } } }, { duration: reduceMotion ? 0 : 180 });
}
$("#zIn").onclick = () => zoomBy(1.25);
$("#zOut").onclick = () => zoomBy(0.8);
$("#zFit").onclick = () => refit();
function updateZoomLabel() { if (state.cy) $("#zPct").textContent = Math.round(state.cy.zoom() * 100) + "%"; }

let miniQueued = false;
function scheduleMinimap() {
  if (miniQueued) return;
  miniQueued = true;
  requestAnimationFrame(() => { miniQueued = false; drawMinimap(); });
}
function miniFrame() {
  const cy = state.cy, cv = $("#mini");
  const bb = cy.elements(":visible").boundingBox();
  const pad = 8, w = cv.width, h = cv.height;
  const s = Math.min((w - 2 * pad) / (bb.w || 1), (h - 2 * pad) / (bb.h || 1));
  const ox = pad + (w - 2 * pad - bb.w * s) / 2 - bb.x1 * s, oy = pad + (h - 2 * pad - bb.h * s) / 2 - bb.y1 * s;
  return { s, ox, oy };
}
function drawMinimap() {
  const cy = state.cy, cv = $("#mini");
  if (!cy || !cv || cv.offsetParent === null) return;
  const ctx = cv.getContext("2d");
  const { s, ox, oy } = miniFrame();
  ctx.clearRect(0, 0, cv.width, cv.height);
  ctx.strokeStyle = "rgba(140,150,170,.18)";
  ctx.lineWidth = 0.6;
  ctx.beginPath();
  cy.edges(":visible").forEach((e) => {
    const a = e.source().position(), b = e.target().position();
    ctx.moveTo(ox + a.x * s, oy + a.y * s);
    ctx.lineTo(ox + b.x * s, oy + b.y * s);
  });
  ctx.stroke();
  cy.nodes(":visible").forEach((n) => {
    const p = n.position();
    ctx.fillStyle = n.data("color") || "#8A93A3";
    ctx.beginPath();
    ctx.arc(ox + p.x * s, oy + p.y * s, n.data("type") === "asset" ? 2.6 : 1.5, 0, Math.PI * 2);
    ctx.fill();
  });
  const ext = cy.extent();
  ctx.strokeStyle = "rgba(230,233,239,.85)";
  ctx.lineWidth = 1.2;
  ctx.strokeRect(ox + ext.x1 * s, oy + ext.y1 * s, ext.w * s, ext.h * s);
}
(() => {
  const cv = $("#mini");
  let drag = false;
  const panTo = (ev) => {
    const cy = state.cy;
    if (!cy) return;
    const r = cv.getBoundingClientRect();
    const { s, ox, oy } = miniFrame();
    const mx = ((ev.clientX - r.left) * (cv.width / r.width) - ox) / s, my = ((ev.clientY - r.top) * (cv.height / r.height) - oy) / s;
    cy.pan({ x: cy.width() / 2 - mx * cy.zoom(), y: cy.height() / 2 - my * cy.zoom() });
  };
  cv.addEventListener("pointerdown", (e) => { drag = true; cv.setPointerCapture(e.pointerId); panTo(e); });
  cv.addEventListener("pointermove", (e) => drag && panTo(e));
  cv.addEventListener("pointerup", () => (drag = false));
})();

function md(src) {
  const lines = esc(src || "").split("\n");
  let html = "", inList = false;
  const inline = (s) => s.replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/(https?:\/\/[^\s)]+)/g, '<a href="$1" target="_blank" rel="noopener">$1</a>');
  for (const l of lines) {
    const li = l.match(/^\s*[-*] (\[ \] )?(.*)/);
    if (li) { if (!inList) { html += "<ul>"; inList = true; } html += `<li>${li[1] ? "☐ " : ""}${inline(li[2])}</li>`; continue; }
    if (inList) { html += "</ul>"; inList = false; }
    if (l.startsWith("### ")) html += `<h3>${inline(l.slice(4))}</h3>`;
    else if (l.startsWith("## ")) html += `<h2>${inline(l.slice(3))}</h2>`;
    else if (l.trim()) html += `<p>${inline(l)}</p>`;
  }
  return html + (inList ? "</ul>" : "");
}

boot();
