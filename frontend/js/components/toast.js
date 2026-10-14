import { h } from "./dom.js";

const TIMEOUT = { ok: 3500, info: 4000, warn: 6000, error: 8000 };

/** toast(message, "ok" | "info" | "warn" | "error") */
export function toast(message, type = "info") {
  const box = document.getElementById("toasts");
  if (!box) return;
  // Don't stack identical messages (e.g. several requests failing while the backend is down).
  if ([...box.children].some((t) => t.dataset.msg === message)) return;
  const close = () => el.remove();
  const el = h("div", { class: `toast ${type}`, role: type === "error" ? "alert" : "status" },
    h("span", {}, message), h("button", { "aria-label": "Dismiss", onclick: close }, "×"));
  el.dataset.msg = message;
  box.append(el);
  setTimeout(close, TIMEOUT[type] ?? 4000);
}
