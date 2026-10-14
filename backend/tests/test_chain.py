import dataclasses
import io
import uuid

import pandas as pd
import pytest
from sqlalchemy import delete

from app.db import SessionLocal
from app.models import ChainRecord
from app.services import chain


def _set(client, **changes):
    client.app.state.settings = dataclasses.replace(client.app.state.settings, **changes)


@pytest.fixture
def unique_csv(sample_csv) -> bytes:
    """sample.csv plus a constant uuid column, so every run gets a hash that is new on the (persistent) node."""
    df = pd.read_csv(io.BytesIO(sample_csv)).assign(run_id=uuid.uuid4().hex)
    return df.to_csv(index=False).encode()


@pytest.fixture
def version(upload, unique_csv) -> dict:
    return upload(f"c-{uuid.uuid4().hex[:8]}", "s.csv", unique_csv).json()["versions"][0]


@pytest.fixture
def experiment(client, version) -> dict:
    r = client.post("/api/experiments", json={"version_id": version["id"], "name": "lr", "task": "classification",
                                              "algorithm": "logistic_regression", "target_column": "purchased"})
    assert r.status_code == 201, r.json()
    return r.json()


@pytest.fixture
def chain_on(client):
    _set(client, chain_enabled=True)
    st = chain.status(client.app.state.settings)
    if not st["contract_deployed"]:
        pytest.skip(f"Hardhat node/contract not available: {st['error']}")
    return st


def _register(client, artifact_type, artifact_id):
    return client.post("/api/chain/register", json={"artifact_type": artifact_type, "artifact_id": artifact_id})


def _verify(client, artifact_type, artifact_id):
    return client.get(f"/api/chain/verify/{artifact_type}/{artifact_id}").json()


# --- chain disabled (fallback) -----------------------------------------------------------------------------------

def test_disabled_status_and_health(client):
    st = client.get("/api/chain/status").json()
    assert st["enabled"] is False and st["connected"] is False and "disabled" in st["error"]
    assert client.get("/api/health").json()["chain"] == "disabled"


def test_disabled_register_pending_and_local_only(client, version, experiment):
    assert _verify(client, "dataset_version", version["id"])["verdict"] == "NOT_REGISTERED"
    r = _register(client, "dataset_version", version["id"])
    assert r.status_code == 200
    rec = r.json()
    assert rec["status"] == "pending" and rec["tx_hash"] is None and rec["sha256"] == version["sha256"]
    assert "disabled" in rec["error"]
    v = _verify(client, "dataset_version", version["id"])
    assert v["verdict"] == "LOCAL_ONLY" and v["local_match"] is True and v["on_chain"] is None
    assert v["chain_status"] == "pending"

    # Registering again reuses the same pending record
    assert _register(client, "dataset_version", version["id"]).json()["id"] == rec["id"]

    e = _register(client, "experiment", experiment["id"]).json()
    assert e["status"] == "pending" and e["sha256"] == experiment["commitment_sha256"]
    ve = _verify(client, "experiment", experiment["id"])
    assert ve["verdict"] == "LOCAL_ONLY" and ve["current_sha"] == ve["db_sha"]

    retry = client.post("/api/chain/retry-pending").json()
    assert retry["retried"] == 2 and retry["registered"] == 0

    lineage = client.get(f"/api/datasets/{version['dataset_id']}/lineage").json()
    assert {n["chain_status"] for n in lineage["nodes"]} == {"pending"}

    client.post(f"/api/dev/tamper/{version['id']}")
    assert _verify(client, "dataset_version", version["id"])["verdict"] == "TAMPERED"

    types = [ev["event_type"] for ev in client.get("/api/audit/events").json()]
    assert {"chain.register", "chain.retry", "chain.verified"} <= set(types)
    assert client.get("/api/audit/verify").json()["valid"] is True


def test_unreachable_node_is_pending(client, version, tmp_path):
    _set(client, chain_enabled=True, chain_rpc_url="http://127.0.0.1:1")
    st = client.get("/api/chain/status").json()
    assert st["enabled"] is True and st["connected"] is False
    rec = _register(client, "dataset_version", version["id"]).json()
    assert rec["status"] == "pending" and rec["error"]
    assert _verify(client, "dataset_version", version["id"])["verdict"] == "LOCAL_ONLY"
    assert client.get("/api/health").json()["chain"] == "unreachable"

    _set(client, chain_deployment_file=tmp_path / "missing.json")
    assert "no deployment file" in _register(client, "dataset_version", version["id"]).json()["error"]


def test_register_validation(client, version):
    assert _register(client, "model", 1).status_code == 422
    assert _register(client, "dataset_version", 999).status_code == 404
    assert client.get("/api/chain/verify/experiment/999").status_code == 404


def test_experiment_commitment_tamper_detected(client, experiment):
    with SessionLocal() as db:
        from app.models import Experiment
        db.get(Experiment, experiment["id"]).seed = 1
        db.commit()
    v = _verify(client, "experiment", experiment["id"])
    assert v["verdict"] == "TAMPERED" and v["local_match"] is False


# --- live chain (skipped without a node + deployed contract) ------------------------------------------------------

@pytest.mark.chain
def test_chain_register_verify_tamper(client, chain_on, version):
    rec = _register(client, "dataset_version", version["id"]).json()
    assert rec["status"] == "registered" and rec["tx_hash"].startswith("0x") and rec["block_number"]
    assert rec["contract_address"] == chain_on["contract_address"] and rec["chain_id"] == chain_on["chain_id"]
    v = _verify(client, "dataset_version", version["id"])
    assert v["verdict"] == "VERIFIED" and v["on_chain"]["registered"] is True
    assert v["on_chain"]["tx_hash"] == rec["tx_hash"] and v["on_chain"]["registrant"]

    again = _register(client, "dataset_version", version["id"]).json()
    assert again["id"] == rec["id"] and again["tx_hash"] == rec["tx_hash"]
    assert client.get("/api/health").json()["chain"] == "ok"

    client.post(f"/api/dev/tamper/{version['id']}")
    t = _verify(client, "dataset_version", version["id"])
    assert t["verdict"] == "TAMPERED" and t["on_chain"]["registered"] is True


@pytest.mark.chain
def test_chain_experiment_verified(client, chain_on, experiment):
    rec = _register(client, "experiment", experiment["id"]).json()
    assert rec["status"] == "registered" and rec["sha256"] == experiment["commitment_sha256"]
    assert _verify(client, "experiment", experiment["id"])["verdict"] == "VERIFIED"


@pytest.mark.chain
def test_chain_retry_pending_and_adopt_existing(client, chain_on, version):
    _set(client, chain_enabled=False)
    assert _register(client, "dataset_version", version["id"]).json()["status"] == "pending"
    _set(client, chain_enabled=True)
    retry = client.post("/api/chain/retry-pending").json()
    assert retry["retried"] == 1 and retry["registered"] == 1
    tx = retry["records"][0]["tx_hash"]

    # Forget the local record: registering again adopts the on-chain record (no revert), tx found via logs
    with SessionLocal() as db:
        db.execute(delete(ChainRecord))
        db.commit()
    rec = _register(client, "dataset_version", version["id"]).json()
    assert rec["status"] == "registered" and rec["tx_hash"] == tx
    assert _verify(client, "dataset_version", version["id"])["verdict"] == "VERIFIED"


# --- chain reset (in-memory Hardhat node restarted + redeployed at the same address) ------------------------------

FAKE_TX = "0x" + "ab" * 32  # a tx hash that does not exist on the chain


def _fake_registered(version, chain_on, tx_hash=FAKE_TX):
    """The exact state a node restart leaves behind: db says registered, the chain has neither hash nor tx."""
    with SessionLocal() as db:
        db.add(ChainRecord(artifact_type="dataset_version", artifact_id=version["id"], sha256=version["sha256"],
                           status="registered", tx_hash=tx_hash, block_number=1, chain_id=chain_on["chain_id"],
                           contract_address=chain_on["contract_address"]))
        db.commit()


@pytest.mark.chain
def test_chain_reset_is_local_only_not_tampered(client, chain_on, version):
    _fake_registered(version, chain_on)
    v = _verify(client, "dataset_version", version["id"])
    assert v["verdict"] == "LOCAL_ONLY" and v["chain_reset"] is True and v["local_match"] is True
    assert "reset" in v["note"]

    rec = _register(client, "dataset_version", version["id"]).json()  # Register re-anchors instead of no-op
    assert rec["status"] == "registered" and rec["tx_hash"] != FAKE_TX
    v = _verify(client, "dataset_version", version["id"])
    assert v["verdict"] == "VERIFIED" and v["chain_reset"] is False and v["note"] is None
    assert "chain.reanchor" in [e["event_type"] for e in client.get("/api/audit/events").json()]


@pytest.mark.chain
def test_retry_pending_reanchors_reset_records(client, chain_on, version):
    _fake_registered(version, chain_on)
    r = client.post("/api/chain/retry-pending").json()
    assert r["retried"] == 0 and r["reanchored"] == 1 and r["registered"] == 1
    assert _verify(client, "dataset_version", version["id"])["verdict"] == "VERIFIED"
    assert client.post("/api/chain/retry-pending").json()["reanchored"] == 0  # nothing lost any more


@pytest.mark.chain
def test_startup_resync_reanchors(client, chain_on, version):
    _fake_registered(version, chain_on)
    chain.resync_on_startup(client.app.state.settings)
    assert _verify(client, "dataset_version", version["id"])["verdict"] == "VERIFIED"


@pytest.mark.chain
def test_db_record_contradicting_chain_is_tampered(client, chain_on, version, upload, unique_csv):
    # A real registration tx exists on this chain, but our db claims it registered a hash that is not there.
    other = upload(f"o-{uuid.uuid4().hex[:8]}", "o.csv", unique_csv + b"\n").json()["versions"][0]
    real_tx = _register(client, "dataset_version", other["id"]).json()["tx_hash"]
    _fake_registered(version, chain_on, tx_hash=real_tx)
    v = _verify(client, "dataset_version", version["id"])
    assert v["verdict"] == "TAMPERED" and v["chain_reset"] is False and v["local_match"] is True


@pytest.mark.chain
def test_real_file_tamper_still_tampered_after_reanchor(client, chain_on, version):
    _fake_registered(version, chain_on)
    client.post("/api/chain/retry-pending")
    client.post(f"/api/dev/tamper/{version['id']}")
    assert _verify(client, "dataset_version", version["id"])["verdict"] == "TAMPERED"


def test_startup_resync_is_silent_when_chain_disabled_or_down(client, version):
    before = len(client.get("/api/audit/events?limit=1000").json())
    chain.resync_on_startup(client.app.state.settings)  # CHAIN_ENABLED=false in tests
    _set(client, chain_enabled=True, chain_rpc_url="http://127.0.0.1:1")
    chain.resync_on_startup(client.app.state.settings)  # node unreachable
    assert len(client.get("/api/audit/events?limit=1000").json()) == before


# --- public-network mode (Sepolia-style: address + bundled ABI + private-key signing) --------------------------

def test_rpc_url_api_keys_are_redacted():
    s = dataclasses.replace(chain_settings_for_redaction(), chain_rpc_url="https://eth-sepolia.g.alchemy.com/v2/SECRETKEY123")
    assert chain.rpc_label(s.chain_rpc_url) == "https://eth-sepolia.g.alchemy.com"
    msg = chain.redact(s, "HTTPError 401 for url: https://eth-sepolia.g.alchemy.com/v2/SECRETKEY123 (also v2/SECRETKEY123)")
    assert "SECRETKEY123" not in msg and "eth-sepolia.g.alchemy.com" in msg


def chain_settings_for_redaction():
    from app.config import get_settings
    return get_settings()


def test_private_key_is_never_in_settings_repr(monkeypatch):
    from app.config import get_settings
    from eth_account import Account
    key = Account.create().key.hex()
    monkeypatch.setenv("CHAIN_PRIVATE_KEY", key)
    monkeypatch.setenv("CHAIN_CONTRACT_ADDRESS", "0x" + "11" * 20)
    s = get_settings()
    assert s.chain_private_key == key and key not in repr(s) and key.removeprefix("0x") not in repr(s)
    assert s.chain_contract_address == "0x" + "11" * 20


def test_address_mode_uses_bundled_abi(client):
    _set(client, chain_contract_address="0x" + "22" * 20)
    dep = chain._deployment(client.app.state.settings)
    names = {e.get("name") for e in dep["abi"]}
    assert dep["address"] == "0x" + "22" * 20 and {"register", "getRecord", "isRegistered", "Registered"} <= names


def test_bad_private_key_is_reported_without_echo(client):
    _set(client, chain_enabled=True, chain_private_key="not-a-key", chain_rpc_url="http://127.0.0.1:1")
    st = client.get("/api/chain/status").json()
    assert "not a valid private key" in (st["error"] or "") or st["connected"] is False
    assert "not-a-key" not in str(st)


@pytest.mark.chain
def test_chain_private_key_signing_path(client, chain_on, version):
    """Exactly the Sepolia code path, run against the local node: a fresh funded wallet signs the tx."""
    from eth_account import Account
    from web3 import Web3
    settings = client.app.state.settings
    w3 = Web3(Web3.HTTPProvider(settings.chain_rpc_url))
    wallet = Account.create()
    w3.eth.wait_for_transaction_receipt(
        w3.eth.send_transaction({"from": w3.eth.accounts[0], "to": wallet.address, "value": w3.to_wei(1, "ether")}))
    _set(client, chain_private_key=wallet.key.hex(), chain_contract_address=chain_on["contract_address"])

    st = client.get("/api/chain/status").json()
    assert st["contract_deployed"] and st["signer"] == wallet.address
    rec = _register(client, "dataset_version", version["id"]).json()
    assert rec["status"] == "registered" and rec["tx_hash"].startswith("0x")
    v = _verify(client, "dataset_version", version["id"])
    assert v["verdict"] == "VERIFIED" and v["on_chain"]["registrant"] == wallet.address
