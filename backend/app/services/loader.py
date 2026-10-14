"""Parse and validate uploaded tabular files. Every failure raises LoadError (→ HTTP 422)."""
import io
import json
from pathlib import Path

import pandas as pd

ALLOWED_FORMATS = ("csv", "xlsx", "json")


class LoadError(ValueError):
    pass


def format_from_filename(filename: str | None) -> str:
    ext = Path(filename or "").suffix.lower().lstrip(".")
    if ext not in ALLOWED_FORMATS:
        raise LoadError(f"unsupported file extension '.{ext}'; allowed: {', '.join(ALLOWED_FORMATS)}")
    return ext


def _raw_header(data: bytes, fmt: str) -> list:
    """Header cells as written in the file (pandas would silently rename blanks/duplicates)."""
    if fmt == "csv":
        return pd.read_csv(io.BytesIO(data), header=None, nrows=1).iloc[0].tolist()
    return pd.read_excel(io.BytesIO(data), sheet_name=0, header=None, nrows=1, engine="openpyxl").iloc[0].tolist()


def _check_columns(columns: list) -> None:
    names = []
    for i, col in enumerate(columns):
        if col is None or (isinstance(col, float) and pd.isna(col)) or not str(col).strip():
            raise LoadError(f"column {i + 1} has an empty name")
        names.append(str(col).strip())
    dupes = sorted({n for n in names if names.count(n) > 1})
    if dupes:
        raise LoadError(f"duplicate column names: {', '.join(dupes)}")


def _parse_json(data: bytes) -> pd.DataFrame:
    try:
        records = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise LoadError(f"invalid JSON: {e}") from e
    if not isinstance(records, list) or not all(isinstance(r, dict) for r in records):
        raise LoadError("JSON must be a list of records (objects)")
    columns = list(dict.fromkeys(k for r in records for k in r))
    _check_columns(columns)
    return pd.DataFrame.from_records(records, columns=columns)


def load_bytes(data: bytes, fmt: str) -> pd.DataFrame:
    if not data:
        raise LoadError("file is empty")
    try:
        if fmt == "json":
            df = _parse_json(data)
        elif fmt == "csv":
            _check_columns(_raw_header(data, fmt))
            df = pd.read_csv(io.BytesIO(data))
        elif fmt == "xlsx":
            _check_columns(_raw_header(data, fmt))
            df = pd.read_excel(io.BytesIO(data), sheet_name=0, engine="openpyxl")
        else:
            raise LoadError(f"unsupported format '{fmt}'")
    except LoadError:
        raise
    except pd.errors.EmptyDataError as e:
        raise LoadError("file is empty") from e
    except Exception as e:
        raise LoadError(f"could not parse {fmt} file: {e}") from e
    if df.shape[1] < 1:
        raise LoadError("file has no columns")
    if df.shape[0] < 1:
        raise LoadError("file has no data rows")
    df.columns = [str(c).strip() for c in df.columns]
    return df


def load_df(path: Path, fmt: str) -> pd.DataFrame:
    return load_bytes(Path(path).read_bytes(), fmt)


def schema_of(df: pd.DataFrame) -> list[dict]:
    return [{"name": c, "dtype": str(df[c].dtype)} for c in df.columns]
