// Experiments: new-run form + runs list with multi-select compare.
import { api } from "../api.js";
import { clear, fmtDate, h, loadAllVersions, loading, mainMetric, statusBadge } from "../components/dom.js";
import { table } from "../components/table.js";
import { toast } from "../components/toast.js";

/** Input for one hyperparam by kind (int, float, int_or_none, bool). Returns {el, read() → value | throws}. */
function hyperparamInput(name, kind, value) {
  if (kind === "bool") {
    const box = h("input", { type: "checkbox", checked: !!value });
    return { el: h("label", { class: "chip" }, box, name), read: () => box.checked };
  }
  const input = h("input", { type: "number", step: kind === "float" ? "any" : "1",
    placeholder: kind === "int_or_none" ? "none" : "" });
  input.value = value ?? "";
  return {
    el: h("label", { class: "field" }, `${name}${kind === "int_or_none" ? " (blank = none)" : ""}`, input),
    read: () => {
      const t = input.value.trim();
      if (t === "") {
        if (kind === "int_or_none") return null;
        throw new Error(`${name} is required`);
      }
      const n = Number(t);
      if (!Number.isFinite(n) || (kind !== "float" && !Number.isInteger(n))) throw new Error(`${name} must be ${kind === "float" ? "a number" : "an integer"}`);
      return n;
    },
  };
}

function runForm(all, meta, preselectVid) {
  const versions = all.flatMap((d) => d.versions.map((v) => ({ ...v, dataset: d })));
  const vSel = h("select", { required: true }, all.map((d) => h("optgroup", { label: d.name },
    d.versions.map((v) => h("option", { value: v.id }, `${d.name} · v${v.version_no} (${v.row_count} rows)`)))));
  const pre = versions.find((v) => v.id === preselectVid) || versions[versions.length - 1];
  if (pre) vSel.value = String(pre.id);

  const taskSel = h("select", {}, h("option", { value: "classification" }, "classification"), h("option", { value: "regression" }, "regression"));
  const algoSel = h("select");
  const targetSel = h("select");
  const name = h("input", { type: "text", placeholder: "e.g. baseline rf", maxlength: "200" });
  const seed = h("input", { type: "number", step: "1", min: "0", value: "42" });
  const testSize = h("input", { type: "number", step: "0.05", min: "0.05", max: "0.95", value: "0.2" });
  const hpBox = h("div", { class: "form-grid" });
  const errors = h("div", { class: "error-box", hidden: true });
  const submit = h("button", { class: "btn primary", type: "submit" }, "Run experiment");
  let hpInputs = [];

  const fillAlgos = () => {
    clear(algoSel).append(...meta.algorithms.filter((a) => a.task === taskSel.value).map((a) => h("option", { value: a.name }, a.name)));
    fillHyperparams();
  };
  const fillHyperparams = () => {
    const algo = meta.algorithms.find((a) => a.name === algoSel.value);
    hpInputs = Object.entries(algo?.params || {}).map(([k, kind]) => ({ key: k, ...hyperparamInput(k, kind, algo.defaults[k]) }));
    clear(hpBox).append(...hpInputs.map((x) => x.el));
  };
  const fillTargets = () => {
    const v = versions.find((x) => x.id === +vSel.value);
    const prev = targetSel.value;
    clear(targetSel).append(...(v?.schema_json || []).map((c) => h("option", { value: c.name }, `${c.name} (${c.dtype})`)));
    const cols = (v?.schema_json || []).map((c) => c.name);
    targetSel.value = cols.includes(prev) ? prev : cols[cols.length - 1] || "";
  };
  taskSel.addEventListener("change", fillAlgos);
  algoSel.addEventListener("change", fillHyperparams);
  vSel.addEventListener("change", fillTargets);
  fillAlgos();
  fillTargets();

  const form = h("form", { class: "card" },
    h("div", { class: "card-head" }, h("h2", {}, "New run")),
    h("div", { class: "form-grid" },
      h("label", { class: "field" }, "Dataset version", vSel),
      h("label", { class: "field" }, "Name", name),
      h("label", { class: "field" }, "Task", taskSel),
      h("label", { class: "field" }, "Algorithm", algoSel),
      h("label", { class: "field" }, "Target column", targetSel),
      h("label", { class: "field" }, "Seed", seed),
      h("label", { class: "field" }, "Test size", testSize)),
    h("h3", { style: "margin-top:16px" }, "Hyperparameters"), hpBox,
    errors,
    h("div", { class: "form-actions" }, submit, h("span", { class: "muted small" }, "Runs synchronously; small datasets take a second or two.")));

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    errors.hidden = true;
    let body;
    try {
      if (!vSel.value) throw new Error("Pick a dataset version.");
      const hyperparams = Object.fromEntries(hpInputs.map((x) => [x.key, x.read()]));
      const v = versions.find((x) => x.id === +vSel.value);
      body = { version_id: +vSel.value, name: name.value.trim() || `${algoSel.value} on ${v.dataset.name} v${v.version_no}`,
        task: taskSel.value, algorithm: algoSel.value, target_column: targetSel.value, hyperparams,
        seed: Number(seed.value), test_size: Number(testSize.value) };
    } catch (err) {
      errors.textContent = err.message;
      errors.hidden = false;
      return;
    }
    submit.disabled = true;
    submit.textContent = "Training…";
    try {
      const exp = await api.post("/experiments", body, { quiet: true });
      toast(`Run “${exp.name}” completed.`, "ok");
      location.hash = `#/experiments/${exp.id}`;
    } catch (err) {
      errors.textContent = err.message;
      errors.hidden = false;
      if (err.status === 400 && err.body?.experiment) {
        errors.append(h("div", {}, h("a", { href: `#/experiments/${err.body.experiment.id}` }, "Open the failed run")));
      }
    } finally {
      submit.disabled = false;
      submit.textContent = "Run experiment";
    }
  });
  return form;
}

export async function render(el, { vid }, isStale) {
  el.append(loading());
  const [all, meta, runs] = await Promise.all([loadAllVersions(api), api.get("/meta/algorithms"), api.get("/experiments")]);
  if (isStale()) return;
  const versionLabel = new Map(all.flatMap((d) => d.versions.map((v) => [v.id, { text: `${d.name} · v${v.version_no}`, d, v }])));

  const selected = new Set();
  const compareBtn = h("button", { class: "btn", disabled: true,
    onclick: () => { location.hash = `#/experiments/compare/${[...selected].join(",")}`; } }, "Compare selected");
  const updateCompare = () => {
    compareBtn.disabled = selected.size < 2;
    compareBtn.textContent = selected.size >= 2 ? `Compare ${selected.size} runs` : "Compare selected";
  };
  const check = (run) => {
    const box = h("input", { type: "checkbox", "aria-label": `Select run ${run.id}` });
    box.addEventListener("change", () => { box.checked ? selected.add(run.id) : selected.delete(run.id); updateCompare(); });
    return box;
  };

  const list = h("div", { class: "card" },
    h("div", { class: "card-head" }, h("h2", {}, `Runs (${runs.length})`), compareBtn),
    table([
      { label: "", render: check },
      { label: "#", class: "num", render: (r) => r.id },
      { label: "Name", render: (r) => h("a", { href: `#/experiments/${r.id}` }, r.name) },
      { label: "Data", render: (r) => {
        const l = versionLabel.get(r.version_id);
        return l ? h("a", { href: `#/datasets/${l.d.id}/v/${r.version_id}` }, l.text) : `version ${r.version_id}`;
      } },
      { label: "Algorithm", render: (r) => r.algorithm },
      { label: "Target", render: (r) => r.target_column },
      { label: "Seed", class: "num", render: (r) => r.seed },
      { label: "Result", render: (r) => mainMetric(r) },
      { label: "Status", render: (r) => statusBadge(r.status) },
      { label: "Repro of", render: (r) => (r.reproduced_from_id ? h("a", { href: `#/experiments/${r.reproduced_from_id}` }, `#${r.reproduced_from_id}`) : "—") },
      { label: "Created", render: (r) => fmtDate(r.created_at) },
    ], runs, { onRowClick: (r) => { location.hash = `#/experiments/${r.id}`; }, emptyText: "No runs yet. Start one above." }));

  clear(el).append(
    h("div", { class: "page-head" }, h("div", {}, h("h1", {}, "Experiments"),
      h("p", { class: "muted sub" }, "Predefined scikit-learn runs with recorded data hash, config, seed, metrics and environment."))),
    h("div", { class: "stack" },
      all.some((d) => d.versions.length) ? runForm(all, meta, vid)
        : h("div", { class: "card empty" }, "Upload a dataset first. ", h("a", { href: "#/datasets" }, "Go to datasets")),
      list));
}
