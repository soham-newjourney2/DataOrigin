import pandas as pd
import pytest

from app.services import ml

CLEAN_OPS = [{"op": "drop_duplicates"}, {"op": "fill_missing", "params": {"column": "age", "strategy": "median"}}]


@pytest.fixture
def v2(client, upload, sample_csv) -> dict:
    v1 = upload("s", "sample.csv", sample_csv).json()["versions"][0]
    return client.post(f"/api/versions/{v1['id']}/transform", json={"ops": CLEAN_OPS}).json()


def _exp(client, version_id, **overrides):
    body = {"version_id": version_id, "name": "rf", "task": "classification",
            "algorithm": "random_forest_classifier", "target_column": "purchased", **overrides}
    return client.post("/api/experiments", json=body)


def test_classification_experiment(client, v2):
    r = _exp(client, v2["id"], hyperparams={"n_estimators": 50})
    assert r.status_code == 201, r.json()
    e = r.json()
    assert e["status"] == "completed" and e["version_id"] == v2["id"] and e["seed"] == 42
    assert set(e["metrics_json"]) == {"accuracy", "f1_macro", "precision_macro", "recall_macro"}
    assert 0 <= e["metrics_json"]["accuracy"] <= 1
    assert e["hyperparams_json"] == {"n_estimators": 50, "max_depth": None, "min_samples_split": 2,
                                     "min_samples_leaf": 1}
    assert {"python", "sklearn", "pandas", "numpy", "platform", "git_commit"} <= set(e["env_json"])
    assert len(e["model_sha256"]) == 64 and e["duration_ms"] >= 0
    assert e["commitment_sha256"] == ml.commitment_of(
        v2["sha256"], v2["id"], "classification", "random_forest_classifier", "purchased",
        e["hyperparams_json"], 42, 0.2, e["metrics_json"], e["env_json"])
    assert "model_path" not in e
    assert client.get(f"/api/experiments/{e['id']}").json() == e
    assert [x["id"] for x in client.get(f"/api/experiments?version_id={v2['id']}").json()] == [e["id"]]
    assert client.get("/api/experiments?version_id=999").json() == []
    assert client.get("/api/audit/events?entity_type=experiment").json()[0]["event_type"] == "experiment.created"


def test_reproduce_and_compare(client, v2):
    e1 = _exp(client, v2["id"]).json()
    r = client.post(f"/api/experiments/{e1['id']}/reproduce")
    assert r.status_code == 201, r.json()
    rep = r.json()
    assert rep["match"] is True and rep["dataset_hash_match"] is True and rep["original_id"] == e1["id"]
    assert rep["experiment"]["reproduced_from_id"] == e1["id"] and rep["experiment"]["id"] == rep["new_experiment_id"]
    assert all(d["diff"] == 0 for d in rep["metric_diffs"].values())
    assert rep["experiment"]["commitment_sha256"] == e1["commitment_sha256"]

    e3 = _exp(client, v2["id"], seed=7).json()
    cmp = client.get(f"/api/experiments/compare?ids={e1['id']},{rep['new_experiment_id']},{e3['id']}").json()
    fields = {f["field"]: f for f in cmp["fields"]}
    assert fields["seed"]["differs"] is True and fields["seed"]["values"] == [42, 42, 7]
    assert fields["algorithm"]["differs"] is False and fields["hyperparams.n_estimators"]["differs"] is False
    assert "metrics.accuracy" in fields and "env.python" in fields
    two = client.get(f"/api/experiments/compare?ids={e1['id']},{rep['new_experiment_id']}").json()
    assert {f["field"]: f["differs"] for f in two["fields"]}["seed"] is False

    assert client.get(f"/api/experiments/compare?ids={e1['id']}").status_code == 422
    assert client.get("/api/experiments/compare?ids=1,x").status_code == 422
    assert client.get(f"/api/experiments/compare?ids={e1['id']},999").status_code == 404


def test_reproduce_detects_tampered_file(client, v2):
    e1 = _exp(client, v2["id"], algorithm="logistic_regression").json()
    client.post(f"/api/dev/tamper/{v2['id']}")
    rep = client.post(f"/api/experiments/{e1['id']}/reproduce").json()
    assert rep["dataset_hash_match"] is False
    assert _exp(client, v2["id"]).status_code == 409


def test_regression_experiment(client, v2):
    for algorithm in ("linear_regression", "random_forest_regressor"):
        r = _exp(client, v2["id"], task="regression", algorithm=algorithm, target_column="income")
        assert r.status_code == 201, r.json()
        assert set(r.json()["metrics_json"]) == {"rmse", "mae", "r2"}


@pytest.mark.parametrize("overrides", [
    {"target_column": "nope"},
    {"target_column": "income"},  # continuous target for classification
    {"task": "regression", "algorithm": "linear_regression", "target_column": "city"},
    {"algorithm": "svm"},
    {"algorithm": "linear_regression"},  # task mismatch
    {"task": "clustering"},
    {"hyperparams": {"n_estimators": 0}},
    {"hyperparams": {"n_estimators": True}},
    {"hyperparams": {"kernel": "rbf"}},
    {"test_size": 1.5},
    {"seed": -1},
    {"name": ""},
])
def test_bad_requests_are_422(client, v2, overrides):
    r = _exp(client, v2["id"], **overrides)
    assert r.status_code == 422, r.json()
    assert client.get("/api/experiments").json() == []


def test_training_crash_is_saved_as_failed(client, v2, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("kaboom")
    monkeypatch.setattr(ml, "build_pipeline", boom)
    r = _exp(client, v2["id"])
    assert r.status_code == 400 and "kaboom" in r.json()["detail"]
    e = r.json()["experiment"]
    assert e["status"] == "failed" and "RuntimeError: kaboom" in e["error"] and e["metrics_json"] is None
    assert client.get(f"/api/experiments/{e['id']}").json()["status"] == "failed"
    assert client.post(f"/api/experiments/{e['id']}/reproduce").status_code == 409
    assert _exp(client, 999).status_code == 404


def test_lineage_shows_experiment(client, v2):
    e = _exp(client, v2["id"]).json()
    graph = client.get(f"/api/datasets/{v2['dataset_id']}/lineage").json()
    node = next(n for n in graph["nodes"] if n["id"] == f"e{e['id']}")
    assert node["main_metric"] == {"name": "accuracy", "value": e["metrics_json"]["accuracy"]}
    assert {"source": f"v{v2['id']}", "target": f"e{e['id']}", "type": "trained_on", "label": None} in graph["edges"]
    impact = client.get(f"/api/versions/{v2['parent_version_id']}/impact").json()
    assert [x["id"] for x in impact["experiments"]] == [e["id"]]


def test_meta_and_unit_helpers():
    meta = {a["name"]: a for a in ml.algorithms_meta()}
    assert meta["logistic_regression"]["task"] == "classification" and meta["linear_regression"]["defaults"]
    df = pd.DataFrame({"x": range(20), "c": ["a", "b"] * 10, "y": [0, 1] * 10})
    X, y = ml.prepare_xy(df, "classification", "y")
    assert list(X.columns) == ["x", "c"] and y.nunique() == 2
    match, diffs = ml.metric_diffs({"a": 0.5}, {"a": 0.5 + 1e-12})
    assert match and diffs["a"]["diff"] < 1e-9
    assert ml.metric_diffs({"a": 0.5}, {"a": 0.6})[0] is False
