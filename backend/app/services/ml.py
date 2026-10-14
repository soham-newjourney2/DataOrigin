"""Predefined scikit-learn experiments: validation, training, metrics, env capture, model + commitment hashes."""
import math
import platform
import subprocess
import time
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LinearRegression, LogisticRegression
from sklearn.metrics import (accuracy_score, f1_score, mean_absolute_error, mean_squared_error, precision_score,
                             r2_score, recall_score)
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler
from sqlalchemy.orm import Session

from app.config import BACKEND_DIR
from app.models import DatasetVersion, Experiment
from app.services import audit, storage
from app.services.hashing import canonical, sha256_file, sha256_text
from app.services.versions import load_version_df


class ExperimentError(Exception):
    """Invalid experiment request (→ 422)."""


# Param spec: (kind, minimum). kinds: int, float (> minimum), int_or_none, bool.
_FOREST = {"n_estimators": ("int", 1), "max_depth": ("int_or_none", 1),
           "min_samples_split": ("int", 2), "min_samples_leaf": ("int", 1)}
_FOREST_DEFAULTS = {"n_estimators": 100, "max_depth": None, "min_samples_split": 2, "min_samples_leaf": 1}

ALGORITHMS = {
    "logistic_regression": {"task": "classification", "estimator": LogisticRegression,
                            "defaults": {"C": 1.0, "max_iter": 1000},
                            "params": {"C": ("float", 0), "max_iter": ("int", 1)}},
    "random_forest_classifier": {"task": "classification", "estimator": RandomForestClassifier,
                                 "defaults": _FOREST_DEFAULTS, "params": _FOREST},
    "linear_regression": {"task": "regression", "estimator": LinearRegression,
                          "defaults": {"fit_intercept": True}, "params": {"fit_intercept": ("bool", None)}},
    "random_forest_regressor": {"task": "regression", "estimator": RandomForestRegressor,
                                "defaults": _FOREST_DEFAULTS, "params": _FOREST},
}
TASKS = ("classification", "regression")
MAX_N_ESTIMATORS = 1000


def algorithms_meta() -> list[dict]:
    return [{"name": name, "task": a["task"], "defaults": a["defaults"],
             "params": {k: kind for k, (kind, _) in a["params"].items()}} for name, a in ALGORITHMS.items()]


def _check_param(key: str, value, kind: str, minimum) -> None:
    is_int = isinstance(value, int) and not isinstance(value, bool)
    if kind == "bool":
        ok = isinstance(value, bool)
    elif kind == "float":
        ok = (is_int or isinstance(value, float)) and math.isfinite(value) and value > minimum
    elif kind == "int_or_none":
        ok = value is None or (is_int and value >= minimum)
    else:
        ok = is_int and value >= minimum
    if ok and key == "n_estimators":
        ok = value <= MAX_N_ESTIMATORS
    if not ok:
        bound = f" ≥ {minimum}" if kind in ("int", "int_or_none") else f" > {minimum}" if kind == "float" else ""
        raise ExperimentError(f"hyperparam '{key}' must be {kind}{bound}")


def resolve_config(task: str, algorithm: str, hyperparams: dict | None, seed, test_size) -> dict:
    """Validate the request and return the effective hyperparams (defaults + overrides)."""
    if task not in TASKS:
        raise ExperimentError(f"task must be one of {list(TASKS)}")
    algo = ALGORITHMS.get(algorithm)
    if algo is None:
        raise ExperimentError(f"unknown algorithm '{algorithm}'; allowed: {list(ALGORITHMS)}")
    if algo["task"] != task:
        raise ExperimentError(f"algorithm '{algorithm}' is for {algo['task']}, not {task}")
    hyperparams = hyperparams or {}
    unknown = sorted(set(hyperparams) - set(algo["params"]))
    if unknown:
        raise ExperimentError(f"unknown hyperparams {unknown}; allowed: {list(algo['params'])}")
    for key, value in hyperparams.items():
        _check_param(key, value, *algo["params"][key])
    if not isinstance(seed, int) or isinstance(seed, bool) or not 0 <= seed < 2**32:
        raise ExperimentError("seed must be an integer in [0, 2^32)")
    if not 0 < test_size < 1:
        raise ExperimentError("test_size must be between 0 and 1 (exclusive)")
    return {**algo["defaults"], **hyperparams}


def prepare_xy(df: pd.DataFrame, task: str, target: str) -> tuple[pd.DataFrame, pd.Series]:
    """Split features/target; rows with a missing target are dropped. Raises ExperimentError if invalid."""
    if target not in df.columns:
        raise ExperimentError(f"target column '{target}' not found")
    if df.shape[1] < 2:
        raise ExperimentError("dataset has no feature columns besides the target")
    df = df[df[target].notna()]
    y, X = df[target], df.drop(columns=[target])
    if len(df) < 10:
        raise ExperimentError(f"target '{target}' has fewer than 10 non-missing rows")
    numeric_y = pd.api.types.is_numeric_dtype(y) and not pd.api.types.is_bool_dtype(y)
    if task == "regression":
        if not numeric_y:
            raise ExperimentError(f"regression target '{target}' must be numeric")
        if y.nunique() < 2:
            raise ExperimentError(f"regression target '{target}' is constant")
    else:
        if numeric_y and not np.all(np.mod(y.to_numpy(dtype=float), 1) == 0):
            raise ExperimentError(f"classification target '{target}' has continuous values; use regression")
        if y.nunique() < 2:
            raise ExperimentError(f"classification target '{target}' needs at least 2 classes")
        y = y.astype(str) if not numeric_y else y.astype(int)
    return X.reset_index(drop=True), y.reset_index(drop=True)


def build_pipeline(X: pd.DataFrame, algorithm: str, hyperparams: dict, seed: int) -> Pipeline:
    numeric = [c for c in X.columns if pd.api.types.is_numeric_dtype(X[c]) and not pd.api.types.is_bool_dtype(X[c])]
    categorical = [c for c in X.columns if c not in numeric]
    pre = ColumnTransformer([
        ("num", Pipeline([("impute", SimpleImputer(strategy="median")), ("scale", StandardScaler())]), numeric),
        ("cat", Pipeline([("impute", SimpleImputer(strategy="most_frequent")),
                          ("onehot", OneHotEncoder(handle_unknown="ignore"))]), categorical),
    ])
    estimator = ALGORITHMS[algorithm]["estimator"]
    model = estimator(**hyperparams)
    if "random_state" in model.get_params():
        model.set_params(random_state=seed)
    return Pipeline([("pre", pre), ("model", model)])


def _as_categorical(X: pd.DataFrame) -> pd.DataFrame:
    """Cast non-numeric columns to str (keeping NaN) so mixed types don't break the encoder."""
    X = X.copy()
    for c in X.columns:
        if not pd.api.types.is_numeric_dtype(X[c]) or pd.api.types.is_bool_dtype(X[c]):
            X[c] = X[c].map(lambda v: v if pd.isna(v) else str(v)).astype(object)
    return X


def _can_stratify(y: pd.Series, test_size: float) -> bool:
    counts = y.value_counts()
    n_test = math.ceil(test_size * len(y))
    return counts.min() >= 2 and len(counts) <= n_test and len(counts) <= len(y) - n_test


def metrics_of(task: str, y_true, y_pred) -> dict:
    if task == "classification":
        m = {"accuracy": accuracy_score(y_true, y_pred),
             "f1_macro": f1_score(y_true, y_pred, average="macro", zero_division=0),
             "precision_macro": precision_score(y_true, y_pred, average="macro", zero_division=0),
             "recall_macro": recall_score(y_true, y_pred, average="macro", zero_division=0)}
    else:
        m = {"rmse": math.sqrt(mean_squared_error(y_true, y_pred)),
             "mae": mean_absolute_error(y_true, y_pred), "r2": r2_score(y_true, y_pred)}
    return {k: round(float(v), 6) for k, v in m.items()}


def git_commit() -> str | None:
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=BACKEND_DIR, capture_output=True, text=True,
                             timeout=5)
        return out.stdout.strip() or None if out.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def env_info() -> dict:
    return {"python": platform.python_version(), "sklearn": sklearn.__version__, "pandas": pd.__version__,
            "numpy": np.__version__, "platform": platform.platform(), "git_commit": git_commit()}


def commitment_of(dataset_sha256: str, version_id: int, task: str, algorithm: str, target_column: str,
                  hyperparams: dict, seed: int, test_size: float, metrics: dict, env: dict) -> str:
    return sha256_text(canonical({
        "dataset_sha256": dataset_sha256, "version_id": version_id, "task": task, "algorithm": algorithm,
        "target_column": target_column, "hyperparams": hyperparams, "seed": seed, "test_size": test_size,
        "metrics": metrics, "env": env,
    }))


def train(X: pd.DataFrame, y: pd.Series, task: str, algorithm: str, hyperparams: dict, seed: int,
          test_size: float, model_file) -> dict:
    """Fit, score and save the model to `model_file` (must not exist). Returns metrics, model sha, duration."""
    start = time.perf_counter()
    stratify = y if task == "classification" and _can_stratify(y, test_size) else None
    X_train, X_test, y_train, y_test = train_test_split(
        _as_categorical(X), y, test_size=test_size, random_state=seed, stratify=stratify)
    pipeline = build_pipeline(X, algorithm, hyperparams, seed)
    pipeline.fit(X_train, y_train)
    metrics = metrics_of(task, y_test, pipeline.predict(X_test))
    model_file.parent.mkdir(parents=True, exist_ok=True)
    with open(model_file, "xb") as f:
        joblib.dump(pipeline, f)
    return {"metrics": metrics, "model_sha256": sha256_file(model_file),
            "duration_ms": int((time.perf_counter() - start) * 1000), "stratified": stratify is not None}


def run_experiment(db: Session, storage_dir: Path, version: DatasetVersion, name: str, task: str, algorithm: str,
                   target_column: str, hyperparams: dict | None = None, seed: int = 42, test_size: float = 0.2,
                   reproduced_from_id: int | None = None) -> Experiment:
    """Validate (ExperimentError → 422), train and persist. A training crash is stored as status='failed'.
    Commits in both cases so a failed run is kept."""
    hyperparams = resolve_config(task, algorithm, hyperparams, seed, test_size)
    X, y = prepare_xy(load_version_df(storage_dir, version), task, target_column)
    exp = Experiment(name=name, version_id=version.id, task=task, algorithm=algorithm, target_column=target_column,
                     hyperparams_json=hyperparams, seed=seed, test_size=test_size, status="running",
                     reproduced_from_id=reproduced_from_id)
    db.add(exp)
    db.flush()
    rel = storage.model_rel_path(exp.id)
    start = time.perf_counter()
    try:
        result = train(X, y, task, algorithm, hyperparams, seed, test_size, storage.abs_path(storage_dir, rel))
    except Exception as exc:  # noqa: BLE001 — any training crash is recorded, not raised
        exp.status, exp.error = "failed", f"{type(exc).__name__}: {exc}"
        exp.duration_ms = int((time.perf_counter() - start) * 1000)
        audit.log_event(db, "experiment.failed", "experiment", exp.id,
                        {"version_id": version.id, "algorithm": algorithm, "error": exp.error})
        db.commit()
        return exp
    env = env_info()
    exp.metrics_json, exp.env_json = result["metrics"], env
    exp.model_path, exp.model_sha256, exp.duration_ms = rel, result["model_sha256"], result["duration_ms"]
    exp.commitment_sha256 = commitment_of(version.sha256, version.id, task, algorithm, target_column,
                                          hyperparams, seed, test_size, result["metrics"], env)
    exp.status = "completed"
    audit.log_event(db, "experiment.created", "experiment", exp.id, {
        "version_id": version.id, "dataset_sha256": version.sha256, "algorithm": algorithm,
        "seed": seed, "metrics": result["metrics"], "commitment_sha256": exp.commitment_sha256,
        "model_sha256": exp.model_sha256, "reproduced_from_id": reproduced_from_id,
    })
    db.commit()
    return exp


def metric_diffs(a: dict, b: dict) -> tuple[bool, dict]:
    """Per-metric {original, new, diff}; match when every metric agrees within 1e-9."""
    diffs, match = {}, set(a) == set(b)
    for key in sorted(set(a) | set(b)):
        va, vb = a.get(key), b.get(key)
        diff = abs(va - vb) if va is not None and vb is not None else None
        diffs[key] = {"original": va, "new": vb, "diff": diff}
        match = match and diff is not None and diff <= 1e-9
    return match, diffs


def _same(values: list) -> bool:
    first = values[0]
    for v in values[1:]:
        if isinstance(first, (int, float)) and isinstance(v, (int, float)) \
                and not isinstance(first, bool) and not isinstance(v, bool):
            if abs(first - v) > 1e-9:
                return False
        elif v != first:
            return False
    return True


COMPARE_FIELDS = ("name", "version_id", "task", "algorithm", "target_column", "seed", "test_size", "status",
                  "model_sha256", "commitment_sha256", "duration_ms", "reproduced_from_id")


def compare(experiments: list[Experiment]) -> dict:
    rows = [(f, [getattr(e, f) for e in experiments]) for f in COMPARE_FIELDS]
    for prefix, attr in (("hyperparams", "hyperparams_json"), ("metrics", "metrics_json"), ("env", "env_json")):
        dicts = [getattr(e, attr) or {} for e in experiments]
        for key in sorted(set().union(*dicts)):
            rows.append((f"{prefix}.{key}", [d.get(key) for d in dicts]))
    return {"ids": [e.id for e in experiments],
            "fields": [{"field": f, "values": vals, "differs": not _same(vals)} for f, vals in rows]}
