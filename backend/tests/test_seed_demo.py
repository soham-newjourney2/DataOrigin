import pandas as pd
import pytest

from scripts import seed_demo


def test_dataset_is_deterministic_with_planted_issues():
    a, b = seed_demo.make_dataset(), seed_demo.make_dataset()
    pd.testing.assert_frame_equal(a, b)
    assert a.duplicated().sum() >= 10 and a["study_hours"].isna().sum() > 0
    assert (a["attendance_pct"] > 100).any() and a["school"].nunique() == 1
    assert 0.3 < a["passed"].mean() < 0.8 and "final_score" not in a.columns  # no label leakage


def test_seed_story(client, tmp_path):
    ids = seed_demo.seed(client, rows=200, out_dir=tmp_path, log=lambda *_: None)
    assert ids["chain"] == {"dataset_version": "pending", "experiment": "pending"}  # chain disabled in tests
    assert ids["score"] == 90

    lineage = client.get(f"/api/datasets/{ids['dataset_id']}/lineage").json()
    assert {n["type"] for n in lineage["nodes"]} == {"version", "experiment"}
    assert {e["type"] for e in lineage["edges"]} == {"derived", "trained_on"}
    q1 = client.get(f"/api/versions/{ids['v1']}/quality").json()["score"]
    q2 = client.get(f"/api/versions/{ids['v2']}/quality").json()["score"]
    assert q2 > q1
    exp = client.get(f"/api/experiments/{ids['experiment']}").json()
    assert exp["status"] == "completed" and exp["metrics_json"]["accuracy"] < 1.0
    rep = client.get(f"/api/experiments?version_id={ids['v2']}").json()
    assert any(r["reproduced_from_id"] == ids["experiment"] for r in rep)

    with pytest.raises(SystemExit):
        seed_demo.seed(client, rows=200, out_dir=tmp_path, log=lambda *_: None)

    # Live demo step: the teammate's overwrite uploaded as a raw version shows drift + schema change
    teammate = (tmp_path / "student_performance_teammate.csv").read_bytes()
    v3 = client.post(f"/api/datasets/{ids['dataset_id']}/versions", files={"file": ("t.csv", teammate)},
                     data={"note": "teammate's copy"}).json()
    drift = client.get(f"/api/versions/{v3['id']}/drift?against={ids['v2']}").json()
    assert set(drift["schema"]["added"]) == {"school", "sleep"}  # v2 dropped `school`; teammate renamed sleep_hours
    assert drift["schema"]["removed"] == ["sleep_hours"]
    cols = {c["name"]: c for c in drift["columns"]}
    assert cols["attendance_pct"]["status"] == "major" and "unknown" in cols["parental_education"]["new_categories"]
    impact = client.get(f"/api/versions/{ids['v1']}/impact").json()
    assert v3["id"] in [v["id"] for v in impact["versions"]]
