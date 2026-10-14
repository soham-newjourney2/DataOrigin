import pandas as pd
import pytest

from app.db import SessionLocal
from app.models import Experiment, Transformation
from app.services import transforms
from tests.conftest import DATA_DIR

CLEAN_OPS = [{"op": "drop_duplicates"}, {"op": "fill_missing", "params": {"column": "age", "strategy": "median"}}]


@pytest.fixture
def v1(upload, sample_csv) -> dict:
    return upload("s", "sample.csv", sample_csv).json()["versions"][0]


def _transform(client, vid, ops, note=None):
    return client.post(f"/api/versions/{vid}/transform", json={"ops": ops, "note": note})


def _df() -> pd.DataFrame:
    return pd.read_csv(DATA_DIR / "sample.csv")


def _apply(ops):
    return transforms.apply_ops(_df(), transforms.normalize_ops(ops))


def test_ops_unit():
    assert len(_apply([{"op": "drop_duplicates"}])) == 28
    assert _apply([{"op": "drop_missing_rows"}]).isna().sum().sum() == 0
    assert len(_apply([{"op": "drop_missing_rows", "params": {"columns": ["city"]}}])) == 29
    assert _apply([{"op": "fill_missing", "params": {"column": "city", "strategy": "mode"}}])["city"].notna().all()
    filled = _apply([{"op": "fill_missing", "params": {"column": "age", "strategy": "value", "value": 0}}])
    assert (filled["age"] == 0).sum() == 1
    assert "city" not in _apply([{"op": "drop_columns", "params": {"columns": ["city"]}}]).columns
    no_out = _apply([{"op": "remove_outliers_iqr", "params": {"columns": ["income"]}}])
    assert no_out["income"].max() < 9999999 and len(no_out) == 29
    assert (_apply([{"op": "filter_rows", "params": {"column": "age", "op": ">=", "value": 40}}])["age"] >= 40).all()
    assert set(_apply([{"op": "filter_rows", "params": {"column": "city", "op": "==", "value": "Rome"}}])["city"]) \
        == {"Rome"}
    assert transforms.summarize_ops(transforms.normalize_ops(CLEAN_OPS)) == "drop_duplicates → fill_missing(age)"


@pytest.mark.parametrize("ops", [
    [{"op": "rm_rf"}],
    [],
    [{"op": "fill_missing", "params": {"column": "nope", "strategy": "mean"}}],
    [{"op": "fill_missing", "params": {"column": "city", "strategy": "mean"}}],
    [{"op": "fill_missing", "params": {"column": "age", "strategy": "value"}}],
    [{"op": "drop_columns", "params": {"columns": []}}],
    [{"op": "remove_outliers_iqr", "params": {"columns": ["city"]}}],
    [{"op": "filter_rows", "params": {"column": "age", "op": "~", "value": 1}}],
    [{"op": "filter_rows", "params": {"column": "age", "op": ">", "value": "abc"}}],
    [{"op": "filter_rows", "params": {"column": "age", "op": ">", "value": 1000}}],  # empty result
])
def test_bad_ops_are_422(client, v1, ops):
    r = _transform(client, v1["id"], ops)
    assert r.status_code == 422, r.json()
    assert len(client.get(f"/api/datasets/{v1['dataset_id']}").json()["versions"]) == 1


def test_transform_creates_v2(client, v1):
    r = _transform(client, v1["id"], CLEAN_OPS)
    assert r.status_code == 201, r.json()
    v2 = r.json()
    assert v2["parent_version_id"] == v1["id"] and v2["version_no"] == 2 and v2["file_format"] == "csv"
    assert v2["row_count"] == 28 and v2["note"] == "transform: drop_duplicates → fill_missing(age)"
    assert v2["ops"] == [{"op": "drop_duplicates", "params": {}}, CLEAN_OPS[1]]

    report = client.get(f"/api/versions/{v2['id']}/quality").json()
    assert report["score"] == v2["quality_score"]
    assert report["report_json"]["summary"]["duplicate_rows"] == 0
    age = next(c for c in report["report_json"]["columns"] if c["name"] == "age")
    assert age["missing"] == 0
    assert v2["quality_score"] > client.get(f"/api/versions/{v1['id']}/quality").json()["score"]

    with SessionLocal() as db:
        t = db.query(Transformation).filter_by(output_version_id=v2["id"]).one()
        assert t.input_version_id == v1["id"] and t.ops_json == v2["ops"]
    events = client.get("/api/audit/events?entity_type=dataset_version").json()
    assert events[0]["event_type"] == "version.created" and events[0]["payload_json"]["ops"] == v2["ops"]
    assert client.post(f"/api/versions/{v2['id']}/verify-local").json()["match"] is True

    # Same ops again → identical bytes → 409
    assert _transform(client, v1["id"], CLEAN_OPS).status_code == 409
    assert _transform(client, 999, CLEAN_OPS).status_code == 404


def test_lineage_and_impact(client, v1):
    v2 = _transform(client, v1["id"], CLEAN_OPS).json()
    graph = client.get(f"/api/datasets/{v1['dataset_id']}/lineage").json()
    assert [n["id"] for n in graph["nodes"]] == [f"v{v1['id']}", f"v{v2['id']}"]
    assert graph["edges"] == [{"source": f"v{v1['id']}", "target": f"v{v2['id']}", "type": "derived",
                               "label": "drop_duplicates → fill_missing(age)"}]
    n1 = graph["nodes"][0]
    assert n1["label"] == "v1" and n1["sha_short"] == v1["sha256"][:12] and n1["rows"] == 30
    assert n1["quality_score"] is not None and n1["chain_status"] == "unregistered"

    v3 = _transform(client, v2["id"], [{"op": "drop_columns", "params": {"columns": ["city"]}}]).json()
    with SessionLocal() as db:
        db.add(Experiment(name="rf", version_id=v3["id"], task="classification", algorithm="random_forest",
                          target_column="purchased", status="completed", metrics_json={"accuracy": 0.9}))
        db.commit()

    imp = client.get(f"/api/versions/{v1['id']}/impact").json()
    assert [(v["id"], v["depth"]) for v in imp["versions"]] == [(v2["id"], 1), (v3["id"], 2)]
    assert [(e["name"], e["depth"]) for e in imp["experiments"]] == [("rf", 3)] and imp["total"] == 3
    assert client.get(f"/api/versions/{v3['id']}/impact").json()["versions"] == []
    assert client.get("/api/versions/999/impact").status_code == 404

    graph = client.get(f"/api/datasets/{v1['dataset_id']}/lineage").json()
    exp = graph["nodes"][-1]
    assert exp["type"] == "experiment" and exp["main_metric"] == {"name": "accuracy", "value": 0.9}
    assert graph["edges"][-1]["type"] == "trained_on" and len(graph["edges"]) == 3
    assert client.get("/api/datasets/999/lineage").status_code == 404


def test_manual_upload_edge_and_meta(client, v1):
    df = _df().head(25)
    client.post(f"/api/datasets/{v1['dataset_id']}/versions", files={"file": ("x.csv", df.to_csv(index=False))})
    graph = client.get(f"/api/datasets/{v1['dataset_id']}/lineage").json()
    assert graph["edges"][0]["label"] == "manual_upload"

    meta = client.get("/api/meta/algorithms").json()
    assert {o["op"] for o in meta["transform_ops"]} == set(transforms.OPS) and len(meta["algorithms"]) == 4
