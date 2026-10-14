import { clear, h } from "./dom.js";

/**
 * tabs([{id, label, render(panel)}], activeId, onChange?) → element.
 * render may be async; only the active tab renders.
 */
export function tabs(items, activeId, onChange) {
  const bar = h("div", { class: "tabs", role: "tablist" });
  const panel = h("div", { role: "tabpanel" });
  let active = items.some((t) => t.id === activeId) ? activeId : items[0].id;

  const show = (id) => {
    active = id;
    for (const b of bar.children) b.setAttribute("aria-selected", String(b.dataset.id === id));
    clear(panel);
    items.find((t) => t.id === id).render(panel);
  };
  for (const t of items) {
    bar.append(h("button", {
      role: "tab", dataset: { id: t.id }, "aria-selected": String(t.id === active),
      onclick: () => { if (t.id !== active) { show(t.id); onChange?.(t.id); } },
    }, t.label));
  }
  show(active);
  const root = h("div", {}, bar, panel);
  root.select = (id) => { show(id); onChange?.(id); };
  return root;
}
