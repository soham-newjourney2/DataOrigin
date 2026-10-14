"""Per-column drift between two versions: PSI for numeric and categorical columns, plus schema changes."""
import numpy as np
import pandas as pd

from app.services.quality import safe_float

EPS = 1e-6
BINS = 10


def status_of(psi: float | None) -> str:
    if psi is None:
        return "unknown"
    return "none" if psi < 0.1 else "moderate" if psi < 0.25 else "major"


def _psi(pa: np.ndarray, pb: np.ndarray) -> float:
    pa, pb = np.maximum(pa, EPS), np.maximum(pb, EPS)
    return float(np.sum((pb - pa) * np.log(pb / pa)))


def numeric_psi(a: pd.Series, b: pd.Series) -> float | None:
    """Bins are A's deciles (open-ended at both sides)."""
    a, b = a.dropna().astype(float).to_numpy(), b.dropna().astype(float).to_numpy()
    if not len(a) or not len(b):
        return None
    inner = np.unique(np.quantile(a, np.linspace(0, 1, BINS + 1)[1:-1]))
    edges = np.concatenate([[-np.inf], inner, [np.inf]])
    ca, _ = np.histogram(a, edges)
    cb, _ = np.histogram(b, edges)
    return _psi(ca / len(a), cb / len(b))


def categorical_psi(a: pd.Series, b: pd.Series) -> tuple[float | None, list[str], list[str]]:
    """PSI on category frequencies, plus categories new in B and missing from B."""
    fa = a.dropna().astype(str).value_counts(normalize=True)
    fb = b.dropna().astype(str).value_counts(normalize=True)
    new = sorted(set(fb.index) - set(fa.index))
    missing = sorted(set(fa.index) - set(fb.index))
    if fa.empty or fb.empty:
        return None, new, missing
    cats = fa.index.union(fb.index)
    psi = _psi(fa.reindex(cats, fill_value=0).to_numpy(), fb.reindex(cats, fill_value=0).to_numpy())
    return psi, new, missing


def compare(df_a: pd.DataFrame, df_b: pd.DataFrame) -> dict:
    """Drift of B relative to reference A."""
    cols_a, cols_b = list(df_a.columns), list(df_b.columns)
    added = [c for c in cols_b if c not in cols_a]
    removed = [c for c in cols_a if c not in cols_b]
    columns = []
    for name in (c for c in cols_a if c in cols_b):
        a, b = df_a[name], df_b[name]
        numeric = pd.api.types.is_numeric_dtype(a) and pd.api.types.is_numeric_dtype(b) \
            and not pd.api.types.is_bool_dtype(a) and not pd.api.types.is_bool_dtype(b)
        col = {"name": name, "kind": "numeric" if numeric else "categorical",
               "dtype_a": str(a.dtype), "dtype_b": str(b.dtype)}
        if numeric:
            psi = numeric_psi(a, b)
            col.update(mean_a=safe_float(a.mean()), mean_b=safe_float(b.mean()),
                       std_a=safe_float(a.std()), std_b=safe_float(b.std()))
        else:
            psi, new, missing = categorical_psi(a, b)
            col.update(new_categories=new, missing_categories=missing)
        col.update(psi=safe_float(psi), status=status_of(psi))
        columns.append(col)

    counts = {s: sum(c["status"] == s for c in columns) for s in ("none", "moderate", "major", "unknown")}
    overall = "major" if counts["major"] else "moderate" if counts["moderate"] else "none"
    return {
        "schema": {"added": added, "removed": removed, "changed": bool(added or removed)},
        "columns": columns,
        "summary": {**counts, "overall": overall},
    }
