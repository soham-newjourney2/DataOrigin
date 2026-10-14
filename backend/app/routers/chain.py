from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.config import Settings, current_settings
from app.db import get_db
from app.schemas import ChainRecordOut, ChainRegisterIn, ChainRetryOut, ChainStatusOut, ChainVerifyOut
from app.services import chain

router = APIRouter(prefix="/chain", tags=["chain"])


@router.get("/status", response_model=ChainStatusOut)
def chain_status(settings: Settings = Depends(current_settings)):
    return chain.status(settings)


@router.post("/register", response_model=ChainRecordOut)
def register(body: ChainRegisterIn, db: Session = Depends(get_db), settings: Settings = Depends(current_settings)):
    """Registered, pending (node down / chain disabled) or failed (revert). Idempotent per artifact."""
    record = chain.register(db, settings, body.artifact_type, body.artifact_id)
    db.commit()
    return record


@router.post("/retry-pending", response_model=ChainRetryOut)
def retry_pending(db: Session = Depends(get_db), settings: Settings = Depends(current_settings)):
    """Retry pending records, and re-anchor records lost to a chain reset (in-memory node restarted)."""
    pending, lost = chain.retry_pending(db, settings)
    db.commit()
    records = pending + lost
    return {"retried": len(pending), "reanchored": len(lost),
            "registered": sum(r.status == "registered" for r in records), "records": records}


@router.get("/verify/{artifact_type}/{artifact_id}", response_model=ChainVerifyOut)
def verify(artifact_type: str, artifact_id: int, quiet: bool = False, db: Session = Depends(get_db),
           settings: Settings = Depends(current_settings)):
    """`quiet=true` skips the `chain.verified` audit event (read-only views such as the dashboard)."""
    result = chain.verify(db, settings, artifact_type, artifact_id, log=not quiet)
    db.commit()
    return result
