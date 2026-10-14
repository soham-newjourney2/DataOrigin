// Audit log: hash-chained events table with filters, plus "Verify log integrity".
import { api } from "../api.js";
import { badge, clear, fmtDate, h, loading } from "../components/dom.js";
import { table } from "../components/table.js";

const ENTITY_LINK = {
  dataset: (id) => `#/datasets/${id}`,
  experiment: (id) => `#/experiments/${id}`,
  dataset_version: null, // needs the dataset id; link via verification instead
};

function entityCell(e) {
  const text = `${e.entity_type} #${e.entity_id}`;
  const link = ENTITY_LINK[e.entity_type];
  if (link) return h("a", { href: link(e.entity_id) }, text);
  if (e.entity_type === "dataset_version") return h("a", { href: `#/verification/dataset_version/${e.entity_id}` }, text);
  return text;
}

function payloadCell(payload) {
  const text = JSON.stringify(payload);
  if (text.length <= 80) return h("code", {}, text);
  return h("details", {}, h("summary", {}, h("code", {}, text.slice(0, 77) + "…")),
    h("pre", { class: "payload" }, JSON.stringify(payload, null, 2)));
}

export async function render(el, _params, isStale) {
  const typeSel = h("select", { "aria-label": "Entity type" },
    ["", "dataset", "dataset_version", "experiment"].map((t) => h("option", { value: t }, t || "All entities")));
  const idInput = h("input", { type: "number", min: "1", placeholder: "Entity id", style: "width:120px" });
  const limitSel = h("select", { "aria-label": "Limit" }, [100, 250, 1000].map((n) => h("option", { value: n }, `Last ${n}`)));
  const integrity = h("div");
  const list = h("div", {}, loading());

  const load = async () => {
    const q = new URLSearchParams({ limit: limitSel.value });
    if (typeSel.value) q.set("entity_type", typeSel.value);
    if (idInput.value) q.set("entity_id", idInput.value);
    clear(list).append(loading());
    const events = await api.get(`/audit/events?${q}`);
    if (isStale()) return;
    clear(list).append(table([
      { label: "#", class: "num", render: (e) => e.id },
      { label: "Time", render: (e) => fmtDate(e.created_at) },
      { label: "Event", render: (e) => badge(e.event_type, e.event_type.startsWith("dev.") ? "bad" : e.event_type.startsWith("chain.") ? "info" : "neutral") },
      { label: "Entity", render: entityCell },
      { label: "Payload", class: "wrap", render: (e) => payloadCell(e.payload_json) },
      { label: "Hash", render: (e) => h("code", { title: `event ${e.event_hash}\nprev ${e.prev_hash}` }, e.event_hash.slice(0, 12)) },
    ], events, { emptyText: "No events match these filters." }));
  };

  const verifyBtn = h("button", { class: "btn primary" }, "Verify log integrity");
  verifyBtn.addEventListener("click", async () => {
    verifyBtn.disabled = true;
    try {
      const r = await api.get("/audit/verify");
      clear(integrity).append(h("div", { class: `banner ${r.valid ? "ok" : "bad"}` },
        h("span", { class: `dot ${r.valid ? "ok" : "bad"}` }),
        h("div", {}, h("strong", {}, r.valid ? "Audit log intact" : `Audit log BROKEN at event #${r.broken_at}`),
          h("div", { class: "small" }, r.valid
            ? `All ${r.checked} events re-hash correctly and each links to the previous one.`
            : `Event #${r.broken_at} does not match its stored hash or chain link; ${r.checked} events checked.`))));
    } finally {
      verifyBtn.disabled = false;
    }
  });

  for (const c of [typeSel, limitSel]) c.addEventListener("change", load);
  idInput.addEventListener("change", load);

  el.append(
    h("div", { class: "page-head" },
      h("div", {}, h("h1", {}, "Audit log"),
        h("p", { class: "muted sub" }, "Every mutating action, hash-chained: each event's hash covers the previous event's hash.")),
      verifyBtn),
    h("div", { class: "stack" },
      integrity,
      h("div", { class: "card" },
        h("div", { class: "row", style: "margin-bottom:12px" }, typeSel, idInput, limitSel,
          h("button", { class: "btn ghost", onclick: load }, "Refresh")),
        list)));
  await load();
}
