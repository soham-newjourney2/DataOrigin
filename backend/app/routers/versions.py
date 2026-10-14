import json

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.config import Settings, current_settings
from app.db import get_db
from app.models import DatasetVersion
from app.schemas import PreviewOut, TransformIn, TransformOut, VerifyLocalOut, VersionOut
from app.services import audit, quality, storage, transforms
from app.services.hashing import sha256_file
from app.services.loader import load_bytes
from app.services.versions import create_version, load_version_df

router = APIRouter(tags=["versions"])


def get_version_or_404(db: Session, version_id: int) -> DatasetVersion:
    version = db.get(DatasetVersion, version_id)
    if version is None:
        raise HTTPException(404, f"version {version_id} not found")
    return version


@router.get("/versions/{version_id}", response_model=VersionOut)
def get_version(version_id: int, db: Session = Depends(get_db)):
    return get_version_or_404(db, version_id)


@router.get("/versions/{version_id}/preview", response_model=PreviewOut)
def preview_version(version_id: int, rows: int = Query(20, ge=1, le=500), db: Session = Depends(get_db),
                    settings: Settings = Depends(current_settings)):
    version = get_version_or_404(db, version_id)
    df = load_version_df(settings.storage_dir, version)
    split = json.loads(df.head(rows).to_json(orient="split", date_format="iso", index=False))
    return {"columns": split["columns"], "rows": split["data"]}


@router.get("/versions/{version_id}/download")
def download_version(version_id: int, db: Session = Depends(get_db),
                     settings: Settings = Depends(current_settings)):
    version = get_version_or_404(db, version_id)
    path = storage.abs_path(settings.storage_dir, version.file_path)
    if not path.is_file():
        raise HTTPException(404, "stored file is missing")
    filename = f"{version.dataset.name}_v{version.version_no}.{version.file_format}"
    return FileResponse(path, filename=filename)


@router.post("/versions/{version_id}/verify-local", response_model=VerifyLocalOut)
def verify_local(version_id: int, db: Session = Depends(get_db), settings: Settings = Depends(current_settings)):
    version = get_version_or_404(db, version_id)
    path = storage.abs_path(settings.storage_dir, version.file_path)
    current = sha256_file(path) if path.is_file() else None
    result = {"stored": version.sha256, "current": current, "match": current == version.sha256}
    audit.log_event(db, "version.verified_local", "dataset_version", version.id, result)
    db.commit()
    return result


@router.post("/versions/{version_id}/transform", response_model=TransformOut, status_code=201)
def transform_version(version_id: int, body: TransformIn, db: Session = Depends(get_db),
                      settings: Settings = Depends(current_settings)):
    """Apply whitelisted ops in order; the result is saved as a new CSV version (parent = this version)."""
    parent = get_version_or_404(db, version_id)
    ops = transforms.normalize_ops(body.ops)
    df = transforms.apply_ops(load_version_df(settings.storage_dir, parent), ops)
    data = df.to_csv(index=False).encode()
    version = create_version(db, settings.storage_dir, parent.dataset, data, "csv", load_bytes(data, "csv"),
                             parent=parent, note=body.note or f"transform: {transforms.summarize_ops(ops)}",
                             ops=ops)
    report = quality.latest_report(db, version.id)
    db.commit()
    return {**VersionOut.model_validate(version).model_dump(by_alias=True),
            "ops": ops, "quality_score": report.score}
