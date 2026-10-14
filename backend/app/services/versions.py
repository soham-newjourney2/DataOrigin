"""Create immutable dataset versions (file + row + optional transformation + quality report + audit event)."""
from pathlib import Path

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Dataset, DatasetVersion, Transformation
from app.services import audit, quality, storage
from app.services.hashing import sha256_bytes
from app.services.loader import load_df, schema_of


class ConflictError(Exception):
    pass


def latest_version(db: Session, dataset_id: int) -> DatasetVersion | None:
    return db.scalar(
        select(DatasetVersion).where(DatasetVersion.dataset_id == dataset_id)
        .order_by(DatasetVersion.version_no.desc()).limit(1)
    )


def load_version_df(storage_dir: Path, version: DatasetVersion) -> pd.DataFrame:
    return load_df(storage.abs_path(storage_dir, version.file_path), version.file_format)


def create_version(db: Session, storage_dir: Path, dataset: Dataset, data: bytes, fmt: str, df: pd.DataFrame,
                   parent: DatasetVersion | None = None, note: str | None = None,
                   ops: list[dict] | None = None) -> DatasetVersion:
    """Caller commits. Raises ConflictError if the bytes match an existing version of this dataset."""
    sha = sha256_bytes(data)
    existing = db.scalar(
        select(DatasetVersion).where(DatasetVersion.dataset_id == dataset.id, DatasetVersion.sha256 == sha)
    )
    if existing:
        raise ConflictError(f"identical to existing version v{existing.version_no} (sha256 {sha})")

    version_no = (db.scalar(
        select(func.max(DatasetVersion.version_no)).where(DatasetVersion.dataset_id == dataset.id)
    ) or 0) + 1
    rel_path = storage.save_version_file(storage_dir, dataset.id, version_no, fmt, data)
    version = DatasetVersion(
        dataset_id=dataset.id, version_no=version_no, parent_version_id=parent.id if parent else None,
        file_path=rel_path, file_format=fmt, sha256=sha, size_bytes=len(data),
        row_count=int(df.shape[0]), col_count=int(df.shape[1]), schema_json=schema_of(df), note=note,
    )
    db.add(version)
    db.flush()
    if parent is not None:
        db.add(Transformation(input_version_id=parent.id, output_version_id=version.id, ops_json=ops or []))
    quality.save_report(db, version, df)
    audit.log_event(db, "version.created", "dataset_version", version.id, {
        "dataset_id": dataset.id, "version_no": version_no, "sha256": sha,
        "parent_version_id": version.parent_version_id, "ops": ops or [],
    })
    return version
