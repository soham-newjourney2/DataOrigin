// Side-by-side run comparison with differing fields highlighted.
import { api } from "../api.js";
import { badge, clear, h, loading } from "../components/dom.js";

const fmt = (v) => (v === null || v === undefined ? "—" : typeof v === "object" ? JSON.stringify(v) : String(v));

export async function render(el, { ids }, isStale) {
  el.append(loading());
  const cmp = await api.get(`/experiments/compare?ids=${ids.join(",")}`);
  if (isStale()) return;
  const nameRow = cmp.fields.find((f) => f.field === "name");
  const diffCount = cmp.fields.filter((f) => f.differs).length;
  let onlyDiffs = false;

  const body = h("tbody");
  const draw = () => {
    clear(body).append(...cmp.fields.filter((f) => !onlyDiffs || f.differs).map((f) => h("tr", { class: f.differs ? "differs" : null },
      h("th", { scope: "row" }, f.field, f.differs ? h("span", { class: "diff-mark", title: "Values differ" }, " ≠") : null),
      f.values.map((v) => h("td", { class: "wrap" }, fmt(v))))));
  };
  const toggle = h("label", { class: "chip" }, h("input", { type: "checkbox", onchange: (e) => { onlyDiffs = e.target.checked; draw(); } }),
    "Show only differences");
  draw();

  clear(el).append(
    h("div", { class: "crumbs" }, h("a", { href: "#/experiments" }, "Experiments"), " / Compare"),
    h("div", { class: "page-head" },
      h("div", {}, h("h1", {}, `Comparing ${cmp.ids.length} runs`),
        h("p", { class: "muted sub" }, `${diffCount} of ${cmp.fields.length} fields differ.`)),
      toggle),
    h("div", { class: "card" }, h("div", { class: "table-wrap" }, h("table", { class: "t compare" },
      h("thead", {}, h("tr", {}, h("th", {}, "Field"), cmp.ids.map((id, i) => h("th", {},
        h("a", { href: `#/experiments/${id}` }, `#${id}`), " ", h("span", { class: "muted" }, fmt(nameRow?.values[i])))))),
      body))),
    h("p", { class: "muted small", style: "margin-top:10px" }, "Numbers within 1e-9 count as equal. ",
      badge("≠", "warn"), " marks a differing field."));
}
