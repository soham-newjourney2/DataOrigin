// Datasets list + upload form (drag-drop, name, description, source).
import { api } from "../api.js";
import { badge, clear, fmtDate, h, loading, scoreBadge, shortSha } from "../components/dom.js";
import { table } from "../components/table.js";
import { toast } from "../components/toast.js";

const ACCEPT = ".csv,.xlsx,.json";

/** Drop zone + hidden file input. onFile(file) is called on pick/drop. */
export function dropzone(onFile, hint = "CSV, XLSX or JSON · up to 20 MB") {
  const input = h("input", { type: "file", accept: ACCEPT, hidden: true });
  const name = h("span", { class: "file-name" });
  const zone = h("div", { class: "dropzone", tabindex: "0", role: "button", "aria-label": "Choose a file to upload" },
    h("strong", {}, "Drop a file here or click to browse"), h("span", { class: "muted small" }, hint), name, input);
  const pick = (file) => {
    if (!file) return;
    name.textContent = file.name;
    onFile(file);
  };
  zone.addEventListener("click", (e) => { if (e.target !== input) input.click(); });
  zone.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); input.click(); } });
  input.addEventListener("change", () => pick(input.files[0]));
  zone.addEventListener("dragover", (e) => { e.preventDefault(); zone.classList.add("over"); });
  zone.addEventListener("dragleave", () => zone.classList.remove("over"));
  zone.addEventListener("drop", (e) => {
    e.preventDefault();
    zone.classList.remove("over");
    pick(e.dataTransfer.files[0]);
  });
  zone.reset = () => { name.textContent = ""; input.value = ""; };
  return zone;
}

function uploadForm() {
  let file = null;
  const nameInput = h("input", { type: "text", name: "name", required: true, placeholder: "e.g. customers", maxlength: "200" });
  const desc = h("input", { type: "text", name: "description", placeholder: "Optional" });
  const source = h("input", { type: "text", name: "source", placeholder: "Optional, e.g. CRM export" });
  const errors = h("div", { class: "error-box", hidden: true });
  const submit = h("button", { class: "btn primary", type: "submit" }, "Upload dataset");
  const zone = dropzone((f) => {
    file = f;
    if (!nameInput.value.trim()) nameInput.value = f.name.replace(/\.[^.]+$/, "");
  });

  const form = h("form", { class: "card" },
    h("div", { class: "card-head" }, h("h2", {}, "Upload a dataset")),
    zone,
    h("div", { class: "form-grid", style: "margin-top:14px" },
      h("label", { class: "field" }, "Name", nameInput),
      h("label", { class: "field" }, "Description", desc),
      h("label", { class: "field" }, "Source", source)),
    errors,
    h("div", { class: "form-actions" }, submit, h("span", { class: "muted small" }, "A SHA-256 hash and quality report are created on upload.")));

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    errors.hidden = true;
    const problems = [];
    if (!file) problems.push("Choose a file to upload.");
    if (!nameInput.value.trim()) problems.push("Enter a dataset name.");
    if (problems.length) { errors.textContent = problems.join("\n"); errors.hidden = false; return; }

    const fd = new FormData();
    fd.append("file", file);
    fd.append("name", nameInput.value.trim());
    if (desc.value.trim()) fd.append("description", desc.value.trim());
    if (source.value.trim()) fd.append("source", source.value.trim());
    submit.disabled = true;
    submit.textContent = "Uploading…";
    try {
      const ds = await api.upload("/datasets", fd, { quiet: true });
      toast(`Uploaded “${ds.name}” as v1.`, "ok");
      location.hash = `#/datasets/${ds.id}`;
    } catch (err) {
      errors.textContent = err.message;
      errors.hidden = false;
    } finally {
      submit.disabled = false;
      submit.textContent = "Upload dataset";
    }
  });
  return form;
}

export async function render(el) {
  const list = h("div", { class: "card" }, h("div", { class: "card-head" }, h("h2", {}, "All datasets")), loading());
  el.append(
    h("div", { class: "page-head" }, h("div", {},
      h("h1", {}, "Datasets"),
      h("p", { class: "muted sub" }, "Every upload and transform is stored as an immutable, hashed version."))),
    h("div", { class: "stack" }, uploadForm(), list));

  const datasets = await api.get("/datasets");
  clear(list).append(h("div", { class: "card-head" }, h("h2", {}, "All datasets"), badge(`${datasets.length}`, "neutral")));
  if (!datasets.length) {
    list.append(h("div", { class: "empty" }, "No datasets yet. Upload a CSV, XLSX or JSON file above to get started."));
    return;
  }
  // Latest-version quality scores come from each dataset's lineage graph (one request per dataset).
  const scores = new Map();
  await Promise.all(datasets.map(async (d) => {
    if (!d.latest_version) return;
    try {
      const g = await api.get(`/datasets/${d.id}/lineage`, { quiet: true });
      const node = g.nodes.find((n) => n.version_id === d.latest_version.id);
      scores.set(d.id, node ? node.quality_score : null);
    } catch { /* score badge just shows — */ }
  }));
  list.append(table([
    { label: "Name", render: (d) => h("a", { href: `#/datasets/${d.id}` }, d.name) },
    { label: "Description", class: "wrap", render: (d) => d.description || h("span", { class: "muted" }, "—") },
    { label: "Versions", class: "num", render: (d) => d.version_count },
    { label: "Latest", render: (d) => (d.latest_version ? `v${d.latest_version.version_no}` : "—") },
    { label: "Rows × cols", class: "num", render: (d) => (d.latest_version ? `${d.latest_version.row_count.toLocaleString()} × ${d.latest_version.col_count}` : "—") },
    { label: "SHA-256", render: (d) => h("code", {}, shortSha(d.latest_version?.sha256)) },
    { label: "Quality", render: (d) => scoreBadge(scores.get(d.id)) },
    { label: "Created", render: (d) => fmtDate(d.created_at) },
  ], datasets, { onRowClick: (d) => { location.hash = `#/datasets/${d.id}`; } }));
}
