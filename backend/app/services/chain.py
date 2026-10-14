"""On-chain hash registration via web3.py, with a local-verification fallback.

Two modes:
- local (default): a Hardhat node; contract address/ABI from CHAIN_DEPLOYMENT_FILE; txs sent from the node's
  unlocked account.
- public network (e.g. Sepolia): CHAIN_CONTRACT_ADDRESS + the bundled ABI; txs signed with CHAIN_PRIVATE_KEY.
"""
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from eth_account import Account
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from web3 import Web3
from web3.exceptions import ContractLogicError, TransactionNotFound

from app.config import Settings
from app.db import SessionLocal
from app.models import ChainRecord, DatasetVersion, Experiment, utcnow_iso
from app.services import audit, ml, storage
from app.services.hashing import sha256_file

ARTIFACT_TYPES = ("dataset_version", "experiment")
RPC_TIMEOUT_S = 10
ABI_FILE = Path(__file__).resolve().parent.parent / "abi" / "ProvenanceRegistry.json"
EXPLORERS = {1: "https://etherscan.io", 11155111: "https://sepolia.etherscan.io", 17000: "https://holesky.etherscan.io"}
RESET_NOTE = ("The chain was reset after this was registered (the local Hardhat node keeps data in memory), so its "
              "on-chain record is gone. The artifact itself is unchanged. Use Retry pending or Register to re-anchor it.")

log = logging.getLogger("dataorigin.chain")


class ChainUnavailable(Exception):
    """Node unreachable, deployment missing, or no contract code at the address."""


def _deployment(settings: Settings) -> dict:
    """{address, abi, chainId?}: from CHAIN_CONTRACT_ADDRESS + bundled ABI, else from the deploy-script file."""
    if settings.chain_contract_address:
        return {"address": settings.chain_contract_address,
                "abi": json.loads(ABI_FILE.read_text(encoding="utf-8")), "chainId": None}
    path = settings.chain_deployment_file
    if not path.is_file():
        raise ChainUnavailable(f"no deployment file at {path}; run `npx hardhat run scripts/deploy.js "
                               f"--network localhost` in contracts/, or set CHAIN_CONTRACT_ADDRESS")
    return json.loads(path.read_text(encoding="utf-8"))


def _signer(settings: Settings):
    """LocalAccount for CHAIN_PRIVATE_KEY, or None (local node: use its unlocked account)."""
    if not settings.chain_private_key:
        return None
    try:
        return Account.from_key(settings.chain_private_key)
    except Exception as exc:  # noqa: BLE001 — never echo the key itself
        raise ChainUnavailable("CHAIN_PRIVATE_KEY is not a valid private key") from exc


def _web3(settings: Settings) -> Web3:
    return Web3(Web3.HTTPProvider(settings.chain_rpc_url, request_kwargs={"timeout": RPC_TIMEOUT_S}))


def connect(settings: Settings):
    """Returns (w3, contract, deployment). Raises ChainUnavailable."""
    if not settings.chain_enabled:
        raise ChainUnavailable("chain disabled (CHAIN_ENABLED=false)")
    dep = _deployment(settings)
    signer = _signer(settings)
    w3 = _web3(settings)
    if not w3.is_connected():
        raise ChainUnavailable(f"cannot reach node at {rpc_label(settings.chain_rpc_url)}")
    address = Web3.to_checksum_address(dep["address"])
    try:
        code = w3.eth.get_code(address)
        dep = {**dep, "chainId": dep.get("chainId") or w3.eth.chain_id}
    except Exception as exc:  # noqa: BLE001
        raise ChainUnavailable(redact(settings, f"node error: {exc}")) from exc
    if not code or code in (b"", b"\x00"):
        hint = ("check CHAIN_CONTRACT_ADDRESS and that CHAIN_RPC_URL is the same network" if settings.chain_contract_address
                else "the Hardhat node is in-memory, so redeploy after a restart")
        raise ChainUnavailable(f"no contract code at {address}; {hint}")
    w3.eth.default_account = signer.address if signer else w3.eth.accounts[0]
    return w3, w3.eth.contract(address=address, abi=dep["abi"]), dep


def _send_register(w3, contract, settings: Settings, record: ChainRecord):
    """Send register(); signed locally with CHAIN_PRIVATE_KEY on public networks. Returns the tx hash."""
    call = contract.functions.register(_to_bytes32(record.sha256), record.artifact_type, str(record.artifact_id))
    signer = _signer(settings)
    if signer is None:
        return call.transact()
    tx = call.build_transaction({"from": signer.address, "chainId": w3.eth.chain_id,
                                 "nonce": w3.eth.get_transaction_count(signer.address, "pending")})
    return w3.eth.send_raw_transaction(signer.sign_transaction(tx).raw_transaction)


def explorer_url(chain_id: int | None) -> str | None:
    return EXPLORERS.get(chain_id)


def rpc_label(url: str) -> str:
    """scheme://host only: hosted RPC URLs carry an API key in the path or query, which must never be shown."""
    scheme, _, rest = url.partition("://")
    return f"{scheme}://{rest.split('/', 1)[0].split('?', 1)[0]}" if rest else url


def redact(settings: Settings, text: str) -> str:
    """Strip the RPC URL (and so its API key) from any error text before it is stored or returned."""
    url = settings.chain_rpc_url
    text = text.replace(url, rpc_label(url))
    tail = url.partition("://")[2].partition("/")[2]  # e.g. "v2/<KEY>"
    return text.replace(tail, "…") if len(tail) >= 8 else text


def status(settings: Settings) -> dict:
    signer = None
    try:
        signer = _signer(settings)
    except ChainUnavailable:
        pass
    out = {"enabled": settings.chain_enabled, "connected": False, "chain_id": None, "contract_address": None,
           "contract_deployed": False, "redeploy_needed": False, "error": None, "network": rpc_label(settings.chain_rpc_url),
           "explorer": None, "signer": signer.address if signer else None}
    if not settings.chain_enabled:
        out["error"] = "chain disabled (CHAIN_ENABLED=false)"
        return out
    try:
        out["contract_address"] = _deployment(settings).get("address")
    except (ChainUnavailable, ValueError) as exc:
        out["error"] = str(exc)
    if settings.chain_private_key and signer is None:
        out["error"] = "CHAIN_PRIVATE_KEY is not a valid private key"
    w3 = _web3(settings)
    try:
        out["connected"] = w3.is_connected()
        if out["connected"]:
            out["chain_id"] = w3.eth.chain_id
            out["explorer"] = explorer_url(out["chain_id"])
            if out["contract_address"]:
                code = w3.eth.get_code(Web3.to_checksum_address(out["contract_address"]))
                out["contract_deployed"] = bool(code) and code != b"\x00"
                if not out["contract_deployed"]:
                    if settings.chain_contract_address:
                        out["error"] = "no contract at CHAIN_CONTRACT_ADDRESS on this network; check the address and RPC"
                    else:
                        out["redeploy_needed"] = True
                        out["error"] = "no contract code at the deployed address (node restarted?); redeploy"
        else:
            out["error"] = f"cannot reach node at {rpc_label(settings.chain_rpc_url)}"
    except Exception as exc:  # noqa: BLE001
        out["connected"], out["error"] = False, redact(settings, f"node error: {exc}")
    return out


def health(settings: Settings) -> str:
    if not settings.chain_enabled:
        return "disabled"
    s = status(settings)
    return "ok" if s["contract_deployed"] else "no_contract" if s["connected"] else "unreachable"


# --- artifacts -------------------------------------------------------------------------------------------------

def get_artifact(db: Session, artifact_type: str, artifact_id: int):
    if artifact_type not in ARTIFACT_TYPES:
        raise HTTPException(422, f"artifact_type must be one of {list(ARTIFACT_TYPES)}")
    model = DatasetVersion if artifact_type == "dataset_version" else Experiment
    obj = db.get(model, artifact_id)
    if obj is None:
        raise HTTPException(404, f"{artifact_type} {artifact_id} not found")
    if artifact_type == "experiment" and obj.status != "completed":
        raise HTTPException(409, f"experiment {artifact_id} is {obj.status}; only completed runs have a commitment")
    return obj


def recompute_commitment(exp: Experiment) -> str:
    return ml.commitment_of(exp.version.sha256, exp.version_id, exp.task, exp.algorithm, exp.target_column,
                            exp.hyperparams_json, exp.seed, exp.test_size, exp.metrics_json, exp.env_json)


def shas(settings: Settings, artifact_type: str, obj) -> tuple[str, str | None]:
    """(db_sha, current_sha). Dataset: current file sha (None if missing). Experiment: recomputed commitment."""
    if artifact_type == "dataset_version":
        path = storage.abs_path(settings.storage_dir, obj.file_path)
        return obj.sha256, sha256_file(path) if path.is_file() else None
    return obj.commitment_sha256, recompute_commitment(obj)


def latest_record(db: Session, artifact_type: str, artifact_id: int) -> ChainRecord | None:
    return db.scalar(
        select(ChainRecord).where(ChainRecord.artifact_type == artifact_type, ChainRecord.artifact_id == artifact_id)
        .order_by(ChainRecord.id.desc()).limit(1)
    )


def _to_bytes32(sha: str) -> bytes:
    return bytes.fromhex(sha)


def _iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="microseconds")


def _find_tx(contract, sha: str, from_block: int = 0) -> tuple[str | None, int | None]:
    """tx hash and block of the Registered event for this hash (None if not found, or if the RPC refuses the
    range — hosted RPCs cap eth_getLogs, so CHAIN_START_BLOCK should be the contract's deployment block)."""
    try:
        logs = contract.events.Registered.get_logs(from_block=from_block, argument_filters={"hash": _to_bytes32(sha)})
    except Exception:  # noqa: BLE001
        return None, None
    if not logs:
        return None, None
    return "0x" + logs[0]["transactionHash"].hex().removeprefix("0x"), logs[0]["blockNumber"]


def _onchain_record(contract, sha: str) -> dict | None:
    rec = contract.functions.getRecord(_to_bytes32(sha)).call()
    # Record tuple: (hash, artifactType, artifactId, registrant, timestamp)
    if not rec[4]:
        return None
    return {"artifact_type": rec[1], "artifact_id": rec[2], "registrant": rec[3], "timestamp": int(rec[4])}


def _submit(settings: Settings, record: ChainRecord) -> None:
    """Try to put record.sha256 on chain; updates record.status in place (registered / pending / failed)."""
    try:
        w3, contract, dep = connect(settings)
    except ChainUnavailable as exc:
        record.status, record.error = "pending", str(exc)
        return
    record.chain_id, record.contract_address = dep.get("chainId"), dep["address"]
    try:
        existing = _onchain_record(contract, record.sha256)
        if existing is None:
            tx = _send_register(w3, contract, settings, record)
            # A timeout here leaves the record pending; a retry adopts the tx if it was mined meanwhile.
            receipt = w3.eth.wait_for_transaction_receipt(tx, timeout=settings.chain_receipt_timeout)
            if receipt["status"] != 1:
                record.status, record.error = "failed", "transaction reverted"
                return
            existing = _onchain_record(contract, record.sha256)
            record.tx_hash = "0x" + bytes(receipt["transactionHash"]).hex().removeprefix("0x")
            record.block_number = receipt["blockNumber"]
        else:
            record.tx_hash, record.block_number = _find_tx(contract, record.sha256, settings.chain_start_block)
        record.status, record.error = "registered", None
        record.registered_at = _iso(existing["timestamp"]) if existing else utcnow_iso()
    except ContractLogicError as exc:
        record.status, record.error = "failed", redact(settings, f"reverted: {exc}")
    except Exception as exc:  # noqa: BLE001 — connection drops mid-call → retry later
        record.status, record.error = "pending", redact(settings, f"{type(exc).__name__}: {exc}")


def _tx_exists(w3, tx_hash: str | None) -> bool:
    if not tx_hash:
        return False
    try:
        w3.eth.get_transaction_receipt(tx_hash)
        return True
    except TransactionNotFound:
        return False


def _lost_to_reset(w3, contract, dep: dict, record: ChainRecord) -> bool:
    """True for a record we marked registered whose hash AND registration tx are both gone from the chain: the
    in-memory node was restarted and redeployed (usually at the same address). The contract is append-only, so a
    registered hash cannot disappear any other way."""
    if record.status != "registered":
        return False
    if record.contract_address and record.contract_address.lower() != dep["address"].lower():
        return True
    return _onchain_record(contract, record.sha256) is None and not _tx_exists(w3, record.tx_hash)


def _log(db: Session, record: ChainRecord, action: str) -> None:
    audit.log_event(db, f"chain.{action}", record.artifact_type, record.artifact_id, {
        "chain_record_id": record.id, "sha256": record.sha256, "status": record.status,
        "tx_hash": record.tx_hash, "block_number": record.block_number, "error": record.error,
    })


def register(db: Session, settings: Settings, artifact_type: str, artifact_id: int) -> ChainRecord:
    """Register the artifact's db sha. Idempotent: an existing registered record is returned as-is (unless a chain
    reset wiped it, then it is re-anchored), and a hash already on chain is adopted instead of reverting.
    Caller commits."""
    obj = get_artifact(db, artifact_type, artifact_id)
    db_sha, _ = shas(settings, artifact_type, obj)
    record = latest_record(db, artifact_type, artifact_id)
    action = "register"
    if record is not None and record.sha256 == db_sha and record.status == "registered":
        try:
            w3, contract, dep = connect(settings)
            if not _lost_to_reset(w3, contract, dep, record):
                return record
        except Exception:  # noqa: BLE001 — chain unavailable: nothing to compare against, keep the record
            return record
        action = "reanchor"
    if record is None or record.sha256 != db_sha:
        record = ChainRecord(artifact_type=artifact_type, artifact_id=artifact_id, sha256=db_sha, status="pending")
        db.add(record)
    _submit(settings, record)
    db.flush()
    _log(db, record, action)
    return record


def _lost_records(db: Session, settings: Settings) -> list[ChainRecord]:
    """Registered records wiped by a chain reset. Empty when the chain is unavailable (nothing to compare)."""
    try:
        w3, contract, dep = connect(settings)
        registered = db.scalars(select(ChainRecord).where(ChainRecord.status == "registered")
                                .order_by(ChainRecord.id))
        return [r for r in registered if _lost_to_reset(w3, contract, dep, r)]
    except Exception:  # noqa: BLE001
        return []


def retry_pending(db: Session, settings: Settings) -> tuple[list[ChainRecord], list[ChainRecord]]:
    """Retry pending records and re-anchor records lost to a chain reset. Returns (retried, reanchored)."""
    pending = list(db.scalars(select(ChainRecord).where(ChainRecord.status == "pending").order_by(ChainRecord.id)))
    lost = _lost_records(db, settings)
    for records, action in ((pending, "retry"), (lost, "reanchor")):
        for record in records:
            _submit(settings, record)
            db.flush()
            _log(db, record, action)
    return pending, lost


def resync_on_startup(settings: Settings) -> None:
    """Backend start: when the chain is reachable, retry pending records and re-anchor any lost to a node restart.
    Does nothing (and writes no audit events) when the chain is disabled or down; never blocks startup."""
    if not settings.chain_enabled:
        return
    try:
        connect(settings)
    except Exception as exc:  # noqa: BLE001
        log.info("chain resync skipped: %s", exc)
        return
    try:
        with SessionLocal() as db:
            pending, lost = retry_pending(db, settings)
            db.commit()
        if pending or lost:
            log.warning("chain resync: retried %d pending, re-anchored %d record(s) lost to a chain reset",
                        len(pending), len(lost))
    except Exception:  # noqa: BLE001
        log.exception("chain resync failed")


def verify(db: Session, settings: Settings, artifact_type: str, artifact_id: int, log: bool = True) -> dict:
    """Verdicts: VERIFIED, LOCAL_ONLY, TAMPERED, NOT_REGISTERED (see docs/structure.md). Caller commits.
    log=False for read-only callers (the audit report)."""
    obj = get_artifact(db, artifact_type, artifact_id)
    db_sha, current = shas(settings, artifact_type, obj)
    local_match = current == db_sha
    record = latest_record(db, artifact_type, artifact_id)
    if record is not None and record.sha256 != db_sha:
        record = None

    on_chain, chain_error, chain_reset = None, None, False
    try:
        w3, contract, dep = connect(settings)
        found = _onchain_record(contract, db_sha)
        tx_hash = record.tx_hash if record and record.tx_hash else (
            _find_tx(contract, db_sha, settings.chain_start_block)[0] if found else None)
        on_chain = {"registered": found is not None,
                    "timestamp": _iso(found["timestamp"]) if found else None,
                    "registrant": found["registrant"] if found else None, "tx_hash": tx_hash,
                    "explorer": explorer_url(dep.get("chainId"))}
        chain_reset = record is not None and found is None and _lost_to_reset(w3, contract, dep, record)
    except ChainUnavailable as exc:
        chain_error = str(exc)
    except Exception as exc:  # noqa: BLE001
        chain_error = redact(settings, f"{type(exc).__name__}: {exc}")

    if not local_match:
        verdict = "TAMPERED"
    elif on_chain and on_chain["registered"]:
        verdict = "VERIFIED"
    elif record is None:
        verdict = "NOT_REGISTERED"
    elif on_chain is None or record.status != "registered" or chain_reset:
        verdict = "LOCAL_ONLY"
    else:
        # The db says registered and the registration tx still exists on this chain, yet the hash is absent:
        # the db record does not match the chain.
        verdict = "TAMPERED"

    result = {"artifact_type": artifact_type, "artifact_id": artifact_id, "current_sha": current, "db_sha": db_sha,
              "local_match": local_match, "on_chain": on_chain, "verdict": verdict,
              "chain_status": record.status if record else "unregistered", "chain_error": chain_error,
              "chain_reset": chain_reset, "note": RESET_NOTE if chain_reset else None}
    if log:
        audit.log_event(db, "chain.verified", artifact_type, artifact_id,
                        {"verdict": verdict, "current_sha": current, "db_sha": db_sha})
    return result
