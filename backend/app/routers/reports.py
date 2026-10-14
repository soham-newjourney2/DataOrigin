from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.config import Settings, current_settings
from app.db import get_db
from app.services import report

router = APIRouter(prefix="/reports", tags=["reports"])


@router.get("/experiment/{experiment_id}")
def experiment_report(experiment_id: int, fmt: str = Query("json", alias="format", pattern="^(json|html)$"),
                      db: Session = Depends(get_db), settings: Settings = Depends(current_settings)):
    """Read-only: builds the report without writing audit events. `?format=html` is printable (Save as PDF)."""
    data = report.build(db, settings, experiment_id)
    if fmt == "html":
        return HTMLResponse(report.to_html(data))
    return data
