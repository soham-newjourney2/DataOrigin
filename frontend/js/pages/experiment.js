// Run detail: metrics, config, env, reproducibility score, Reproduce, Register on chain, report links.
import { api } from "../api.js";
import { badge, chainBadge, clear, fmtDate, fmtNum, h, loading, statusBadge } from "../components/dom.js";
import { table } from "../components/table.js";
import { toast } from "../components/toast.js";

const kvTable = (pairs) => table([
  { label: "Field", render: ([k]) => k },
  { label: "Value", class: "wrap", render: ([, v]) => (v instanceof Node ? v : v === null || v === undefined || v === "" ? "—" : String(v)) },
], pairs);

function reproduceResult(r) {
  return h("div", { class: `result-box ${r.match ? "ok" : "bad"}` },
    h("div", { class: "row" },
      badge(r.match ? "✓ Metrics match" : "✗ Metrics differ", r.match ? "ok" : "bad"),
      badge(r.dataset_hash_match ? "dataset hash matches" : "dataset file changed!", r.dataset_hash_match ? "ok" : "bad"),
      h("span", { class: "spacer" }),
      h("a", { href: `#/experiments/${r.new_experiment_id}` }, `Open run #${r.new_experiment_id} →`)),
    table([
      { label: "Metric", render: ([k]) => k },
      { label: "Original", class: "num", render: ([, d]) => fmtNum(d.original, 6) },
      { label: "Reproduced", class: "num", render: ([, d]) => fmtNum(d.new, 6) },
      { label: "|Δ|", class: "num", render: ([, d]) => (d.diff === null ? "—" : d.diff.toExponential(1)) },
    ], Object.entries(r.metric_diffs)));
}

function scoreCard(report) {
  const rep = report.reproducibility;
  const cls = rep.score >= 80 ? "ok" : rep.score >= 50 ? "warn" : "bad";
  return h("div", { class: "card" },
    h("div", { class: "card-head" }, h("h2", {}, "Reproducibility"),
      h("span", { class: `big-score ${cls}` }, String(rep.score), h("small", {}, " / 100"))),
    h("ul", { class: "checks" }, rep.checks.map((c) => h("li", { class: c.passed ? "pass" : "fail" },
      h("span", { class: "mark", "aria-hidden": "true" }, c.passed ? "✓" : "✗"), c.label,
      h("span", { class: "muted small" }, ` ${c.points}/${c.max_points}`)))),
    rep.warnings.length ? h("div", { class: "warnings" }, h("strong", {}, "Missing metadata"),
      h("ul", {}, rep.warnings.map((w) => h("li", {}, w)))) : null);
}

export async function render(el, { id }, isStale) {
  el.append(loading());
  const exp = await api.get(`/experiments/${id}`);
  const [version, report] = await Promise.all([
    api.get(`/versions/${exp.version_id}`),
    api.get(`/reports/experiment/${id}`, { quiet: true }).catch(() => null),
  ]);
  if (isStale()) return;
  const dataset = report?.dataset;
  const chainStatus = report?.verification?.experiment?.chain_status ?? "unregistered";
  const chainSlot = h("span", {}, chainBadge(chainStatus));
  const resultSlot = h("div");
  const completed = exp.status === "completed";

  const reproduceBtn = h("button", { class: "btn primary", disabled: !completed, title: completed ? null : "Only completed runs can be reproduced" }, "Reproduce");
  reproduceBtn.addEventListener("click", async () => {
    reproduceBtn.disabled = true;
    reproduceBtn.textContent = "Re-running…";
    try {
      const r = await api.post(`/experiments/${id}/reproduce`);
      clear(resultSlot).append(reproduceResult(r));
      toast(r.match ? "Reproduced: metrics match exactly." : "Reproduced, but metrics differ.", r.match ? "ok" : "warn");
    } finally {
      reproduceBtn.disabled = false;
      reproduceBtn.textContent = "Reproduce";
    }
  });

  const registerBtn = h("button", { class: "btn", disabled: !completed }, "Register on chain");
  registerBtn.addEventListener("click", async () => {
    registerBtn.disabled = true;
    try {
      const rec = await api.post("/chain/register", { artifact_type: "experiment", artifact_id: id });
      clear(chainSlot).append(chainBadge(rec.status));
      const msg = { registered: `Registered on chain${rec.tx_hash ? ` (tx ${rec.tx_hash.slice(0, 12)}…)` : ""}.`,
        pending: `Saved as pending: ${rec.error || "chain unavailable"}.`, failed: `Registration failed: ${rec.error}` }[rec.status];
      toast(msg, rec.status === "registered" ? "ok" : rec.status === "pending" ? "warn" : "error");
    } finally {
      registerBtn.disabled = false;
    }
  });

  const reportUrl = `/api/reports/experiment/${id}`;
  const cfg = [
    ["Dataset", dataset ? h("a", { href: `#/datasets/${dataset.id}/v/${version.id}` }, `${dataset.name} · v${version.version_no}`) : `version ${version.id}`],
    ["Dataset SHA-256", h("code", {}, version.sha256)],
    ["Task", exp.task], ["Algorithm", exp.algorithm], ["Target column", exp.target_column],
    ["Hyperparameters", h("code", {}, JSON.stringify(exp.hyperparams_json))],
    ["Seed", exp.seed], ["Test size", exp.test_size],
    ["Model SHA-256", exp.model_sha256 ? h("code", {}, exp.model_sha256) : null],
    ["Commitment SHA-256", exp.commitment_sha256 ? h("code", {}, exp.commitment_sha256) : null],
    ["Duration", exp.duration_ms === null ? null : `${exp.duration_ms} ms`],
    ["Reproduced from", exp.reproduced_from_id ? h("a", { href: `#/experiments/${exp.reproduced_from_id}` }, `#${exp.reproduced_from_id}`) : null],
    ["Created", fmtDate(exp.created_at)],
  ];

  clear(el).append(
    h("div", { class: "crumbs" }, h("a", { href: "#/experiments" }, "Experiments"), ` / #${exp.id}`),
    h("div", { class: "page-head" },
      h("div", {}, h("div", { class: "row" }, h("h1", {}, exp.name), statusBadge(exp.status), chainSlot),
        h("p", { class: "muted sub" }, `${exp.algorithm} · target ${exp.target_column} · seed ${exp.seed}`)),
      h("div", { class: "row" },
        reproduceBtn, registerBtn,
        h("a", { class: "btn", href: `#/verification/experiment/${id}` }, "Verify"),
        h("a", { class: "btn", href: `${reportUrl}?format=html`, target: "_blank", rel: "noopener" }, "Open report ↗"),
        h("a", { class: "btn ghost", href: reportUrl, download: `experiment_${id}_report.json` }, "Download JSON"))),
    h("div", { class: "stack" },
      exp.error ? h("div", { class: "error-box" }, exp.error) : null,
      resultSlot,
      exp.metrics_json ? h("div", { class: "card" }, h("h2", {}, "Metrics"),
        h("div", { class: "stats" }, Object.entries(exp.metrics_json).map(([k, v]) =>
          h("div", { class: "stat" }, h("div", { class: "label" }, k), h("div", { class: "value" }, fmtNum(v, 4)))))) : null,
      report ? scoreCard(report) : null,
      h("div", { class: "grid-2" },
        h("div", { class: "card" }, h("h2", {}, "Configuration"), kvTable(cfg)),
        h("div", { class: "card" }, h("h2", {}, "Environment"), kvTable(Object.entries(exp.env_json || {}))))));
}
