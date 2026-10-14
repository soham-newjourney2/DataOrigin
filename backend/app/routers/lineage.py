from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.routers.datasets import get_dataset_or_404
from app.routers.versions import get_version_or_404
from app.services import lineage

router = APIRouter(tags=["lineage"])


@router.get("/datasets/{dataset_id}/lineage")
def dataset_lineage(dataset_id: int, db: Session = Depends(get_db)):
    get_dataset_or_404(db, dataset_id)
    return lineage.dataset_graph(db, dataset_id)


@router.get("/versions/{version_id}/impact")
def version_impact(version_id: int, db: Session = Depends(get_db)):
    return lineage.impact(db, get_version_or_404(db, version_id))
