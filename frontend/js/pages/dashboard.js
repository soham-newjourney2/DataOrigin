// Dashboard (home): integrity tiles, reproducibility ring, KPIs, pipeline and recent activity for the newest dataset.
import { api } from "../api.js";
import { svg } from "../components/charts.js";
import { clear, fmtNum, h, loading, shortSha } from "../components/dom.js";

const VERDICT = { VERIFIED: ["ok", "Verified"], LOCAL_ONLY: ["warn", "Local only"], TAMPERED: ["bad", "Tampered"],
  NOT_REGISTERED: ["neutral", "Not registered"] };

function ring(score) {
  const cls = score >= 80 ? "" : score >= 50 ? "warn" : "bad";
  return svg("svg", { width: 104, height: 104, viewBox: "0 0 104 104", role: "img", "aria-label": `Reproducibility ${score} of 100` },
    svg("circle", { class: "ring-track", cx: 52, cy: 52, r: 44 }),
    svg("circle", { class: `ring-arc ${cls}`, cx: 52, cy: 52, r: 44, pathLength: 100, transform: "rotate(-90 52 52)",
      style: `stroke-dasharray: ${score} 100` }),
    svg("text", { x: 52, y: 61, "text-anchor": "middle", "font-size": 26 }, String(score)));
}

const time = (iso) => new Date(iso).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });

function emptyState() {
  return h("div", { class: "card hero-empty" },
    h("h2", {}, "Welcome to DataOrigin"),
    h("p", { class: "muted" }, "Upload a dataset to start tracking its versions, quality, lineage and experiments."),
    h("p", { class: "muted small" }, "Or load the demo story from backend/: ", h("code", {}, ".venv/Scripts/python scripts/seed_demo.py")),
    h("div", { class: "row", style: "justify-content:center;margin-top:12px" },
      h("a", { class: "btn primary", href: "#/datasets" }, "Upload a dataset")));
}

export async function render(el, _params, isStale) {
  el.append(loading());
  const datasets = await api.get("/datasets");
  if (isStale()) return;
  if (!datasets.length) { clear(el).append(header(null), emptyState()); return; }

  const focus = datasets.reduce((a, b) => (b.id > a.id ? b : a));
  const [detail, graph, runs, events] = await Promise.all([
    api.get(`/datasets/${focus.id}`), api.get(`/datasets/${focus.id}/lineage`),
    api.get("/experiments"), api.get("/audit/events?limit=6"),
  ]);
  const versions = detail.versions;
  const vids = new Set(versions.map((v) => v.id));
  const myRuns = runs.filter((r) => vids.has(r.version_id));
  const latestRun = myRuns.find((r) => r.status === "completed");
  const latest = versions[versions.length - 1];
  const score = new Map(graph.nodes.filter((n) => n.type === "version").map((n) => [n.version_id, n.quality_score]));

  const recent = versions.slice(-3).reverse();
  const [verdicts, report, drift] = await Promise.all([
    Promise.all(recent.map((v) => api.get(`/chain/verify/dataset_version/${v.id}?quiet=true`, { quiet: true }).catch(() => null))),
    latestRun ? api.get(`/reports/experiment/${latestRun.id}`, { quiet: true }).catch(() => null) : null,
    latest.parent_version_id
      ? api.get(`/versions/${latest.id}/drift?against=${latest.parent_version_id}`, { quiet: true }).catch(() => null) : null,
  ]);
  if (isStale()) return;

  const first = versions[0];
  const q0 = score.get(first.id), q1 = score.get(latest.id);
  const metricName = latestRun?.task === "regression" ? "r2" : "accuracy";
  const metric = latestRun?.metrics_json?.[metricName];

  const tiles = recent.map((v, i) => {
    const res = verdicts[i];
    const [cls, word] = res ? VERDICT[res.verdict] || ["neutral", res.verdict] : ["neutral", "Unknown"];
    const hash = res && !res.local_match && res.current_sha
      ? `${shortSha(res.db_sha).slice(0, 8)} ≠ ${shortSha(res.current_sha).slice(0, 8)}` : `${shortSha(v.sha256)}`;
    return h("a", { class: `vtile ${cls}${cls === "ok" ? " sheen" : ""}`, href: `#/verification/dataset_version/${v.id}` },
      h("span", { class: "who" }, `v${v.version_no} · ${v.note || "version"}`),
      h("span", { class: "what" }, word),
      h("span", { class: "hash" }, hash));
  });

  const rep = report?.reproducibility;
  // Icon tiles render only in the dark theme (CSS); paths are 24×24 stroke icons.
  const icon = (cls, ...paths) => h("span", { class: `kpi-ico ${cls}`, "aria-hidden": "true" },
    svg("svg", { viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", "stroke-width": 1.8,
      "stroke-linecap": "round", "stroke-linejoin": "round" }, paths.map((d) => svg("path", { d }))));
  const kpi = (ico, label, value, hint) => h("div", { class: "card lift kpi span-4" }, ico,
    h("div", { class: "label" }, label), h("div", { class: "value" }, value), hint ? h("div", { class: "hint" }, hint) : null);

  const pipeline = h("div", { class: "pipeline" });
  versions.forEach((v, i) => {
    if (i) pipeline.append(h("span", { class: "flow-line", "aria-hidden": "true" }));
    pipeline.append(h("a", { class: "pill", href: `#/datasets/${focus.id}/v/${v.id}` }, `v${v.version_no} `,
      h("span", { class: "muted" }, `${fmtNum(v.row_count)} rows`)));
  });
  myRuns.filter((r) => r.status === "completed" && !r.reproduced_from_id).slice(0, 2).forEach((r) => {
    pipeline.append(h("span", { class: "flow-line", "aria-hidden": "true" }),
      h("a", { class: "pill exp", href: `#/experiments/${r.id}` }, `${r.name} `,
        h("span", { class: "muted" }, r.metrics_json?.[r.task === "regression" ? "r2" : "accuracy"]?.toFixed(3) ?? "")));
  });

  clear(el).append(
    header(focus, versions.length, myRuns.length, latest.id),
    h("div", { class: "dash-grid" },
      h("section", { class: "card span-8", "aria-label": "Integrity" },
        h("div", { class: "card-head" }, h("h2", {}, "Integrity"), h("a", { href: "#/verification" }, "Verification")),
        h("div", { class: "verdict-tiles" }, tiles)),
      h("section", { class: "card lift span-4", "aria-label": "Reproducibility" },
        rep ? h("div", { class: "score-ring" }, ring(rep.score),
          h("div", {}, h("h2", { style: "margin:0 0 4px" }, "Reproducibility"),
            h("div", { class: "muted small" }, `${rep.checks.filter((c) => c.passed).length} of ${rep.checks.length} checks pass`),
            h("a", { href: `#/experiments/${latestRun.id}` }, "Open run")))
          : h("div", {}, h("h2", {}, "Reproducibility"), h("p", { class: "muted" }, "No completed run yet."),
            h("a", { class: "btn sm", href: `#/experiments/new/${latest.id}` }, "Start a run"))),
      kpi(icon("teal", "M12 3l2 4.5 4.8.5-3.6 3.2 1 4.8L12 13.6 7.8 16l1-4.8L5.2 8l4.8-.5z", "M5 20h14"),
        "Quality", q0 !== undefined && q1 !== undefined && first.id !== latest.id ? `${fmtNum(q0, 1)} → ${fmtNum(q1, 1)}` : fmtNum(q1, 1),
        first.id !== latest.id ? `v${first.version_no} to v${latest.version_no}` : `v${latest.version_no}`),
      kpi(icon("violet", "M3 12h4l3-7 4 14 3-7h4"),
        latestRun ? `Model ${metricName}` : "Model", metric !== undefined ? metric.toFixed(3) : "—",
        latestRun ? `${latestRun.algorithm.replaceAll("_", " ")} · seed ${latestRun.seed}` : "no runs yet"),
      kpi(icon("amber", "M3 17l6-6 4 4 8-8", "M15 7h6v6"),
        `Drift on v${latest.version_no}`, drift ? `${drift.summary.major} major` : "—",
        drift ? (drift.schema.changed ? "schema changed vs parent" : `vs v${versions.find((v) => v.id === latest.parent_version_id)?.version_no}`)
          : "no parent to compare"),
      h("section", { class: "card span-12", "aria-label": "Lineage" },
        h("div", { class: "card-head" }, h("h2", {}, "Lineage"), h("a", { href: `#/lineage/${focus.id}` }, "Open graph")),
        pipeline),
      h("section", { class: "card span-12", "aria-label": "Recent activity" },
        h("div", { class: "card-head" }, h("h2", {}, "Recent activity"), h("a", { href: "#/audit" }, "Audit log")),
        h("div", { class: "activity" }, events.flatMap((e) => [
          h("span", { class: "time" }, time(e.created_at)),
          h("span", { style: `font-weight:700${e.event_type.startsWith("dev.") ? ";color:var(--bad)" : ""}` }, e.event_type),
          h("span", { class: "muted" }, `${e.entity_type} #${e.entity_id}`)])))));
}

function header(focus, nVersions, nRuns, latestVid) {
  return h("div", { class: "page-head" },
    h("div", {}, h("div", { class: "kicker" }, "Overview"), h("h1", {}, "Dashboard"),
      h("p", { class: "muted sub" }, focus ? `${focus.name} · ${nVersions} version${nVersions === 1 ? "" : "s"} · ${nRuns} run${nRuns === 1 ? "" : "s"}` : "Nothing tracked yet")),
    h("div", { class: "row" },
      focus ? h("a", { class: "btn", href: `#/experiments/new/${latestVid}` }, "New run") : null,
      h("a", { class: "btn primary", href: "#/datasets" }, "Upload dataset")));
}
