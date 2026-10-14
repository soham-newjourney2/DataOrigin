import dataclasses
import hashlib

import pytest
from sqlalchemy import update

from app.db import SessionLocal
from app.models import AuditEvent


def test_csv_upload_hash_and_metadata(client, upload, sample_csv):
    r = upload("adult", "sample.csv", sample_csv, description="d", source="s")
    assert r.status_code == 201, r.text
    body = r.json()
    v1 = body["versions"][0]
    assert v1["version_no"] == 1 and v1["parent_version_id"] is None
    assert v1["sha256"] == hashlib.sha256(sample_csv).hexdigest()
    assert (v1["row_count"], v1["col_count"]) == (30, 7)
    assert v1["file_format"] == "csv"
    assert {c["name"] for c in v1["schema_json"]} >= {"age", "income", "purchased"}

    listing = client.get("/api/datasets").json()
    assert listing[0]["version_count"] == 1 and listing[0]["latest_version"]["id"] == v1["id"]
    assert client.get(f"/api/datasets/{body['id']}").json()["name"] == "adult"


def test_json_and_xlsx_uploads(upload, sample_json, sample_xlsx):
    rj = upload("j", "sample.json", sample_json)
    assert rj.status_code == 201, rj.text
    assert rj.json()["versions"][0]["sha256"] == hashlib.sha256(sample_json).hexdigest()
    assert rj.json()["versions"][0]["row_count"] == 6

    rx = upload("x", "data.XLSX", sample_xlsx)
    assert rx.status_code == 201, rx.text
    v = rx.json()["versions"][0]
    assert v["file_format"] == "xlsx" and (v["row_count"], v["col_count"]) == (4, 3)
    assert v["sha256"] == hashlib.sha256(sample_xlsx).hexdigest()


@pytest.mark.parametrize("filename,data,reason", [
    ("data.txt", b"a,b\n1,2\n", "extension"),
    ("empty.csv", b"", "empty"),
    ("header_only.csv", b"a,b\n", "no data rows"),
    ("dupe_cols.csv", b"a,a\n1,2\n", "duplicate column"),
    ("blank_col.csv", b"a,\n1,2\n", "empty name"),
    ("obj.json", b'{"a": 1}', "list of records"),
    ("bad.json", b"[1, 2]", "list of records"),
    ("broken.xlsx", b"not a workbook", "could not parse"),
])
def test_invalid_uploads_422(upload, filename, data, reason):
    r = upload("bad", filename, data)
    assert r.status_code == 422
    assert reason in r.json()["detail"]


def test_size_limit_422(client, upload):
    client.app.state.settings = dataclasses.replace(client.app.state.settings, max_upload_mb=0)
    r = upload("big", "a.csv", b"a\n1\n")
    assert r.status_code == 422 and "limit" in r.json()["detail"]


def test_duplicate_name_409(upload, sample_csv, sample_json):
    assert upload("same", "sample.csv", sample_csv).status_code == 201
    r = upload("same", "sample.json", sample_json)
    assert r.status_code == 409


def test_second_version_and_same_sha_409(client, upload, sample_csv):
    ds = upload("ds", "sample.csv", sample_csv).json()
    v1_id = ds["versions"][0]["id"]
    v2_bytes = sample_csv + b"60,50000.0,40,10,master,Paris,1\n"
    r = client.post(f"/api/datasets/{ds['id']}/versions", data={"note": "more rows"},
                    files={"file": ("v2.csv", v2_bytes)})
    assert r.status_code == 201, r.text
    v2 = r.json()
    assert v2["version_no"] == 2 and v2["parent_version_id"] == v1_id and v2["row_count"] == 31

    dup = client.post(f"/api/datasets/{ds['id']}/versions", files={"file": ("again.csv", sample_csv)})
    assert dup.status_code == 409
    assert client.post("/api/datasets/999/versions", files={"file": ("a.csv", b"a\n1\n")}).status_code == 404


def test_version_read_preview_download(client, upload, sample_csv):
    vid = upload("p", "sample.csv", sample_csv).json()["versions"][0]["id"]
    assert client.get(f"/api/versions/{vid}").json()["id"] == vid
    prev = client.get(f"/api/versions/{vid}/preview?rows=5").json()
    assert prev["columns"][0] == "age" and len(prev["rows"]) == 5
    assert prev["rows"][3][0] is None  # the missing age cell serializes as null
    dl = client.get(f"/api/versions/{vid}/download")
    assert dl.status_code == 200 and dl.content == sample_csv
    assert client.get("/api/versions/999").status_code == 404


def test_verify_local_and_tamper(client, upload, sample_csv):
    vid = upload("t", "sample.csv", sample_csv).json()["versions"][0]["id"]
    before = client.post(f"/api/versions/{vid}/verify-local").json()
    assert before["match"] is True and before["current"] == before["stored"]

    assert client.post(f"/api/dev/tamper/{vid}").status_code == 200
    after = client.post(f"/api/versions/{vid}/verify-local").json()
    assert after["match"] is False and after["current"] != after["stored"]


def test_tamper_disabled_without_demo_mode(client, upload, sample_csv):
    vid = upload("t", "sample.csv", sample_csv).json()["versions"][0]["id"]
    client.app.state.settings = dataclasses.replace(client.app.state.settings, demo_mode=False)
    assert client.post(f"/api/dev/tamper/{vid}").status_code == 404


def test_audit_events_and_chain(client, upload, sample_csv):
    ds = upload("a", "sample.csv", sample_csv).json()
    events = client.get("/api/audit/events").json()
    assert [e["event_type"] for e in events] == ["version.created", "dataset.created"]
    assert events[1]["prev_hash"] == "0" * 64 and events[0]["prev_hash"] == events[1]["event_hash"]
    filtered = client.get(f"/api/audit/events?entity_type=dataset&entity_id={ds['id']}").json()
    assert len(filtered) == 1

    ok = client.get("/api/audit/verify").json()
    assert ok == {"valid": True, "checked": 2, "broken_at": None}

    first_id = events[1]["id"]
    with SessionLocal() as db:
        db.execute(update(AuditEvent).where(AuditEvent.id == first_id).values(payload_json={"name": "evil"}))
        db.commit()
    bad = client.get("/api/audit/verify").json()
    assert bad["valid"] is False and bad["broken_at"] == first_id
