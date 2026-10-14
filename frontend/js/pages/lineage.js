// Lineage: SVG graph laid out left→right by depth (manual layout), curved labelled edges, drag to pan,
// click a node for details + downstream impact.
import { api } from "../api.js";
import { svg } from "../components/charts.js";
import { badge, chainBadge, clear, empty, fmtNum, h, loading, scoreBadge, shortSha, statusBadge } from "../components/dom.js";
import { toast } from "../components/toast.js";

// COL_W leaves a 164px gap between columns so edge labels (≤ LABEL_MAX chars) never overlap nodes.
const COL_W = 340, ROW_H = 100, NODE_W = 176, NODE_H = 66, PAD = 44, LABEL_MAX = 24;
const CHAIN_DOT = { registered: "ok", pending: "warn", failed: "bad" };
const ALGO_SHORT = { logistic_regression: "LogReg", random_forest_classifier: "RF classifier",
  linear_regression: "LinReg", random_forest_regressor: "RF regressor" };
const ZOOM_MIN = 0.4, ZOOM_MAX = 2.5;

/** Assign each node a {depth, row}. Versions: BFS over derived edges from roots; experiments: version depth + 1. */
export function layout(graph) {
  const nodes = new Map(graph.nodes.map((n) => [n.id, { ...n, depth: 0, row: 0 }]));
  const children = new Map(), parents = new Map();
  for (const e of graph.edges) {
    if (!children.has(e.source)) children.set(e.source, []);
    children.get(e.source).push(e.target);
    parents.set(e.target, e.source);
  }
  const roots = [...nodes.values()].filter((n) => n.type === "version" && !parents.has(n.id));
  const queue = roots.map((n) => n.id), seen = new Set(queue);
  while (queue.length) {
    const id = queue.shift();
    for (const c of children.get(id) || []) {
      if (seen.has(c) || !nodes.has(c)) continue;
      nodes.get(c).depth = nodes.get(id).depth + 1;
      seen.add(c);
      queue.push(c);
    }
  }
  // Rows: place each column in parent order so edges cross as little as possible.
  const columns = new Map();
  for (const n of nodes.values()) {
    if (!columns.has(n.depth)) columns.set(n.depth, []);
    columns.get(n.depth).push(n);
  }
  for (const depth of [...columns.keys()].sort((a, b) => a - b)) {
    const col = columns.get(depth);
    col.sort((a, b) => {
      const pa = nodes.get(parents.get(a.id))?.row ?? 0, pb = nodes.get(parents.get(b.id))?.row ?? 0;
      return pa - pb || (a.type === b.type ? 0 : a.type === "version" ? -1 : 1) || a.id.localeCompare(b.id, undefined, { numeric: true });
    });
    col.forEach((n, i) => { n.row = i; });
  }
  return { nodes, maxDepth: Math.max(0, ...[...nodes.values()].map((n) => n.depth)),
    maxRows: Math.max(1, ...[...columns.values()].map((c) => c.length)) };
}

const pos = (n) => ({ x: PAD + n.depth * COL_W, y: PAD + n.row * ROW_H });
const trunc = (s, n) => (s && s.length > n ? s.slice(0, n - 1) + "…" : s || "");

function nodeShape(n) {
  const { x, y } = pos(n);
  const g = svg("g", { class: `lnode ${n.type}`, transform: `translate(${x},${y})`, tabindex: "0", role: "button",
    "data-id": n.id, "aria-label": `${n.type} ${n.label}` });
  if (n.type === "version") {
    g.append(
      svg("rect", { width: NODE_W, height: NODE_H, rx: 10, class: "shape" }),
      svg("text", { x: 14, y: 24, class: "title" }, n.label),
      svg("text", { x: 14, y: 44, class: "sub mono" }, shortSha(n.sha_short)),
      svg("text", { x: NODE_W - 14, y: 24, "text-anchor": "end", class: "sub" },
        n.quality_score === null ? "q —" : `q ${n.quality_score.toFixed(1)}`),
      svg("text", { x: NODE_W - 14, y: 44, "text-anchor": "end", class: "sub" }, `${fmtNum(n.rows)} rows`),
    );
  } else {
    const r = NODE_H / 2; // pill-shaped experiment node
    g.append(
      svg("rect", { width: NODE_W, height: NODE_H, rx: r, class: "shape" }),
      svg("text", { x: 22, y: 27, class: "title" }, trunc(n.label, 17)),
      svg("text", { x: 22, y: 46, class: "sub" }, trunc(`${ALGO_SHORT[n.algorithm] || n.algorithm} · ${
        n.main_metric ? `${n.main_metric.name === "accuracy" ? "acc" : n.main_metric.name} ${n.main_metric.value.toFixed(3)}` : n.status}`, 24)),
    );
  }
  g.append(svg("circle", { cx: NODE_W - 6, cy: 6, r: 6, class: `chain-dot ${CHAIN_DOT[n.chain_status] || "none"}` },
    svg("title", {}, `chain: ${n.chain_status}`)));
  g.append(svg("title", {}, n.type === "version" ? `${n.label} · sha ${n.sha_short} · chain ${n.chain_status}` : `${n.label} · chain ${n.chain_status}`));
  return g;
}

function edgeShape(e, nodes) {
  const a = nodes.get(e.source), b = nodes.get(e.target);
  if (!a || !b) return null;
  const pa = pos(a), pb = pos(b);
  const x1 = pa.x + NODE_W, y1 = pa.y + NODE_H / 2, x2 = pb.x, y2 = pb.y + NODE_H / 2;
  const dx = Math.max(40, (x2 - x1) / 2);
  const d = `M ${x1} ${y1} C ${x1 + dx} ${y1}, ${x2 - dx} ${y2}, ${x2} ${y2}`;
  const label = e.type === "derived" ? e.label : "trained on";
  return svg("g", { class: `ledge ${e.type}` },
    svg("path", { d, "marker-end": "url(#arrow)" }),
    svg("text", { x: (x1 + x2) / 2, y: (y1 + y2) / 2 - 8, "text-anchor": "middle" }, trunc(label, LABEL_MAX), svg("title", {}, label)));
}

/** Drag-to-pan + zoom on an SVG whose content is inside `world`.
 *  Returns {wasDrag(), zoom(factor), reset()}; Ctrl/⌘ + wheel zooms (plain wheel still scrolls the page). */
function enablePanZoom(root, world) {
  let tx = 0, ty = 0, k = 1, start = null, moved = false;
  const apply = () => world.setAttribute("transform", `translate(${tx},${ty}) scale(${k})`);
  const unitsPerPx = () => root.viewBox.baseVal.width / root.getBoundingClientRect().width || 1;
  const zoomAt = (factor, cx, cy) => {
    const k2 = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, k * factor));
    tx = cx - (cx - tx) * (k2 / k);
    ty = cy - (cy - ty) * (k2 / k);
    k = k2;
    apply();
  };
  root.addEventListener("pointerdown", (e) => {
    if (e.button !== 0) return;
    start = { x: e.clientX, y: e.clientY, tx, ty };
    moved = false;
  });
  root.addEventListener("pointermove", (e) => {
    if (!start) return;
    const dx = e.clientX - start.x, dy = e.clientY - start.y;
    if (!moved && Math.hypot(dx, dy) < 4) return;
    if (!moved) { moved = true; root.setPointerCapture(e.pointerId); root.classList.add("panning"); }
    tx = start.tx + dx * unitsPerPx();
    ty = start.ty + dy * unitsPerPx();
    apply();
  });
  const end = () => { start = null; root.classList.remove("panning"); };
  root.addEventListener("pointerup", end);
  root.addEventListener("pointercancel", end);
  root.addEventListener("wheel", (e) => {
    if (!e.ctrlKey && !e.metaKey) return;
    e.preventDefault();
    const r = root.getBoundingClientRect();
    zoomAt(e.deltaY < 0 ? 1.12 : 1 / 1.12, (e.clientX - r.left) * unitsPerPx(), (e.clientY - r.top) * unitsPerPx());
  }, { passive: false });
  const vb = () => root.viewBox.baseVal;
  return {
    wasDrag: () => moved,
    zoom: (factor) => zoomAt(factor, vb().width / 2, vb().height / 2),
    reset: () => { tx = 0; ty = 0; k = 1; apply(); },
  };
}

async function sidePanel(panel, node, datasetId) {
  clear(panel);
  if (!node) {
    panel.append(h("p", { class: "muted" }, "Click a node to see its details and downstream impact."));
    return;
  }
  if (node.type === "experiment") {
    panel.append(
      h("div", { class: "row" }, h("h2", { style: "margin:0" }, node.label), statusBadge(node.status)),
      h("dl", { class: "kv" },
        h("dt", {}, "Algorithm"), h("dd", {}, node.algorithm),
        h("dt", {}, "Main metric"), h("dd", {}, node.main_metric ? `${node.main_metric.name} = ${node.main_metric.value}` : "—"),
        h("dt", {}, "Chain"), h("dd", {}, chainBadge(node.chain_status))),
      h("div", { class: "row" },
        h("a", { class: "btn primary", href: `#/experiments/${node.experiment_id}` }, "Open run"),
        h("a", { class: "btn", href: `#/verification/experiment/${node.experiment_id}` }, "Verify")),
      h("p", { class: "muted small", style: "margin-top:12px" }, "Experiments have no downstream artifacts."));
    return;
  }
  const impactBox = h("div", {}, loading("Computing impact…"));
  panel.append(
    h("div", { class: "row" }, h("h2", { style: "margin:0" }, node.label), scoreBadge(node.quality_score)),
    h("dl", { class: "kv" },
      h("dt", {}, "SHA-256"), h("dd", {}, h("code", {}, node.sha_short), " …"),
      h("dt", {}, "Rows"), h("dd", {}, fmtNum(node.rows)),
      h("dt", {}, "Chain"), h("dd", {}, chainBadge(node.chain_status))),
    h("div", { class: "row" },
      h("a", { class: "btn primary", href: `#/datasets/${datasetId}/v/${node.version_id}` }, "Open version"),
      h("a", { class: "btn", href: `#/experiments/new/${node.version_id}` }, "New run"),
      h("a", { class: "btn", href: `#/verification/dataset_version/${node.version_id}` }, "Verify")),
    h("h3", { style: "margin-top:16px" }, "Downstream impact"),
    impactBox);
  try {
    const imp = await api.get(`/versions/${node.version_id}/impact`);
    clear(impactBox);
    if (!imp.total) {
      impactBox.append(h("p", { class: "muted" }, "Nothing depends on this version yet."));
      return;
    }
    impactBox.append(
      h("p", { class: "small" }, `${imp.total} downstream artifact${imp.total > 1 ? "s" : ""} would be affected by a change here.`),
      h("ul", { class: "impact" },
        imp.versions.map((v) => h("li", {}, badge(`depth ${v.depth}`, "neutral"), " ",
          h("a", { href: `#/datasets/${v.dataset_id}/v/${v.id}` }, `v${v.version_no}`), h("span", { class: "muted small" }, ` · ${v.ops}`))),
        imp.experiments.map((e) => h("li", {}, badge(`depth ${e.depth}`, "neutral"), " ",
          h("a", { href: `#/experiments/${e.id}` }, e.name), " ", statusBadge(e.status),
          h("span", { class: "muted small" }, ` · ${e.algorithm}`)))));
  } catch (err) {
    clear(impactBox).append(h("div", { class: "error-box" }, err.message));
  }
}

export async function render(el, { id }, isStale) {
  el.append(loading());
  const datasets = await api.get("/datasets");
  if (isStale()) return;
  clear(el);
  const head = h("div", { class: "page-head" }, h("div", {}, h("h1", {}, "Lineage"),
    h("p", { class: "muted sub" }, "How versions were derived and which experiments were trained on them.")));
  if (!datasets.length) {
    el.append(head, h("div", { class: "card" }, empty("No datasets yet."), h("div", { class: "row", style: "justify-content:center" },
      h("a", { class: "btn primary", href: "#/datasets" }, "Upload a dataset"))));
    return;
  }
  const current = datasets.find((d) => d.id === id) || datasets[0];
  const pick = h("select", { "aria-label": "Dataset", onchange: () => { location.hash = `#/lineage/${pick.value}`; } },
    datasets.map((d) => h("option", { value: d.id }, d.name)));
  pick.value = String(current.id);
  head.append(h("label", { class: "field" }, "Dataset", pick));

  const graph = await api.get(`/datasets/${current.id}/lineage`);
  if (isStale()) return;
  const { nodes, maxDepth, maxRows } = layout(graph);
  const width = PAD * 2 + maxDepth * COL_W + NODE_W, height = PAD * 2 + (maxRows - 1) * ROW_H + NODE_H;
  const world = svg("g", {},
    graph.edges.map((e) => edgeShape(e, nodes)),
    [...nodes.values()].map(nodeShape));
  // Drawn at natural size (1 unit = 1px), shrunk only when wider than the card — small graphs aren't blown up.
  const root = svg("svg", { class: "lineage", viewBox: `0 0 ${width} ${height}`, style: `width:${width}px`,
    role: "group", "aria-label": `Lineage graph for ${current.name}` },
    svg("defs", {}, svg("marker", { id: "arrow", viewBox: "0 0 10 10", refX: 9, refY: 5, markerWidth: 7, markerHeight: 7, orient: "auto-start-reverse" },
      svg("path", { d: "M 0 0 L 10 5 L 0 10 z", class: "arrowhead" }))),
    world);
  const view = enablePanZoom(root, world);
  const wasDrag = view.wasDrag;
  const tool = (label, text, onclick) => h("button", { class: "btn sm icon-btn", type: "button", title: label, "aria-label": label, onclick }, text);
  const panel = h("div", { class: "card side" });
  const select = (nodeId) => {
    for (const g of world.querySelectorAll(".lnode")) g.classList.toggle("selected", g.dataset.id === nodeId);
    sidePanel(panel, nodes.get(nodeId), current.id).catch((err) => toast(err.message, "error"));
  };
  root.addEventListener("click", (e) => {
    if (wasDrag()) return;
    const g = e.target.closest(".lnode");
    if (g) select(g.dataset.id);
  });
  root.addEventListener("keydown", (e) => {
    const g = e.target.closest?.(".lnode");
    if (g && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); select(g.dataset.id); }
  });

  const versionsCount = graph.nodes.filter((n) => n.type === "version").length;
  el.append(head, h("div", { class: "lineage-layout" },
    h("div", { class: "card" },
      h("div", { class: "card-head" },
        h("div", {}, h("h2", { style: "margin:0" }, current.name),
          h("span", { class: "small muted" },
            `${versionsCount} version${versionsCount === 1 ? "" : "s"} · ${graph.nodes.length - versionsCount} experiment(s) · drag to pan · Ctrl + scroll to zoom`)),
        h("div", { class: "row graph-tools" },
          tool("Zoom out", "−", () => view.zoom(1 / 1.2)),
          tool("Zoom in", "+", () => view.zoom(1.2)),
          h("button", { class: "btn sm", type: "button", onclick: () => view.reset() }, "Fit"))),
      h("div", { class: "lineage-wrap" }, root),
      h("div", { class: "legend row small muted" },
        h("span", { class: "lg version" }), "version", h("span", { class: "lg experiment" }), "experiment",
        h("span", { class: "dot ok" }), "registered", h("span", { class: "dot warn" }), "pending",
        h("span", { class: "dot" }), "not registered")),
    panel));
  const latest = [...nodes.values()].filter((n) => n.type === "version").sort((a, b) => b.version_id - a.version_id)[0];
  select(latest?.id);
}
