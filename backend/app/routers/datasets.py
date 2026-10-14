from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings, current_settings
from app.db import get_db
from app.models import Dataset, DatasetVersion
from app.schemas import DatasetDetail, DatasetListItem, DatasetOut, VersionOut
from app.services import audit
from app.services.loader import LoadError, format_from_filename, load_bytes
from app.services.versions import ConflictError, create_version, latest_version

router = APIRouter(tags=["datasets"])


async def read_upload(file: UploadFile, settings: Settings) -> tuple[bytes, str, object]:
    """Validate extension, size and content. Returns (bytes, format, dataframe)."""
    fmt = format_from_filename(file.filename)
    limit = settings.max_upload_mb * 1024 * 1024
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise LoadError(f"file exceeds {settings.max_upload_mb} MB limit")
    return data, fmt, load_bytes(data, fmt)


def get_dataset_or_404(db: Session, dataset_id: int) -> Dataset:
    dataset = db.get(Dataset, dataset_id)
    if dataset is None:
        raise HTTPException(404, f"dataset {dataset_id} not found")
    return dataset


@router.post("/datasets", response_model=DatasetDetail, status_code=201)
async def upload_dataset(
    file: UploadFile = File(...), name: str = Form(...), description: str | None = Form(None),
    source: str | None = Form(None), db: Session = Depends(get_db),
    settings: Settings = Depends(current_settings),
):
    name = name.strip()
    if not name:
        raise LoadError("name must not be empty")
    if db.scalar(select(Dataset).where(Dataset.name == name)):
        raise ConflictError(f"dataset name '{name}' already exists")
    data, fmt, df = await read_upload(file, settings)

    dataset = Dataset(name=name, description=description, source=source)
    db.add(dataset)
    db.flush()
    audit.log_event(db, "dataset.created", "dataset", dataset.id,
                    {"name": name, "description": description, "source": source, "filename": file.filename})
    create_version(db, settings.storage_dir, dataset, data, fmt, df, note="initial upload")
    db.commit()
    db.refresh(dataset)
    return dataset


@router.get("/datasets", response_model=list[DatasetListItem])
def list_datasets(db: Session = Depends(get_db)):
    counts = dict(db.execute(
        select(DatasetVersion.dataset_id, func.count()).group_by(DatasetVersion.dataset_id)
    ).all())
    items = []
    for d in db.scalars(select(Dataset).order_by(Dataset.id)):
        latest = latest_version(db, d.id)
        items.append(DatasetListItem(
            **DatasetOut.model_validate(d).model_dump(),
            version_count=counts.get(d.id, 0),
            latest_version=VersionOut.model_validate(latest) if latest else None,
        ))
    return items


@router.get("/datasets/{dataset_id}", response_model=DatasetDetail)
def get_dataset(dataset_id: int, db: Session = Depends(get_db)):
    return get_dataset_or_404(db, dataset_id)


@router.post("/datasets/{dataset_id}/versions", response_model=VersionOut, status_code=201)
async def upload_version(
    dataset_id: int, file: UploadFile = File(...), note: str | None = Form(None),
    db: Session = Depends(get_db), settings: Settings = Depends(current_settings),
):
    dataset = get_dataset_or_404(db, dataset_id)
    data, fmt, df = await read_upload(file, settings)
    parent = latest_version(db, dataset.id)
    version = create_version(db, settings.storage_dir, dataset, data, fmt, df, parent=parent, note=note,
                             ops=[{"op": "manual_upload"}])
    db.commit()
    return version
