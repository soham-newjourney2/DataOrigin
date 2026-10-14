"""Data quality analysis: missing, duplicates, IQR outliers, constant columns, IsolationForest anomalies, score."""
import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import DatasetVersion, QualityReport

SAMPLE_N = 5


def safe_float(x) -> float | None:
    """JSON-safe float (NaN/inf → None)."""
    if x is None:
        return None
    x = float(x)
    return round(x, 6) if np.isfinite(x) else None


def _pct(part: int, whole: int) -> float:
    return round(100.0 * part / whole, 2) if whole else 0.0


def _numeric_columns(df: pd.DataFrame) -> list[str]:
    return [c for c in df.select_dtypes(include="number").columns if df[c].notna().any()]


def iqr_outliers(s: pd.Series) -> tuple[float, float, np.ndarray]:
    """Bounds and positional indices of values outside Q1−1.5·IQR, Q3+1.5·IQR."""
    q1, q3 = s.quantile(0.25), s.quantile(0.75)
    iqr = q3 - q1
    lower, upper = q1 - 1.5 * iqr, q3 + 1.5 * iqr
    mask = ((s < lower) | (s > upper)).to_numpy()
    return float(lower), float(upper), np.flatnonzero(mask)


def _anomalies(df: pd.DataFrame, numeric: list[str]) -> dict:
    rows = len(df)
    if len(numeric) < 2 or rows < 20:
        return {"ran": False, "reason": "needs ≥2 numeric columns and ≥20 rows", "count": 0, "top_indices": []}
    x = df[numeric].astype(float)
    x = x.fillna(x.median()).to_numpy()
    model = IsolationForest(random_state=42)
    labels = model.fit_predict(x)
    scores = model.score_samples(x)  # lower = more anomalous
    top = np.argsort(scores, kind="stable")[:SAMPLE_N]
    return {
        "ran": True, "columns": numeric, "count": int((labels == -1).sum()),
        "top_indices": [int(i) for i in top], "top_scores": [safe_float(scores[i]) for i in top],
    }


def score_of(missing_pct: float, dup_pct: float, outlier_row_pct: float, constant_cols: int) -> float:
    score = (100 - min(40, missing_pct * 2) - min(30, dup_pct * 3)
             - min(20, outlier_row_pct * 2) - min(10, 5 * constant_cols))
    return round(max(0.0, min(100.0, score)), 1)


def analyze(df: pd.DataFrame) -> dict:
    rows, cols = df.shape
    numeric = _numeric_columns(df)
    columns, findings = [], []
    outlier_rows = np.zeros(rows, dtype=bool)
    constant = []

    for name in df.columns:
        s = df[name]
        missing = int(s.isna().sum())
        missing_pct = _pct(missing, rows)
        nunique = int(s.nunique(dropna=True))
        col = {"name": name, "dtype": str(s.dtype), "missing": missing, "missing_pct": missing_pct,
               "unique": nunique, "is_numeric": name in numeric, "outliers": None}
        if name in numeric:
            col.update(mean=safe_float(s.mean()), std=safe_float(s.std()), min=safe_float(s.min()), max=safe_float(s.max()))
            lower, upper, idx = iqr_outliers(s)
            outlier_rows[idx] = True
            col["outliers"] = {"count": int(len(idx)), "lower": safe_float(lower), "upper": safe_float(upper),
                               "sample_indices": [int(i) for i in idx[:SAMPLE_N]]}
            if len(idx):
                findings.append({
                    "severity": "warning", "check": "outliers", "column": name,
                    "message": f"Column `{name}` has {len(idx)} IQR outlier(s) outside "
                               f"[{lower:.4g}, {upper:.4g}].",
                    "suggestion": f"Review them, or apply remove_outliers_iqr on `{name}`.",
                })
        if missing:
            strategy = "median" if name in numeric else "mode"
            findings.append({
                "severity": "critical" if missing_pct >= 30 else "warning", "check": "missing", "column": name,
                "message": f"Column `{name}` has {missing} missing value(s) ({missing_pct:g}%).",
                "suggestion": (f"Consider drop_columns on `{name}`." if missing_pct >= 50 else
                               f"Consider fill_missing with {strategy}, or drop_missing_rows."),
            })
        if nunique <= 1:
            constant.append(name)
            findings.append({
                "severity": "info", "check": "constant", "column": name,
                "message": f"Column `{name}` is constant ({nunique} distinct value).",
                "suggestion": f"Consider drop_columns on `{name}`; it carries no information.",
            })
        columns.append(col)

    missing_total = int(df.isna().sum().sum())
    missing_pct = _pct(missing_total, rows * cols)
    dup_mask = df.duplicated().to_numpy()
    dup_count = int(dup_mask.sum())
    dup_pct = _pct(dup_count, rows)
    if dup_count:
        findings.append({
            "severity": "critical" if dup_pct >= 10 else "warning", "check": "duplicates", "column": None,
            "message": f"{dup_count} fully duplicated row(s) ({dup_pct:g}%).",
            "suggestion": "Consider drop_duplicates.",
        })
    outlier_row_count = int(outlier_rows.sum())
    outlier_row_pct = _pct(outlier_row_count, rows)

    anomalies = _anomalies(df, numeric)
    if anomalies["count"]:
        findings.append({
            "severity": "info", "check": "anomalies", "column": None,
            "message": f"IsolationForest flagged {anomalies['count']} row(s) as anomalous "
                       f"(most anomalous: {anomalies['top_indices']}).",
            "suggestion": "Inspect these rows; they may be data-entry errors or rare cases.",
        })

    order = {"critical": 0, "warning": 1, "info": 2}
    findings.sort(key=lambda f: order[f["severity"]])
    summary = {
        "rows": rows, "cols": cols, "missing_total": missing_total, "missing_pct": missing_pct,
        "duplicate_rows": dup_count, "duplicate_pct": dup_pct,
        "duplicate_sample_indices": [int(i) for i in np.flatnonzero(dup_mask)[:SAMPLE_N]],
        "outlier_rows": outlier_row_count, "outlier_row_pct": outlier_row_pct,
        "constant_columns": constant,
        "dtypes": {k: int(v) for k, v in df.dtypes.astype(str).value_counts().items()},
    }
    return {
        "score": score_of(missing_pct, dup_pct, outlier_row_pct, len(constant)),
        "summary": summary, "columns": columns, "findings": findings, "anomalies": anomalies,
    }


def save_report(db: Session, version: DatasetVersion, df: pd.DataFrame) -> QualityReport:
    """Analyze and store a report for this version. Caller commits."""
    report = analyze(df)
    row = QualityReport(version_id=version.id, score=report["score"], report_json=report)
    db.add(row)
    db.flush()
    return row


def latest_report(db: Session, version_id: int) -> QualityReport | None:
    return db.scalar(
        select(QualityReport).where(QualityReport.version_id == version_id)
        .order_by(QualityReport.id.desc()).limit(1)
    )
