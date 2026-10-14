"""Seed a demo story: synthetic student-performance data with planted issues →
v1 (raw) → v2 (cleaned) → experiment → reproduce → register on chain.

Also writes demo_data/student_performance_teammate.csv: a "teammate overwrote the dataset" copy for the live demo
(upload it as a new raw version, then show drift + lineage).

Usage (from backend/):  .venv/Scripts/python scripts/seed_demo.py [--name NAME] [--rows N]
Runs in-process against the configured DATABASE_URL / STORAGE_DIR (backend/.env); the server need not be running.
"""
import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

DEFAULT_NAME = "student-performance (demo)"
DEMO_DIR = BACKEND_DIR.parent / "demo_data"
SEED = 7


def make_dataset(rows: int = 400, seed: int = SEED) -> pd.DataFrame:
    """Synthetic student-performance table. Planted issues: missing values, duplicate rows, impossible values
    (outliers) and a constant column."""
    rng = np.random.default_rng(seed)
    study = rng.gamma(4.0, 2.0, rows).round(1)                     # weekly study hours
    attendance = np.clip(rng.normal(86, 8, rows), 40, 100).round(1)
    sleep = np.clip(rng.normal(7.0, 1.0, rows), 4, 10).round(1)
    prior_gpa = np.clip(rng.normal(2.9, 0.5, rows), 1.0, 4.0).round(2)
    parent_edu = rng.choice(["high_school", "bachelor", "master", "phd"], rows, p=[0.4, 0.35, 0.2, 0.05])
    internet = rng.choice(["yes", "no"], rows, p=[0.85, 0.15])
    edu_bonus = pd.Series(parent_edu).map({"high_school": 0, "bachelor": 2, "master": 3, "phd": 4}).to_numpy()
    final = (-13 + 2.2 * study + 0.35 * attendance + 9 * prior_gpa + 1.5 * (sleep - 7) + edu_bonus
             + 3 * (internet == "yes") + rng.normal(0, 7, rows))
    final = np.clip(final, 0, 100).round(1)
    df = pd.DataFrame({
        "age": rng.integers(16, 23, rows), "gender": rng.choice(["F", "M"], rows),
        "study_hours": study, "attendance_pct": attendance, "sleep_hours": sleep, "prior_gpa": prior_gpa,
        "parental_education": parent_edu, "internet_access": internet, "school": "Lincoln High",
        "passed": (final >= 60).astype(int),  # the latent score itself is not published (would leak the label)
    })
    # Planted issues
    df.loc[rng.choice(rows, int(rows * 0.06), replace=False), "study_hours"] = np.nan
    df.loc[rng.choice(rows, int(rows * 0.03), replace=False), "parental_education"] = np.nan
    df.loc[rng.choice(rows, 4, replace=False), "attendance_pct"] = [180.0, 250.0, 999.0, -20.0]  # data-entry errors
    dups = df.iloc[rng.choice(rows, 12, replace=False)]
    return pd.concat([df, dups], ignore_index=True)


def make_teammate_copy(df: pd.DataFrame, seed: int = SEED) -> pd.DataFrame:
    """What a teammate's 'quick fix' overwrite looks like: attendance rescaled to 0–1, a column renamed,
    rows silently dropped and a new category value introduced."""
    rng = np.random.default_rng(seed + 1)
    out = df.drop_duplicates().dropna(subset=["study_hours"]).copy()
    out["attendance_pct"] = (out["attendance_pct"] / 100).round(3)
    out = out.rename(columns={"sleep_hours": "sleep"})
    out = out.drop(index=out.sample(frac=0.15, random_state=int(rng.integers(1_000_000))).index)
    out.loc[out.sample(frac=0.1, random_state=1).index, "parental_education"] = "unknown"
    return out.reset_index(drop=True)


CLEAN_OPS = [
    {"op": "drop_duplicates", "params": {}},
    {"op": "drop_columns", "params": {"columns": ["school"]}},
    {"op": "filter_rows", "params": {"column": "attendance_pct", "op": "<=", "value": 100}},
    {"op": "filter_rows", "params": {"column": "attendance_pct", "op": ">=", "value": 0}},
    {"op": "fill_missing", "params": {"column": "study_hours", "strategy": "median"}},
    {"op": "fill_missing", "params": {"column": "parental_education", "strategy": "mode"}},
]


def _ok(r, what: str) -> dict:
    if r.status_code >= 400:
        raise SystemExit(f"{what} failed ({r.status_code}): {r.text}")
    return r.json()


def seed(client, name: str = DEFAULT_NAME, rows: int = 400, out_dir: Path = DEMO_DIR, log=print) -> dict:
    """Build the story through the API. Returns ids. Raises SystemExit if `name` already exists."""
    if any(d["name"] == name for d in client.get("/api/datasets").json()):
        raise SystemExit(f"Dataset '{name}' already exists. Use --name to seed another copy.")
    df = make_dataset(rows)
    raw = df.to_csv(index=False).encode()

    ds = _ok(client.post("/api/datasets", data={
        "name": name, "description": "Synthetic student-performance data (numpy, seeded) with planted quality issues.",
        "source": "scripts/seed_demo.py"}, files={"file": ("student_performance.csv", raw)}), "upload")
    v1 = ds["versions"][0]
    q1 = _ok(client.get(f"/api/versions/{v1['id']}/quality"), "quality v1")
    log(f"v1  {v1['row_count']} rows  quality {q1['score']}  findings {len(q1['report_json']['findings'])}")

    v2 = _ok(client.post(f"/api/versions/{v1['id']}/transform", json={
        "ops": CLEAN_OPS, "note": "clean: dedupe, drop constant column, remove impossible attendance, impute"}),
        "transform")
    log(f"v2  {v2['row_count']} rows  quality {v2['quality_score']}")

    exp = _ok(client.post("/api/experiments", json={
        "version_id": v2["id"], "name": "pass/fail random forest", "task": "classification",
        "algorithm": "random_forest_classifier", "target_column": "passed",
        "hyperparams": {"n_estimators": 200, "max_depth": 6}, "seed": 42, "test_size": 0.25}), "experiment")
    log(f"exp #{exp['id']}  accuracy {exp['metrics_json']['accuracy']}  f1 {exp['metrics_json']['f1_macro']}")
    rep = _ok(client.post(f"/api/experiments/{exp['id']}/reproduce"), "reproduce")
    log(f"reproduce → match={rep['match']}  (run #{rep['new_experiment_id']})")

    chain = {}
    for artifact_type, artifact_id in (("dataset_version", v2["id"]), ("experiment", exp["id"])):
        rec = _ok(client.post("/api/chain/register", json={"artifact_type": artifact_type, "artifact_id": artifact_id}),
                  "register")
        chain[artifact_type] = rec["status"]
        log(f"chain {artifact_type} #{artifact_id}: {rec['status']}" + (f"  ({rec['error']})" if rec["error"] else ""))
    if "pending" in chain.values():
        log("  → chain unavailable: records are pending. Start the node + deploy, then click 'Retry pending' "
            "on the Verification page.")

    score = _ok(client.get(f"/api/reports/experiment/{exp['id']}"), "report")["reproducibility"]["score"]
    log(f"reproducibility score {score}/100")

    out_dir.mkdir(parents=True, exist_ok=True)
    teammate = out_dir / "student_performance_teammate.csv"
    make_teammate_copy(df).to_csv(teammate, index=False)
    log(f"demo file for the live 'teammate overwrote it' step: {teammate}")
    return {"dataset_id": ds["id"], "v1": v1["id"], "v2": v2["id"], "experiment": exp["id"],
            "reproduction": rep["new_experiment_id"], "chain": chain, "score": score, "teammate_csv": str(teammate)}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--name", default=DEFAULT_NAME)
    parser.add_argument("--rows", type=int, default=400)
    args = parser.parse_args(argv)

    import warnings
    warnings.filterwarnings("ignore", message=".*httpx.*")
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app()) as client:
        ids = seed(client, args.name, args.rows)
    print(f"\nDone. Open http://localhost:8000/#/lineage/{ids['dataset_id']} "
          f"or #/experiments/{ids['experiment']} (start the backend if it isn't running).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
