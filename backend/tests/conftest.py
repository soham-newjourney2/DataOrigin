import io
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.main import create_app

DATA_DIR = Path(__file__).parent / "data"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    monkeypatch.setenv("STORAGE_DIR", str(tmp_path / "storage"))
    monkeypatch.setenv("CHAIN_ENABLED", "false")
    monkeypatch.setenv("DEMO_MODE", "true")
    with TestClient(create_app()) as c:
        yield c


@pytest.fixture
def sample_csv() -> bytes:
    return (DATA_DIR / "sample.csv").read_bytes()


@pytest.fixture
def sample_json() -> bytes:
    return (DATA_DIR / "sample.json").read_bytes()


@pytest.fixture
def sample_xlsx() -> bytes:
    df = pd.DataFrame({"x": [1, 2, 3, 4], "y": [0.5, 1.5, 2.5, 3.5], "label": ["a", "b", "a", "b"]})
    buf = io.BytesIO()
    df.to_excel(buf, index=False, engine="openpyxl")
    return buf.getvalue()


@pytest.fixture
def upload(client):
    """upload(name, filename, data, **form) -> response for POST /api/datasets."""
    def _upload(name: str, filename: str, data: bytes, **form):
        return client.post("/api/datasets", data={"name": name, **form}, files={"file": (filename, data)})
    return _upload
