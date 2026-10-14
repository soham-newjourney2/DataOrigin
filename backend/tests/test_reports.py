import dataclasses
import io
import uuid

import pandas as pd
import pytest
from sqlalchemy import delete

from app.db import SessionLocal
from app.models import Transformation
from app.services import chain, ml, report

CLEAN_OPS = [{"op": "drop_duplicates"}, {"op": "fill_missing", "params": {"column": "age", "strategy": "median"}}]
SECTIONS = {"dataset", "transformations", "quality", "experiment", "reproductions", "verification", "audit_trail",
            "reproducibility", "report_sha256"}


@pytest.fixture
def v2(client, upload, sample_csv) -> dict:
    # uuid column → hashes are new on a persistent chain node
    data = pd.read_csv(io.BytesIO(sample_csv)).assign(run_id=uuid.uuid4().hex).to_csv(index=False).encode()
    v1 = upload("flow", "sample.csv", data, description="<script>alert(1)</script>").json()["versions"][0]
    return client.post(f"/api/versions/{v1['id']}/transform", json={"ops": CLEAN_OPS}).json()


def _exp(client, version_id, **overrides) -> dict:
    body = {"version_id": version_id, "name": "rf", "task": "classification",
            "algorithm": "random_forest_classifier", "target_column": "purchased", **overrides}
    r = client.post("/api/experiments", json=body)
    assert r.status_code in (201, 400), r.json()
    return r.json() if r.status_code == 201 else r.json()["experiment"]


def _report(client, eid) -> dict:
    r = client.get(f"/api/reports/experiment/{eid}")
    assert r.status_code == 200, r.json()
    return r.json()


def _failed_checks(rep) -> set[str]:
    return {c["key"] for c in rep["reproducibility"]["checks"] if not c["passed"]}


def test_full_flow_report(client, v2):
    e = _exp(client, v2["id"])
    assert client.post(f"/api/experiments/{e['id']}/reproduce").json()["match"] is True

    client.app.state.settings = dataclasses.replace(client.app.state.settings, chain_enabled=True)
    on_chain = chain.status(client.app.state.settings)["contract_deployed"]
    if on_chain:
        for t, i in (("dataset_version", v2["id"]), ("experiment", e["id"])):
            assert client.post("/api/chain/register", json={"artifact_type": t, "artifact_id": i}).json()["status"] \
                == "registered"
    else:
        client.app.state.settings = dataclasses.replace(client.app.state.settings, chain_enabled=False)

    rep = _report(client, e["id"])
    assert SECTIONS <= set(rep) and len(rep["report_sha256"]) == 64
    assert [v["version_no"] for v in rep["dataset"]["ancestry"]] == [1, 2] and rep["dataset"]["lineage_complete"]
    assert rep["transformations"][0]["summary"] == "drop_duplicates → fill_missing(age)"
    assert rep["quality"]["version_id"] == v2["id"] and rep["quality"]["score"] >= 70
    assert rep["experiment"]["config"]["seed"] == 42 and rep["experiment"]["metrics"] == e["metrics_json"]
    assert rep["reproductions"][0]["match"] is True and rep["reproductions"][0]["relation"] == "reproduction"
    assert rep["audit_trail"]["chain_verify"]["valid"] is True
    types = {ev["event_type"] for ev in rep["audit_trail"]["events"]}
    assert {"dataset.created", "version.created", "experiment.created", "experiment.reproduced"} <= types

    if on_chain:
        assert rep["verification"]["dataset_version"]["verdict"] == "VERIFIED"
        assert rep["verification"]["experiment"]["verdict"] == "VERIFIED"
        assert rep["reproducibility"]["score"] == 100 and rep["reproducibility"]["warnings"] == []
    else:
        assert rep["verification"]["dataset_version"]["verdict"] == "NOT_REGISTERED"
        assert rep["reproducibility"]["score"] == 90 and _failed_checks(rep) == {"on_chain"}

    # Reproduction's own report counts the match with its original
    repro = _report(client, rep["reproductions"][0]["id"])
    assert repro["reproductions"][0]["relation"] == "original" and "reproduced" not in _failed_checks(repro)


def test_fresh_run_warnings_and_lower_score(client, v2):
    e = _exp(client, v2["id"])
    rep = _report(client, e["id"])
    assert _failed_checks(rep) == {"reproduced", "on_chain"}
    assert rep["reproducibility"]["score"] == 75 and len(rep["reproducibility"]["warnings"]) == 2
    assert any("not been successfully reproduced" in w for w in rep["reproducibility"]["warnings"])
    assert rep["reproductions"] == [] and rep["verification"]["experiment"]["verdict"] == "NOT_REGISTERED"


def test_report_is_read_only_and_hash_stable(client, v2):
    e = _exp(client, v2["id"])
    before = len(client.get("/api/audit/events?limit=1000").json())
    a, b = _report(client, e["id"]), _report(client, e["id"])
    assert a["report_sha256"] == b["report_sha256"]
    assert len(client.get("/api/audit/events?limit=1000").json()) == before


def test_html_report(client, v2):
    e = _exp(client, v2["id"])
    r = client.get(f"/api/reports/experiment/{e['id']}?format=html")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    body = r.text
    assert body.startswith("<!doctype html>") and "<style>" in body and "Reproducibility score" in body
    for heading in ("Dataset", "Transformations", "Quality", "Experiment", "Reproductions", "Verification",
                    "Audit trail"):
        assert f"<h2>{heading}</h2>" in body
    assert "<script>" not in body and "&lt;script&gt;" in body
    assert "<link" not in body and "src=" not in body  # self-contained
    assert client.get(f"/api/reports/experiment/{e['id']}?format=xml").status_code == 422
    assert client.get("/api/reports/experiment/999").status_code == 404


def test_tampered_and_incomplete_lineage(client, v2):
    e = _exp(client, v2["id"])
    with SessionLocal() as db:
        db.execute(delete(Transformation).where(Transformation.output_version_id == v2["id"]))
        db.commit()
    client.post(f"/api/dev/tamper/{v2['id']}")
    rep = _report(client, e["id"])
    assert rep["verification"]["dataset_version"]["verdict"] == "TAMPERED"
    assert {"dataset_hash", "lineage"} <= _failed_checks(rep) and not rep["dataset"]["lineage_complete"]
    assert rep["reproducibility"]["score"] == 75 - 20 - 10
    assert client.get(f"/api/reports/experiment/{e['id']}?format=html").status_code == 200


def test_failed_experiment_report(client, v2, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("kaboom")
    monkeypatch.setattr(ml, "build_pipeline", boom)
    e = _exp(client, v2["id"])
    assert e["status"] == "failed"
    rep = _report(client, e["id"])
    assert rep["verification"]["experiment"] is None and rep["experiment"]["error"].startswith("RuntimeError")
    assert "env" in _failed_checks(rep)
    assert "not applicable" in client.get(f"/api/reports/experiment/{e['id']}?format=html").text


def test_score_checks_unit():
    all_pass = report.score_checks({k: True for k, *_ in report.CHECKS})
    assert all_pass["score"] == 100 and all_pass["warnings"] == []
    none = report.score_checks({})
    assert none["score"] == 0 and len(none["warnings"]) == len(report.CHECKS) == 8
