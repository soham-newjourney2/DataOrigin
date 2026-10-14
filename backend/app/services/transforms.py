"""Whitelisted dataframe transforms. Each op is `{op, params}`; anything else raises TransformError (→ 422)."""
import operator

import pandas as pd

from app.services.quality import iqr_outliers


class TransformError(Exception):
    pass


OPS = {
    "drop_duplicates": {"params": {}, "description": "Remove fully duplicated rows (keeps the first)."},
    "drop_missing_rows": {"params": {"columns": "list[str]?"},
                          "description": "Drop rows with a missing value (in the given columns, or any)."},
    "fill_missing": {"params": {"column": "str", "strategy": "mean|median|mode|value", "value": "any?"},
                     "description": "Fill missing values in one column."},
    "drop_columns": {"params": {"columns": "list[str]"}, "description": "Remove columns."},
    "remove_outliers_iqr": {"params": {"columns": "list[str]"},
                            "description": "Drop rows outside Q1−1.5·IQR, Q3+1.5·IQR in any of the numeric columns."},
    "filter_rows": {"params": {"column": "str", "op": "==|!=|>|>=|<|<=", "value": "any"},
                    "description": "Keep only rows where `column op value`."},
}

COMPARATORS = {"==": operator.eq, "!=": operator.ne, ">": operator.gt, ">=": operator.ge,
               "<": operator.lt, "<=": operator.le}


def _column(df: pd.DataFrame, params: dict, key: str = "column") -> str:
    name = params.get(key)
    if not isinstance(name, str) or not name:
        raise TransformError(f"'{key}' must be a column name")
    if name not in df.columns:
        raise TransformError(f"unknown column '{name}'")
    return name


def _columns(df: pd.DataFrame, params: dict, required: bool) -> list[str] | None:
    names = params.get("columns")
    if names is None and not required:
        return None
    if not isinstance(names, list) or not names or not all(isinstance(n, str) for n in names):
        raise TransformError("'columns' must be a non-empty list of column names")
    unknown = [n for n in names if n not in df.columns]
    if unknown:
        raise TransformError(f"unknown column(s): {unknown}")
    return names


def _numeric(df: pd.DataFrame, name: str, op: str) -> None:
    if not pd.api.types.is_numeric_dtype(df[name]) or pd.api.types.is_bool_dtype(df[name]):
        raise TransformError(f"column '{name}' is not numeric")


def _fill_missing(df: pd.DataFrame, p: dict) -> pd.DataFrame:
    name = _column(df, p)
    strategy = p.get("strategy")
    s = df[name]
    if strategy in ("mean", "median"):
        _numeric(df, name, "fill_missing")
        value = getattr(s, strategy)()
    elif strategy == "mode":
        modes = s.mode(dropna=True)
        value = modes.iloc[0] if len(modes) else None
    elif strategy == "value":
        if "value" not in p or p["value"] is None:
            raise TransformError("strategy 'value' needs a 'value'")
        value = p["value"]
    else:
        raise TransformError("strategy must be mean, median, mode or value")
    if value is None or pd.isna(value):
        raise TransformError(f"column '{name}' has no values to compute {strategy} from")
    return df.assign(**{name: s.fillna(value)})


def _remove_outliers(df: pd.DataFrame, p: dict) -> pd.DataFrame:
    names = _columns(df, p, required=True)
    drop = pd.Series(False, index=df.index)
    for name in names:
        _numeric(df, name, "remove_outliers_iqr")
        _, _, idx = iqr_outliers(df[name])
        drop.iloc[idx] = True
    return df[~drop]


def _filter_rows(df: pd.DataFrame, p: dict) -> pd.DataFrame:
    name = _column(df, p)
    cmp = COMPARATORS.get(p.get("op"))
    if cmp is None:
        raise TransformError(f"op must be one of {list(COMPARATORS)}")
    if "value" not in p:
        raise TransformError("'value' is required")
    value, s = p["value"], df[name]
    if pd.api.types.is_numeric_dtype(s) and not pd.api.types.is_bool_dtype(s):
        try:
            value = float(value)
        except (TypeError, ValueError):
            raise TransformError(f"column '{name}' is numeric; value must be a number") from None
    else:
        s, value = s.astype("string"), str(value)
    return df[cmp(s, value).fillna(False).astype(bool)]


def _apply_one(df: pd.DataFrame, op: str, p: dict) -> pd.DataFrame:
    if op == "drop_duplicates":
        return df.drop_duplicates()
    if op == "drop_missing_rows":
        return df.dropna(subset=_columns(df, p, required=False))
    if op == "fill_missing":
        return _fill_missing(df, p)
    if op == "drop_columns":
        return df.drop(columns=_columns(df, p, required=True))
    if op == "remove_outliers_iqr":
        return _remove_outliers(df, p)
    if op == "filter_rows":
        return _filter_rows(df, p)
    raise TransformError(f"unknown op '{op}'; allowed: {list(OPS)}")


def normalize_ops(ops: list) -> list[dict]:
    if not isinstance(ops, list) or not ops:
        raise TransformError("ops must be a non-empty list")
    out = []
    for i, item in enumerate(ops):
        if not isinstance(item, dict) or not isinstance(item.get("op"), str):
            raise TransformError(f"ops[{i}] must be an object with an 'op' name")
        if item["op"] not in OPS:
            raise TransformError(f"unknown op '{item['op']}'; allowed: {list(OPS)}")
        params = item.get("params") or {}
        if not isinstance(params, dict):
            raise TransformError(f"ops[{i}].params must be an object")
        out.append({"op": item["op"], "params": params})
    return out


def apply_ops(df: pd.DataFrame, ops: list[dict]) -> pd.DataFrame:
    """Apply normalized ops in order. Raises TransformError on bad params or an empty result."""
    for i, item in enumerate(ops):
        try:
            df = _apply_one(df, item["op"], item["params"]).reset_index(drop=True)
        except TransformError as exc:
            raise TransformError(f"ops[{i}] {item['op']}: {exc}") from None
    if df.shape[0] == 0 or df.shape[1] == 0:
        raise TransformError("transform produced an empty dataset")
    return df


def summarize_ops(ops: list[dict]) -> str:
    """Short label, e.g. 'drop_duplicates → fill_missing(age)'."""
    parts = []
    for item in ops:
        p = item.get("params") or {}
        target = p.get("column") or ",".join(p.get("columns") or [])
        parts.append(f"{item['op']}({target})" if target else item["op"])
    return " → ".join(parts)
