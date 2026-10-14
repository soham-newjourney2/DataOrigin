from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.config import Settings, current_settings
from app.db import get_db
from app.routers.versions import get_version_or_404
from app.services import audit, storage
from app.services.hashing import sha256_file

router = APIRouter(tags=["dev"])

TAMPER_BYTES = b"\n#tampered"


@router.get("/dev/status")
def dev_status(settings: Settings = Depends(current_settings)):
    """Lets the UI decide whether to show demo-only controls."""
    return {"demo_mode": settings.demo_mode}


@router.post("/dev/tamper/{version_id}")
def tamper(version_id: int, db: Session = Depends(get_db), settings: Settings = Depends(current_settings)):
    """Demo only: append bytes to a stored file so verification fails."""
    if not settings.demo_mode:
        raise HTTPException(404, "Not Found")
    version = get_version_or_404(db, version_id)
    path = storage.abs_path(settings.storage_dir, version.file_path)
    with open(path, "ab") as f:
        f.write(TAMPER_BYTES)
    current = sha256_file(path)
    audit.log_event(db, "dev.tampered", "dataset_version", version.id, {"appended_bytes": len(TAMPER_BYTES)})
    db.commit()
    return {"version_id": version.id, "stored": version.sha256, "current": current, "tampered": True}
