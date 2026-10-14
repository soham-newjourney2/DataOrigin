import { empty, h } from "./dom.js";

/**
 * table(columns, rows, {onRowClick, rowClass, emptyText})
 * columns: [{label, render(row) → Node|string, class?}]
 */
export function table(columns, rows, { onRowClick, rowClass, emptyText = "Nothing here yet." } = {}) {
  if (!rows.length) return empty(emptyText);
  const head = h("tr", {}, columns.map((c) => h("th", { class: c.class, scope: "col" }, c.label)));
  const body = rows.map((row) => h("tr", {
    class: [onRowClick ? "clickable" : "", rowClass ? rowClass(row) : ""].join(" ").trim() || null,
    onclick: onRowClick ? (ev) => {
      if (ev.target.closest("button, a, input, select")) return;
      onRowClick(row);
    } : null,
  }, columns.map((c) => h("td", { class: c.class }, c.render(row)))));
  return h("div", { class: "table-wrap" }, h("table", { class: "t" }, h("thead", {}, head), h("tbody", {}, body)));
}
