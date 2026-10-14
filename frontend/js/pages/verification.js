// Verification: chain status banner, artifact picker, Register / Verify, big verdict badge, demo tamper.
import { api } from "../api.js";
import { badge, chainBadge, clear, fmtDate, h, loadAllVersions, loading } from "../components/dom.js";
import { toast } from "../components/toast.js";

const NETWORK_NAMES = { 1: "Ethereum mainnet", 11155111: "Ethereum Sepolia", 17000: "Ethereum Holesky", 31337: "local Hardhat node" };

/** <code>value</code>, linked to the block explorer when the network has one. */
function explorerLink(explorer, kind, value) {
  const code = h("code", {}, value);
  return explorer ? h("a", { href: `${explorer}/${kind}/${value}`, target: "_blank", rel: "noopener" }, code, " ↗") : code;
}

const VERDICT = {
  VERIFIED: ["ok", "Local file matches its recorded hash, and the hash is registered on chain."],
  LOCAL_ONLY: ["warn", "Local file matches, but the on-chain record is pending or the chain is unavailable."],
  TAMPERED: ["bad", "The artifact no longer matches its recorded hash (or its on-chain record is missing)."],
  NOT_REGISTERED: ["neutral", "The local file matches, but this artifact has never been registered."],
};

function statusBanner(st, onRetry) {
  let cls, title, detail;
  if (!st.enabled) [cls, title, detail] = ["warn", "Chain disabled", "CHAIN_ENABLED=false — registrations are kept as pending and verification is local only."];
  else if (!st.connected) [cls, title, detail] = ["bad", "Chain node unreachable", st.error || "Start the Hardhat node."];
  else if (!st.contract_deployed) [cls, title, detail] = ["bad", "Contract not deployed", st.error || "Run the deploy script."];
  else {
    const name = NETWORK_NAMES[st.chain_id] || `chain ${st.chain_id}`;
    [cls, title, detail] = ["ok", `Connected to ${name}`,
      h("span", {}, "contract ", explorerLink(st.explorer, "address", st.contract_address),
        st.signer ? h("span", {}, " · signer ", explorerLink(st.explorer, "address", st.signer)) : null)];
  }
  const hint = st.redeploy_needed
    ? h("div", { class: "small" }, "The Hardhat node is in-memory: after a restart run ", h("code", {}, "npx hardhat run scripts/deploy.js --network localhost"), " in contracts/.")
    : null;
  return h("div", { class: `banner ${cls}` },
    h("span", { class: `dot ${cls}` }),
    h("div", {}, h("strong", {}, title), h("div", { class: "small" }, detail), hint),
    h("span", { class: "spacer" }),
    h("button", { class: "btn sm", onclick: onRetry }, "Retry pending"));
}

function verdictView(v) {
  const [cls, explain] = VERDICT[v.verdict] || ["neutral", ""];
  const oc = v.on_chain;
  return h("div", { class: "stack" },
    h("div", { class: `verdict ${cls}`, role: "status" },
      h("div", { class: "verdict-label" }, v.verdict.replace("_", " ")),
      h("div", { class: "small" }, explain)),
    v.note ? h("div", { class: "banner warn" }, h("span", { class: "dot warn" }),
      h("div", {}, h("strong", {}, "Chain was reset"), h("div", { class: "small" }, v.note))) : null,
    h("dl", { class: "kv" },
      h("dt", {}, "Recorded SHA-256"), h("dd", {}, h("code", {}, v.db_sha)),
      h("dt", {}, v.artifact_type === "experiment" ? "Recomputed commitment" : "Current file SHA-256"),
      h("dd", {}, v.current_sha ? h("code", { class: v.local_match ? null : "mismatch" }, v.current_sha) : badge("file missing", "bad")),
      h("dt", {}, "Local match"), h("dd", {}, badge(v.local_match ? "yes" : "no", v.local_match ? "ok" : "bad")),
      h("dt", {}, "Chain record"), h("dd", {}, chainBadge(v.chain_status)),
      h("dt", {}, "On chain"), h("dd", {}, oc === null ? h("span", { class: "muted" }, v.chain_error || "unavailable")
        : oc.registered ? h("div", {}, badge("registered", "ok"), ` ${fmtDate(oc.timestamp)}`,
          h("div", { class: "small muted" }, "registrant ", explorerLink(oc.explorer, "address", oc.registrant)),
          oc.tx_hash ? h("div", { class: "small muted" }, "tx ", explorerLink(oc.explorer, "tx", oc.tx_hash)) : null)
          : badge("not on chain", "neutral"))));
}

export async function render(el, { type, id }, isStale) {
  el.append(loading());
  const [st, all, runs, dev] = await Promise.all([
    api.get("/chain/status"), loadAllVersions(api), api.get("/experiments"),
    api.get("/dev/status", { quiet: true }).catch(() => ({ demo_mode: false })),
  ]);
  if (isStale()) return;

  const bannerSlot = h("div");
  const retry = async () => {
    const r = await api.post("/chain/retry-pending");
    const parts = [r.retried && `retried ${r.retried} pending`, r.reanchored && `re-anchored ${r.reanchored} lost to a chain reset`].filter(Boolean);
    toast(parts.length ? `${parts.join(", ")} · ${r.registered} now registered.` : "Nothing to retry: no pending or reset records.", r.registered ? "ok" : "info");
    clear(bannerSlot).append(statusBanner(await api.get("/chain/status"), retry));
    if (r.registered && artifact()) await doVerify();
  };
  bannerSlot.append(statusBanner(st, retry));

  const typeSel = h("select", { "aria-label": "Artifact type" },
    h("option", { value: "dataset_version" }, "Dataset version"), h("option", { value: "experiment" }, "Experiment"));
  const artSel = h("select", { "aria-label": "Artifact" });
  const fillArtifacts = () => {
    if (typeSel.value === "dataset_version") {
      clear(artSel).append(...all.map((d) => h("optgroup", { label: d.name },
        d.versions.map((v) => h("option", { value: v.id }, `${d.name} · v${v.version_no} · ${v.sha256.slice(0, 10)}`)))));
    } else {
      const done = runs.filter((r) => r.status === "completed");
      clear(artSel).append(...done.map((r) => h("option", { value: r.id }, `#${r.id} ${r.name}`)));
    }
    if (!artSel.options.length) artSel.append(h("option", { value: "" }, "Nothing to verify yet"));
  };
  typeSel.value = type || "dataset_version";
  fillArtifacts();
  if (id) artSel.value = String(id);

  const result = h("div", { class: "card" }, h("p", { class: "muted" }, "Pick an artifact, then Verify or Register."));
  const artifact = () => (artSel.value ? { artifact_type: typeSel.value, artifact_id: +artSel.value } : null);
  const busy = async (btn, fn) => {
    btn.disabled = true;
    try { await fn(); } finally { btn.disabled = !artSel.value; }
  };
  const doVerify = async () => {
    const a = artifact();
    if (!a) return;
    history.replaceState(null, "", `#/verification/${a.artifact_type}/${a.artifact_id}`);
    clear(result).append(loading("Verifying…"));
    try {
      const v = await api.get(`/chain/verify/${a.artifact_type}/${a.artifact_id}`);
      clear(result).append(verdictView(v));
    } catch (err) {
      clear(result).append(h("div", { class: "error-box" }, err.message));
    }
  };
  const verifyBtn = h("button", { class: "btn primary" }, "Verify");
  verifyBtn.addEventListener("click", () => busy(verifyBtn, doVerify));
  const registerBtn = h("button", { class: "btn" }, "Register");
  registerBtn.addEventListener("click", () => busy(registerBtn, async () => {
    const a = artifact();
    if (!a) return;
    const rec = await api.post("/chain/register", a);
    toast(rec.status === "registered" ? `Registered${rec.tx_hash ? ` · tx ${rec.tx_hash.slice(0, 12)}…` : ""}`
      : rec.status === "pending" ? `Pending: ${rec.error}` : `Failed: ${rec.error}`,
    rec.status === "registered" ? "ok" : rec.status === "pending" ? "warn" : "error");
    await doVerify();
  }));
  const tamperBtn = h("button", { class: "btn danger", title: "Demo only: appends bytes to the stored file" }, "Simulate tamper");
  tamperBtn.addEventListener("click", () => busy(tamperBtn, async () => {
    const a = artifact();
    if (!a || a.artifact_type !== "dataset_version") return;
    if (!confirm("Append bytes to this version's stored file? This permanently changes the file on disk (demo only).")) return;
    await api.post(`/dev/tamper/${a.artifact_id}`);
    toast("File tampered. Verifying…", "warn");
    await doVerify();
  }));
  const syncTamper = () => { tamperBtn.hidden = !dev.demo_mode || typeSel.value !== "dataset_version"; };
  const syncButtons = () => { for (const b of [verifyBtn, registerBtn, tamperBtn]) b.disabled = !artSel.value; };
  typeSel.addEventListener("change", () => { fillArtifacts(); syncTamper(); syncButtons(); clear(result).append(h("p", { class: "muted" }, "Pick an artifact, then Verify or Register.")); });
  syncTamper();
  syncButtons();

  clear(el).append(
    h("div", { class: "page-head" }, h("div", {}, h("h1", {}, "Verification"),
      h("p", { class: "muted sub" }, "Check stored artifacts against their recorded hashes and the on-chain registry."))),
    h("div", { class: "stack" },
      bannerSlot,
      h("div", { class: "card" },
        h("div", { class: "row" },
          h("label", { class: "field" }, "Type", typeSel),
          h("label", { class: "field", style: "flex:1;min-width:240px" }, "Artifact", artSel),
          h("div", { class: "row", style: "align-self:flex-end" }, verifyBtn, registerBtn, tamperBtn))),
      result));
  if (id && artifact()) await doVerify();
}
