import io

import pandas as pd

from app.services import drift, quality
from tests.conftest import DATA_DIR


def _csv(df: pd.DataFrame) -> bytes:
    return df.to_csv(index=False).encode()


def _clean_df() -> pd.DataFrame:
    n = 30
    return pd.DataFrame({"x": range(n), "y": [i * 2.5 for i in range(n)], "cat": ["a", "b", "c"] * (n // 3)})


def test_analyze_sample():
    report = quality.analyze(pd.read_csv(DATA_DIR / "sample.csv"))
    s = report["summary"]
    assert s["missing_total"] == 3 and s["duplicate_rows"] == 2
    income = next(c for c in report["columns"] if c["name"] == "income")
    assert income["outliers"]["count"] >= 1 and 5 in income["outliers"]["sample_indices"]
    assert report["score"] < 100 and report["findings"]
    assert {f["check"] for f in report["findings"]} >= {"missing", "duplicates", "outliers"}
    assert all(f["severity"] in {"info", "warning", "critical"} for f in report["findings"])
    assert report["anomalies"]["ran"] and len(report["anomalies"]["top_indices"]) == 5


def test_analyze_clean_and_constant():
    assert quality.analyze(_clean_df())["score"] >= 95
    df = _clean_df().assign(k=1)
    report = quality.analyze(df)
    assert report["summary"]["constant_columns"] == ["k"] and report["score"] == 95.0
    small = quality.analyze(_clean_df().head(10))
    assert small["anomalies"]["ran"] is False


def test_score_formula_caps():
    assert quality.score_of(0, 0, 0, 0) == 100.0
    assert quality.score_of(100, 100, 100, 5) == 0.0
    assert quality.score_of(1.43, 6.67, 3.33, 0) == round(100 - 2.86 - 20.01 - 6.66, 1)


def test_drift_self_and_shifted():
    df = pd.read_csv(DATA_DIR / "sample.csv")
    same = drift.compare(df, df)
    assert all(c["status"] == "none" for c in same["columns"]) and same["summary"]["overall"] == "none"
    shifted = df.assign(hours_per_week=df["hours_per_week"] * 3)
    cols = {c["name"]: c for c in drift.compare(df, shifted)["columns"]}
    assert cols["hours_per_week"]["status"] == "major"
    assert cols["age"]["status"] == "none"


def test_drift_schema_and_categories():
    a = _clean_df()
    b = a.drop(columns=["y"]).assign(z=1, cat=["a", "b", "d"] * 10)
    result = drift.compare(a, b)
    assert result["schema"] == {"added": ["z"], "removed": ["y"], "changed": True}
    cat = next(c for c in result["columns"] if c["name"] == "cat")
    assert cat["kind"] == "categorical" and cat["new_categories"] == ["d"] and cat["missing_categories"] == ["c"]
    assert cat["status"] != "none"


def test_upload_creates_quality_report(client, upload, sample_csv):
    ds = upload("q", "sample.csv", sample_csv).json()
    vid = ds["versions"][0]["id"]
    r = client.get(f"/api/versions/{vid}/quality")
    assert r.status_code == 200
    body = r.json()
    assert body["version_id"] == vid and body["score"] < 100
    assert body["report_json"]["summary"]["missing_total"] == 3

    rerun = client.post(f"/api/versions/{vid}/quality")
    assert rerun.status_code == 201 and rerun.json()["id"] > body["id"]
    assert rerun.json()["score"] == body["score"]
    assert client.get(f"/api/versions/{vid}/quality").json()["id"] == rerun.json()["id"]
    events = client.get("/api/audit/events").json()
    assert events[0]["event_type"] == "quality.analyzed"
    assert client.get("/api/audit/verify").json()["valid"] is True


def test_new_version_gets_report_and_404s(client, upload):
    ds = upload("c", "clean.csv", _csv(_clean_df())).json()
    v2 = client.post(f"/api/datasets/{ds['id']}/versions", files={"file": ("c.csv", _csv(_clean_df().head(29)))})
    assert v2.status_code == 201
    assert client.get(f"/api/versions/{v2.json()['id']}/quality").json()["score"] >= 95
    assert client.get("/api/versions/999/quality").status_code == 404
    assert client.post("/api/versions/999/quality").status_code == 404


def test_drift_endpoint(client, upload, sample_csv):
    ds = upload("d", "sample.csv", sample_csv).json()
    v1 = ds["versions"][0]["id"]
    df = pd.read_csv(io.BytesIO(sample_csv))
    v2 = client.post(f"/api/datasets/{ds['id']}/versions",
                     files={"file": ("s.csv", _csv(df.assign(income=df["income"] * 3)))}).json()["id"]

    same = client.get(f"/api/versions/{v1}/drift?against={v1}").json()
    assert all(c["status"] == "none" for c in same["columns"]) and same["schema"]["changed"] is False

    r = client.get(f"/api/versions/{v2}/drift?against={v1}")
    assert r.status_code == 200
    body = r.json()
    assert body["version_id"] == v2 and body["against_version_id"] == v1
    cols = {c["name"]: c for c in body["columns"]}
    assert cols["income"]["status"] == "major" and cols["income"]["mean_b"] > cols["income"]["mean_a"]
    assert body["summary"]["overall"] == "major"

    assert client.get(f"/api/versions/{v1}/drift?against=999").status_code == 404
    assert client.get(f"/api/versions/{v1}/drift").status_code == 422
