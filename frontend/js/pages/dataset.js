// Dataset detail: versions table + version panel (Preview, Quality, Transform, Drift).
import { api } from "../api.js";
import { barChart, gauge } from "../components/charts.js";
import { badge, clear, empty, fmtDate, fmtNum, h, loading, parseValue, scoreBadge, shortSha } from "../components/dom.js";
import { table } from "../components/table.js";
import { tabs } from "../components/tabs.js";
import { toast } from "../components/toast.js";
import { dropzone } from "./datasets.js";

const TABS = ["preview", "quality", "transform", "drift"];
const SEVERITY = { critical: "bad", warning: "warn", info: "info" };
const DRIFT = { none: "ok", moderate: "warn", major: "bad", unknown: "neutral" };

let metaCache = null;
const pendingOps = new Map(); // version id → [{op, params}] (survives tab switches and re-renders)

async function meta() {
  metaCache ??= await api.get("/meta/algorithms");
  return metaCache;
}

const versionHash = (dsId, vid, tab) => `#/datasets/${dsId}/v/${vid}${tab ? `/${tab}` : ""}`;

function copyButton(text) {
  return h("button", {
    class: "btn ghost sm icon-btn", title: "Copy full SHA-256", "aria-label": "Copy SHA-256",
    onclick: async () => {
      try { await navigator.clipboard.writeText(text); toast("SHA-256 copied.", "ok"); }
      catch { toast(text, "info"); }
    },
  }, "⧉");
}

function verifyCell(version) {
  const holder = h("span");
  const btn = h("button", { class: "btn sm" }, "Verify");
  btn.addEventListener("click", async () => {
    btn.disabled = true;
    try {
      const r = await api.post(`/versions/${version.id}/verify-local`);
      clear(holder).append(r.match ? badge("✓ match", "ok", "Stored file matches its SHA-256")
        : badge("✗ mismatch", "bad", `Current: ${r.current ?? "file missing"}`));
      toast(r.match ? `v${version.version_no}: file matches its recorded hash.`
        : `v${version.version_no}: file does NOT match its recorded hash!`, r.match ? "ok" : "error");
    } catch { btn.disabled = false; }
  });
  holder.append(btn);
  return holder;
}

function newVersionForm(dataset, onCancel) {
  let file = null;
  const note = h("input", { type: "text", placeholder: "What changed? (optional)" });
  const errors = h("div", { class: "error-box", hidden: true });
  const submit = h("button", { class: "btn primary", type: "submit" }, "Upload version");
  const form = h("form", { class: "card" },
    h("div", { class: "card-head" }, h("h2", {}, "Upload a new raw version"),
      h("button", { class: "btn ghost sm", type: "button", onclick: onCancel }, "Cancel")),
    dropzone((f) => { file = f; }, "Becomes the next version; its parent is the current latest version."),
    h("div", { class: "form-grid", style: "margin-top:14px" }, h("label", { class: "field" }, "Note", note)),
    errors, h("div", { class: "form-actions" }, submit));
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    errors.hidden = true;
    if (!file) { errors.textContent = "Choose a file to upload."; errors.hidden = false; return; }
    const fd = new FormData();
    fd.append("file", file);
    if (note.value.trim()) fd.append("note", note.value.trim());
    submit.disabled = true;
    try {
      const v = await api.upload(`/datasets/${dataset.id}/versions`, fd, { quiet: true });
      toast(`Uploaded v${v.version_no}.`, "ok");
      location.hash = versionHash(dataset.id, v.id, "quality");
    } catch (err) {
      errors.textContent = err.message;
      errors.hidden = false;
      submit.disabled = false;
    }
  });
  return form;
}

// --- Preview -----------------------------------------------------------------------------------------------

async function previewTab(panel, version) {
  panel.append(loading());
  const p = await api.get(`/versions/${version.id}/preview?rows=50`);
  const cols = p.columns.map((name, i) => ({
    label: name,
    render: (row) => (row[i] === null ? h("span", { class: "muted" }, "null") : String(row[i])),
  }));
  clear(panel).append(
    h("p", { class: "muted small" }, `First ${p.rows.length} of ${version.row_count.toLocaleString()} rows.`),
    table(cols, p.rows, { emptyText: "This version has no rows." }));
}

// --- Quality -----------------------------------------------------------------------------------------------

/** Map a finding to the transform op that fixes it (null when there is no one-click fix). */
export function suggestedOp(finding, report) {
  const col = report.columns.find((c) => c.name === finding.column);
  switch (finding.check) {
    case "duplicates": return { op: "drop_duplicates", params: {} };
    case "missing":
      return col && col.missing_pct >= 50
        ? { op: "drop_columns", params: { columns: [finding.column] } }
        : { op: "fill_missing", params: { column: finding.column, strategy: col?.is_numeric ? "median" : "mode" } };
    case "outliers": return { op: "remove_outliers_iqr", params: { columns: [finding.column] } };
    case "constant": return { op: "drop_columns", params: { columns: [finding.column] } };
    default: return null;
  }
}

// Order for applying several suggestions: dedupe first, then drop columns, fill, and finally trim outliers.
const OP_ORDER = ["drop_duplicates", "drop_columns", "fill_missing", "remove_outliers_iqr"];

/** Suggested ops for a report, de-duplicated and in a sensible application order. */
export function suggestedOps(report) {
  const seen = new Set();
  return report.findings.map((f) => suggestedOp(f, report)).filter((op) => {
    if (!op) return false;
    const key = JSON.stringify(op);
    return !seen.has(key) && seen.add(key);
  }).sort((a, b) => OP_ORDER.indexOf(a.op) - OP_ORDER.indexOf(b.op));
}

function opLabel(o) {
  const p = o.params || {};
  const target = p.column || (p.columns || []).join(", ");
  return target ? `${o.op}(${target}${p.strategy ? ` · ${p.strategy}` : ""})` : o.op;
}

function addOp(version, op) {
  const ops = pendingOps.get(version.id) || [];
  const key = JSON.stringify(op);
  if (ops.some((o) => JSON.stringify(o) === key)) { toast(`${opLabel(op)} is already in the transform.`, "info"); return false; }
  ops.push(structuredClone(op));
  pendingOps.set(version.id, ops);
  return true;
}

async function qualityTab(panel, version, goTo) {
  panel.append(loading());
  let q;
  try {
    q = await api.get(`/versions/${version.id}/quality`, { quiet: true });
  } catch (err) {
    if (err.status !== 404) throw err;
    clear(panel).append(empty("No quality report for this version yet."),
      h("div", { class: "row", style: "justify-content:center" }, rerunButton(version, goTo)));
    return;
  }
  const r = q.report_json, s = r.summary, a = r.anomalies;
  const fixable = suggestedOps(r);

  const stat = (label, value, hint) => h("div", { class: "stat", title: hint }, h("div", { class: "label" }, label), h("div", { class: "value" }, value));
  const top = h("div", { class: "quality-top" },
    gauge(q.score),
    h("div", {},
      h("div", { class: "stats" },
        stat("Rows × cols", `${s.rows.toLocaleString()} × ${s.cols}`),
        stat("Missing cells", `${s.missing_total} (${fmtNum(s.missing_pct)}%)`),
        stat("Duplicate rows", `${s.duplicate_rows} (${fmtNum(s.duplicate_pct)}%)`),
        stat("Outlier rows", `${s.outlier_rows} (${fmtNum(s.outlier_row_pct)}%)`, "Rows with at least one IQR outlier"),
        stat("Anomalies", a.ran ? a.count : "—", a.ran ? "IsolationForest" : a.reason)),
      h("div", { class: "row", style: "margin-top:12px" },
        h("span", { class: "muted small" }, `Analyzed ${fmtDate(q.created_at)}`), h("span", { class: "spacer" }),
        fixable.length ? h("button", {
          class: "btn", onclick: () => {
            const added = fixable.filter((op) => addOp(version, op)).length;
            if (added) toast(`Added ${added} suggested op${added > 1 ? "s" : ""} to the transform.`, "ok");
            goTo("transform");
          },
        }, `Apply all ${fixable.length} suggestions`) : null,
        rerunButton(version, goTo))));

  const findings = r.findings.length ? h("ul", { class: "findings" }, r.findings.map((f) => {
    const op = suggestedOp(f, r);
    return h("li", { class: `finding ${f.severity}` },
      badge(f.severity, SEVERITY[f.severity] || "neutral"),
      h("div", {}, h("div", { class: "msg" }, f.message), h("div", { class: "sug" }, f.suggestion)),
      op ? h("button", {
        class: "btn sm", title: `Add ${opLabel(op)} to the transform`,
        onclick: () => { if (addOp(version, op)) toast(`Added ${opLabel(op)}.`, "ok"); goTo("transform"); },
      }, "Apply suggestion") : h("span"));
  })) : empty("No findings — this version looks clean.");

  const missing = r.columns.map((c) => ({ label: c.name, value: c.missing_pct }));
  const outlierCols = r.columns.filter((c) => c.outliers && c.outliers.count > 0);

  clear(panel).append(h("div", { class: "stack" },
    top,
    h("div", {}, h("h3", {}, `Findings (${r.findings.length})`), findings),
    h("div", { class: "grid-2" },
      h("div", {}, h("h3", {}, "Missing values per column"),
        barChart(missing, { format: (v) => `${fmtNum(v)}%`, maxValue: Math.max(5, ...missing.map((m) => m.value)), title: "Missing % per column" })),
      h("div", {},
        h("h3", {}, "IQR outliers"),
        table([
          { label: "Column", render: (c) => c.name },
          { label: "Count", class: "num", render: (c) => c.outliers.count },
          { label: "Bounds", render: (c) => `${fmtNum(c.outliers.lower)} … ${fmtNum(c.outliers.upper)}` },
          { label: "Sample rows", render: (c) => h("code", {}, c.outliers.sample_indices.join(", ")) },
        ], outlierCols, { emptyText: "No IQR outliers in numeric columns." }),
        h("h3", { style: "margin-top:16px" }, "ML anomalies (IsolationForest)"),
        a.ran
          ? h("p", {}, `${a.count} row(s) flagged. Most anomalous rows: `, h("code", {}, a.top_indices.join(", ")),
            h("span", { class: "muted small" }, ` (on ${a.columns.length} numeric columns)`))
          : h("p", { class: "muted" }, `Not run: ${a.reason}.`)))));
}

function rerunButton(version, goTo) {
  const btn = h("button", { class: "btn ghost" }, "Re-run analysis");
  btn.addEventListener("click", async () => {
    btn.disabled = true;
    try { await api.post(`/versions/${version.id}/quality`); toast("Quality analysis re-run.", "ok"); goTo("quality", true); }
    catch { btn.disabled = false; }
  });
  return btn;
}

// --- Transform ---------------------------------------------------------------------------------------------

function paramEditor(key, hint, op, columns, rerender) {
  const p = op.params;
  const optional = hint.endsWith("?");
  const type = hint.replace(/\?$/, "");
  const label = `${key}${optional ? " (optional)" : ""}`;

  if (key === "column" && type === "str") {
    const sel = h("select", { onchange: () => { p.column = sel.value; } },
      h("option", { value: "" }, "Choose column…"), columns.map((c) => h("option", { value: c.name }, c.name)));
    sel.value = p.column || "";
    return h("label", { class: "field" }, label, sel);
  }
  if (type === "list[str]") {
    const chosen = new Set(p.columns || []);
    const chips = columns.map((c) => {
      const box = h("input", { type: "checkbox", checked: chosen.has(c.name) });
      const chip = h("label", { class: `chip${chosen.has(c.name) ? " on" : ""}` }, box, c.name);
      box.addEventListener("change", () => {
        box.checked ? chosen.add(c.name) : chosen.delete(c.name);
        chip.classList.toggle("on", box.checked);
        if (chosen.size) p.columns = columns.map((x) => x.name).filter((n) => chosen.has(n));
        else delete p.columns;
      });
      return chip;
    });
    return h("div", { class: "field", style: "flex-basis:100%" }, h("span", {}, label), h("div", { class: "chips" }, chips));
  }
  if (type.includes("|")) {
    const choices = type.split("|");
    const sel = h("select", {
      onchange: () => { p[key] = sel.value; if (key === "strategy") rerender(); },
    }, h("option", { value: "" }, "Choose…"), choices.map((c) => h("option", { value: c }, c)));
    sel.value = p[key] || "";
    return h("label", { class: "field" }, label, sel);
  }
  if (key === "value" && op.op === "fill_missing" && p.strategy !== "value") return null;
  const input = h("input", { type: "text", placeholder: type === "any" || type === "any?" ? "number or text" : type,
    oninput: () => { if (input.value.trim() === "") delete p[key]; else p[key] = parseValue(input.value); } });
  input.value = p[key] ?? "";
  return h("label", { class: "field" }, label, input);
}

async function transformTab(panel, version, dataset, goTo) {
  panel.append(loading());
  const [m, quality] = await Promise.all([
    meta(),
    api.get(`/versions/${version.id}/quality`, { quiet: true }).catch(() => null),
  ]);
  const specs = Object.fromEntries(m.transform_ops.map((o) => [o.op, o]));
  const columns = version.schema_json;
  const ops = pendingOps.get(version.id) || [];
  pendingOps.set(version.id, ops);

  const list = h("div", { class: "ops" });
  const note = h("input", { type: "text", placeholder: "Optional note (defaults to the op summary)" });
  const errors = h("div", { class: "error-box", hidden: true });
  const applyBtn = h("button", { class: "btn primary" }, "Apply → new version");

  const drawOps = () => {
    clear(list);
    if (!ops.length) list.append(empty("No ops yet. Add one below, or use a suggestion from the quality findings."));
    ops.forEach((op, i) => {
      const spec = specs[op.op];
      const opSel = h("select", {
        "aria-label": "Operation",
        onchange: () => { ops[i] = { op: opSel.value, params: {} }; drawOps(); },
      }, m.transform_ops.map((o) => h("option", { value: o.op }, o.op)));
      opSel.value = op.op;
      const params = Object.entries(spec.params).map(([k, hint]) => paramEditor(k, hint, op, columns, drawOps));
      list.append(h("div", { class: "op" },
        h("span", { class: "n" }, i + 1),
        opSel,
        h("div", { class: "params" }, params.length ? params : h("span", { class: "muted small", style: "margin-top:6px" }, "No parameters.")),
        h("div", { class: "row" },
          h("button", { class: "btn ghost sm icon-btn", title: "Move up", "aria-label": "Move up", disabled: i === 0,
            onclick: () => { [ops[i - 1], ops[i]] = [ops[i], ops[i - 1]]; drawOps(); } }, "↑"),
          h("button", { class: "btn ghost sm icon-btn danger", title: "Remove", "aria-label": "Remove op",
            onclick: () => { ops.splice(i, 1); drawOps(); } }, "×")),
        h("div", { class: "desc" }, spec.description)));
    });
    applyBtn.disabled = !ops.length;
  };

  const addSel = h("select", { "aria-label": "Op to add" }, m.transform_ops.map((o) => h("option", { value: o.op }, o.op)));
  const addRow = h("div", { class: "row" }, addSel,
    h("button", { class: "btn", onclick: () => { ops.push({ op: addSel.value, params: {} }); drawOps(); } }, "+ Add op"));

  let suggestions = null;
  if (quality) {
    const r = quality.report_json;
    const sug = suggestedOps(r);
    if (sug.length) {
      suggestions = h("div", {}, h("h3", {}, "Suggestions from quality findings"),
        h("div", { class: "suggestions" }, sug.map((op) => h("button", {
          class: "btn sm", title: "Add to the op list",
          onclick: () => { if (addOp(version, op)) drawOps(); }, // addOp appends to this same `ops` array
        }, `+ ${opLabel(op)}`))));
    }
  }

  applyBtn.addEventListener("click", async () => {
    errors.hidden = true;
    applyBtn.disabled = true;
    applyBtn.textContent = "Applying…";
    try {
      const v = await api.post(`/versions/${version.id}/transform`,
        { ops, note: note.value.trim() || null }, { quiet: true });
      pendingOps.delete(version.id);
      toast(`Created v${v.version_no} (quality ${v.quality_score.toFixed(1)}).`, "ok");
      location.hash = versionHash(dataset.id, v.id, "quality");
    } catch (err) {
      errors.textContent = err.message;
      errors.hidden = false;
      applyBtn.disabled = false;
      applyBtn.textContent = "Apply → new version";
    }
  });

  drawOps();
  clear(panel).append(h("div", { class: "stack" },
    h("p", { class: "muted" }, `Ops run in order on v${version.version_no}. The result is saved as a new CSV version with v${version.version_no} as its parent.`),
    suggestions,
    h("div", {}, h("h3", {}, "Operations"), list, h("div", { style: "margin-top:10px" }, addRow)),
    h("div", { class: "form-grid" }, h("label", { class: "field" }, "Note", note)),
    errors,
    h("div", { class: "form-actions" }, applyBtn,
      h("button", { class: "btn ghost", onclick: () => { ops.length = 0; drawOps(); } }, "Clear"))));
}

// --- Drift -------------------------------------------------------------------------------------------------

async function driftTab(panel, version, versions) {
  const others = versions.filter((v) => v.id !== version.id);
  if (!others.length) {
    panel.append(empty("Only one version exists. Upload or derive another version to compare drift."));
    return;
  }
  const defaultRef = others.find((v) => v.id === version.parent_version_id) || others[0];
  const sel = h("select", { "aria-label": "Reference version" },
    others.map((v) => h("option", { value: v.id }, `v${v.version_no}${v.id === version.parent_version_id ? " (parent)" : ""}`)));
  sel.value = String(defaultRef.id);
  const out = h("div");

  const load = async () => {
    clear(out).append(loading());
    const d = await api.get(`/versions/${version.id}/drift?against=${sel.value}`);
    const ref = versions.find((v) => v.id === +sel.value);
    const sum = d.summary;
    const schema = d.schema;
    clear(out).append(h("div", { class: "stack" },
      h("div", { class: "row" },
        h("span", {}, "Overall: "), badge(sum.overall, DRIFT[sum.overall]),
        badge(`${sum.none} none`, "ok"), badge(`${sum.moderate} moderate`, "warn"), badge(`${sum.major} major`, "bad"),
        sum.unknown ? badge(`${sum.unknown} unknown`, "neutral") : null),
      schema.changed ? h("div", { class: "row" }, h("strong", {}, "Schema changes:"),
        schema.added.map((c) => badge(`+ ${c}`, "info", "Added column")),
        schema.removed.map((c) => badge(`− ${c}`, "bad", "Removed column"))) : null,
      table([
        { label: "Column", render: (c) => c.name },
        { label: "Kind", render: (c) => c.kind },
        { label: "PSI", class: "num", render: (c) => (c.psi === null ? "—" : c.psi.toFixed(4)) },
        { label: "Status", render: (c) => badge(c.status, DRIFT[c.status] || "neutral") },
        { label: `Detail (v${ref.version_no} → v${version.version_no})`, class: "wrap", render: (c) => (c.kind === "numeric"
          ? `mean ${fmtNum(c.mean_a)} → ${fmtNum(c.mean_b)} · std ${fmtNum(c.std_a)} → ${fmtNum(c.std_b)}`
          : [c.new_categories.length ? `new: ${c.new_categories.join(", ")}` : "",
            c.missing_categories.length ? `missing: ${c.missing_categories.join(", ")}` : ""].filter(Boolean).join(" · ") || "same categories") },
      ], d.columns, { emptyText: "No shared columns to compare." }),
      h("p", { class: "muted small" }, "PSI < 0.1 none · < 0.25 moderate · otherwise major. Bins come from the reference version.")));
  };
  sel.addEventListener("change", load);
  panel.append(h("div", { class: "row", style: "margin-bottom:14px" },
    h("span", {}, `Compare v${version.version_no} against`), sel));
  panel.append(out);
  await load();
}

// --- Page --------------------------------------------------------------------------------------------------

export async function render(el, { id, vid, tab }, isStale) {
  el.append(loading());
  const [dataset, graph] = await Promise.all([
    api.get(`/datasets/${id}`),
    api.get(`/datasets/${id}/lineage`, { quiet: true }).catch(() => ({ nodes: [] })),
  ]);
  if (isStale()) return;
  const versions = dataset.versions;
  const score = new Map(graph.nodes.filter((n) => n.type === "version").map((n) => [n.version_id, n.quality_score]));
  const byId = new Map(versions.map((v) => [v.id, v]));
  const version = byId.get(vid) || versions[versions.length - 1];
  if (vid && !byId.has(vid)) toast(`Version ${vid} is not part of this dataset; showing the latest.`, "warn");

  const formSlot = h("div");
  const uploadBtn = h("button", { class: "btn", onclick: () => {
    if (formSlot.childElementCount) { clear(formSlot); return; }
    formSlot.append(newVersionForm(dataset, () => clear(formSlot)));
  } }, "Upload new raw version");

  const versionsTable = table([
    { label: "Version", render: (v) => h("a", { href: versionHash(id, v.id) }, `v${v.version_no}`) },
    { label: "SHA-256", render: (v) => h("span", { class: "sha" }, h("code", { title: v.sha256 }, shortSha(v.sha256)), copyButton(v.sha256)) },
    { label: "Rows × cols", class: "num", render: (v) => `${v.row_count.toLocaleString()} × ${v.col_count}` },
    { label: "Parent", render: (v) => (v.parent_version_id ? `v${byId.get(v.parent_version_id)?.version_no ?? "?"}` : "—") },
    { label: "Note", class: "wrap", render: (v) => v.note || h("span", { class: "muted" }, "—") },
    { label: "Quality", render: (v) => scoreBadge(score.get(v.id)) },
    { label: "Created", render: (v) => fmtDate(v.created_at) },
    { label: "Integrity", render: (v) => verifyCell(v) },
  ], versions, {
    onRowClick: (v) => { location.hash = versionHash(id, v.id, tab); },
    rowClass: (v) => (v.id === version.id ? "selected" : ""),
  });

  // Tab switches update the URL without re-rendering the page.
  const goTo = (t, force = false) => {
    const target = versionHash(id, version.id, t);
    if (force) {
      if (location.hash === target) window.dispatchEvent(new HashChangeEvent("hashchange"));
      else location.hash = target;
      return;
    }
    history.replaceState(null, "", target);
    panelTabs.select(t);
  };
  const run = (fn) => (panel) => fn(panel).catch((err) => {
    clear(panel).append(h("div", { class: "error-box" }, err.message || String(err)));
  });
  const panelTabs = tabs([
    { id: "preview", label: "Preview", render: run((p) => previewTab(p, version)) },
    { id: "quality", label: "Quality", render: run((p) => qualityTab(p, version, goTo)) },
    { id: "transform", label: "Transform", render: run((p) => transformTab(p, version, dataset, goTo)) },
    { id: "drift", label: "Drift", render: run((p) => driftTab(p, version, versions)) },
  ], TABS.includes(tab) ? tab : "quality", (t) => history.replaceState(null, "", versionHash(id, version.id, t)));

  clear(el).append(
    h("div", { class: "crumbs" }, h("a", { href: "#/datasets" }, "Datasets"), " / ", dataset.name),
    h("div", { class: "page-head" },
      h("div", {},
        h("h1", {}, dataset.name),
        h("p", { class: "muted sub" }, [dataset.description, dataset.source ? `Source: ${dataset.source}` : null,
          `Created ${fmtDate(dataset.created_at)}`].filter(Boolean).join(" · "))),
      h("div", { class: "row" },
        h("a", { class: "btn", href: `#/lineage/${id}` }, "Lineage"),
        h("a", { class: "btn", href: `#/experiments/new/${version.id}` }, `New run on v${version.version_no}`),
        uploadBtn)),
    h("div", { class: "stack" },
      formSlot,
      h("div", { class: "card" }, h("div", { class: "card-head" }, h("h2", {}, `Versions (${versions.length})`)), versionsTable),
      h("div", { class: "card" },
        h("div", { class: "card-head" },
          h("h2", {}, `v${version.version_no}`),
          h("span", { class: "row muted small" }, h("code", { title: version.sha256 }, shortSha(version.sha256)),
            ` · ${version.row_count.toLocaleString()} rows × ${version.col_count} cols · ${version.file_format.toUpperCase()}`),
          h("span", { class: "spacer" }),
          h("a", { class: "btn sm", href: `/api/versions/${version.id}/download` }, "Download")),
        panelTabs)));
}
