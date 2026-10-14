from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import AuditEvent
from app.schemas import AuditEventOut, AuditVerifyOut
from app.services.audit import verify_chain

router = APIRouter(tags=["audit"])


@router.get("/audit/events", response_model=list[AuditEventOut])
def list_events(entity_type: str | None = None, entity_id: int | None = None,
                limit: int = Query(100, ge=1, le=1000), db: Session = Depends(get_db)):
    """Newest first."""
    query = select(AuditEvent)
    if entity_type:
        query = query.where(AuditEvent.entity_type == entity_type)
    if entity_id is not None:
        query = query.where(AuditEvent.entity_id == entity_id)
    return db.scalars(query.order_by(AuditEvent.id.desc()).limit(limit)).all()


@router.get("/audit/verify", response_model=AuditVerifyOut)
def verify(db: Session = Depends(get_db)):
    return verify_chain(db)
