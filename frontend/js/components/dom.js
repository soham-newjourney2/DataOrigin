// Tiny DOM helpers. Strings become text nodes (never innerHTML), so API data is always escaped.

/** h("div", {class: "x", onclick: fn}, child, [children], "text") */
export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key.startsWith("on") && typeof value === "function") el.addEventListener(key.slice(2), value);
    else if (key === "class") el.className = value;
    else if (key === "dataset") Object.assign(el.dataset, value);
    else if (key in el && typeof value !== "string") el[key] = value;
    else el.setAttribute(key, value === true ? "" : value);
  }
  append(el, children);
  return el;
}

export function append(el, children) {
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return el;
}

export function clear(el) {
  el.replaceChildren();
  return el;
}

export const shortSha = (sha) => (sha ? sha.slice(0, 12) : "—");

export function fmtDate(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return isNaN(d) ? iso : d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

export function fmtNum(value, digits = 2) {
  if (value === null || value === undefined) return "—";
  if (typeof value !== "number") return String(value);
  if (Number.isInteger(value)) return value.toLocaleString();
  return value.toLocaleString(undefined, { maximumFractionDigits: digits });
}

export function scoreClass(score) {
  if (score === null || score === undefined) return "neutral";
  return score >= 85 ? "ok" : score >= 70 ? "warn" : "bad";
}

export function badge(text, cls = "neutral", title) {
  return h("span", { class: `badge ${cls}`, title }, text);
}

export function scoreBadge(score) {
  return badge(score === null || score === undefined ? "—" : score.toFixed(1), scoreClass(score), "Quality score");
}

export function loading(text = "Loading…") {
  return h("div", { class: "loading" }, h("span", { class: "spinner" }), " ", text);
}

export function empty(text) {
  return h("div", { class: "empty" }, text);
}

/** Parse a free-text param: numbers become numbers, everything else stays a string. */
export function parseValue(text) {
  const t = String(text).trim();
  return t !== "" && !isNaN(Number(t)) ? Number(t) : t;
}

const CHAIN = { registered: "ok", pending: "warn", failed: "bad", unregistered: "neutral" };

export function chainBadge(status) {
  return badge(status || "unregistered", CHAIN[status] || "neutral", "On-chain registration status");
}

const RUN = { completed: "ok", failed: "bad", running: "info" };

export function statusBadge(status) {
  return badge(status, RUN[status] || "neutral");
}

/** Main metric label for an experiment (accuracy for classification, r2 for regression). */
export function mainMetric(exp) {
  const name = exp.task === "regression" ? "r2" : "accuracy";
  const value = exp.metrics_json?.[name];
  return value === undefined || value === null ? "—" : `${name} ${value.toFixed(3)}`;
}

/** Load every dataset with its versions: [{...dataset, versions: [...]}]. */
export async function loadAllVersions(api) {
  const list = await api.get("/datasets");
  return Promise.all(list.map((d) => api.get(`/datasets/${d.id}`)));
}
