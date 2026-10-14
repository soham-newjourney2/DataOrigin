// Hash router: #/ (dashboard), #/datasets, #/datasets/:id[/v/:vid[/:tab]], #/lineage[/:id],
// #/experiments[/new/:vid], #/experiments/:id, #/experiments/compare/:ids, #/verification[/:type/:id], #/audit
import { api } from "./api.js";
import { clear, h } from "./components/dom.js";
import { initThemeToggle } from "./components/theme.js";

const ROUTES = [
  { pattern: /^\/(?:dashboard)?\/?$/, nav: "dashboard", load: () => import("./pages/dashboard.js") },
  { pattern: /^\/datasets\/?$/, nav: "datasets", load: () => import("./pages/datasets.js") },
  { pattern: /^\/datasets\/(\d+)(?:\/v\/(\d+)(?:\/([a-z]+))?)?\/?$/, nav: "datasets",
    load: () => import("./pages/dataset.js"), params: (m) => ({ id: +m[1], vid: m[2] ? +m[2] : null, tab: m[3] || null }) },
  { pattern: /^\/lineage(?:\/(\d+))?\/?$/, nav: "lineage",
    load: () => import("./pages/lineage.js"), params: (m) => ({ id: m[1] ? +m[1] : null }) },
  { pattern: /^\/experiments\/compare\/([\d,]+)\/?$/, nav: "experiments",
    load: () => import("./pages/compare.js"), params: (m) => ({ ids: m[1].split(",").filter(Boolean).map(Number) }) },
  { pattern: /^\/experiments\/(\d+)\/?$/, nav: "experiments",
    load: () => import("./pages/experiment.js"), params: (m) => ({ id: +m[1] }) },
  { pattern: /^\/experiments(?:\/new\/(\d+))?\/?$/, nav: "experiments",
    load: () => import("./pages/experiments.js"), params: (m) => ({ vid: m[1] ? +m[1] : null }) },
  { pattern: /^\/verification(?:\/(dataset_version|experiment)\/(\d+))?\/?$/, nav: "verification",
    load: () => import("./pages/verification.js"), params: (m) => ({ type: m[1] || null, id: m[2] ? +m[2] : null }) },
  { pattern: /^\/audit\/?$/, nav: "audit", load: () => import("./pages/audit.js") },
];

const app = document.getElementById("app");
let renderToken = 0;

function setActiveNav(name) {
  for (const a of document.querySelectorAll("[data-nav]")) a.classList.toggle("active", a.dataset.nav === name);
}

async function route() {
  const path = location.hash.replace(/^#/, "") || "/";
  const token = ++renderToken;
  for (const r of ROUTES) {
    const m = path.match(r.pattern);
    if (!m) continue;
    setActiveNav(typeof r.nav === "function" ? r.nav(m) : r.nav);
    const page = await r.load();
    if (token !== renderToken) return;
    clear(app);
    try {
      await page.render(app, r.params ? r.params(m) : {}, () => token !== renderToken);
    } catch (err) {
      if (token !== renderToken) return;
      console.error(err);
      clear(app).append(err.status === 0 ? offlineCard() : h("div", { class: "card" }, h("h2", {}, "Something went wrong"),
        h("p", { class: "muted" }, err.message || String(err)), h("a", { class: "btn", href: "#/datasets" }, "Back to datasets")));
    }
    return;
  }
  setActiveNav(null);
  clear(app).append(h("div", { class: "card" }, h("h2", {}, "Page not found"),
    h("p", { class: "muted" }, `No page at #${path}.`), h("a", { class: "btn", href: "#/datasets" }, "Go to datasets")));
}

const START_CMD = ".venv/Scripts/python -m uvicorn app.main:app --port 8000";

function offlineCard() {
  return h("div", { class: "card empty" },
    h("h2", {}, "Can't reach the DataOrigin backend"),
    h("p", { class: "muted" }, "Start it from the backend folder, then retry:"),
    h("p", {}, h("code", {}, START_CMD)),
    h("button", { class: "btn primary", onclick: () => { health(); route(); } }, "Retry"));
}

function setBanner(cls, title, detail, action) {
  const banner = document.getElementById("global-banner");
  if (!cls) { banner.hidden = true; clear(banner); return; }
  clear(banner).append(h("div", { class: `banner ${cls}` }, h("span", { class: `dot ${cls}` }),
    h("div", {}, h("strong", {}, title), h("div", { class: "small" }, detail)), h("span", { class: "spacer" }), action));
  banner.hidden = false;
}

let wasOffline = false;
let healthTimer = null;

async function health() {
  clearTimeout(healthTimer);
  const box = document.getElementById("health");
  const dot = box.querySelector(".dot"), text = box.querySelector(".health-text");
  let offline = false;
  try {
    const hc = await api.get("/health", { quiet: true });
    const chain = { ok: ["ok", "chain connected"], disabled: ["warn", "chain disabled"],
      no_contract: ["warn", "contract not deployed"], unreachable: ["warn", "chain unreachable"] }[hc.chain]
      || ["warn", `chain ${hc.chain}`];
    dot.className = `dot ${hc.db === "ok" ? chain[0] : "bad"}`;
    text.textContent = hc.db === "ok" ? `API ok · ${chain[1]}` : "database error";
    if (hc.db !== "ok") setBanner("bad", "Database error", "The backend is up but cannot reach its database.");
    else if (hc.chain === "unreachable" || hc.chain === "no_contract") {
      setBanner("warn", hc.chain === "unreachable" ? "Chain node disconnected" : "Contract not deployed",
        "Registrations are saved as pending and verification falls back to local hashes. Start the Hardhat node and deploy, then retry pending records.",
        h("a", { class: "btn sm", href: "#/verification" }, "Open Verification"));
    } else setBanner(null);
  } catch {
    offline = true;
    dot.className = "dot bad";
    text.textContent = "API unreachable";
    setBanner("bad", "Backend unreachable", `Start it from backend/: ${START_CMD}`,
      h("button", { class: "btn sm", onclick: () => health() }, "Retry"));
  }
  if (wasOffline && !offline) route(); // came back: reload the current page
  wasOffline = offline;
  healthTimer = setTimeout(health, offline ? 5000 : 30000);
}

window.addEventListener("hashchange", route);
initThemeToggle(document.getElementById("theme-toggle"));
route();
health();
