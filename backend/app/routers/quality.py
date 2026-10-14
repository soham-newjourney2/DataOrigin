from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.config import Settings, current_settings
from app.db import get_db
from app.routers.versions import get_version_or_404
from app.schemas import DriftOut, QualityReportOut
from app.services import audit, drift, quality
from app.services.versions import load_version_df

router = APIRouter(tags=["quality"])


@router.get("/versions/{version_id}/quality", response_model=QualityReportOut)
def get_quality(version_id: int, db: Session = Depends(get_db)):
    get_version_or_404(db, version_id)
    report = quality.latest_report(db, version_id)
    if report is None:
        raise HTTPException(404, f"no quality report for version {version_id}")
    return report


@router.post("/versions/{version_id}/quality", response_model=QualityReportOut, status_code=201)
def rerun_quality(version_id: int, db: Session = Depends(get_db), settings: Settings = Depends(current_settings)):
    version = get_version_or_404(db, version_id)
    report = quality.save_report(db, version, load_version_df(settings.storage_dir, version))
    audit.log_event(db, "quality.analyzed", "dataset_version", version.id,
                    {"report_id": report.id, "score": report.score})
    db.commit()
    return report


@router.get("/versions/{version_id}/drift", response_model=DriftOut)
def get_drift(version_id: int, against: int = Query(...), db: Session = Depends(get_db),
              settings: Settings = Depends(current_settings)):
    """Drift of `version_id` relative to the reference version `against`."""
    current = get_version_or_404(db, version_id)
    reference = get_version_or_404(db, against)
    result = drift.compare(load_version_df(settings.storage_dir, reference),
                           load_version_df(settings.storage_dir, current))
    return {"version_id": current.id, "against_version_id": reference.id, **result}
