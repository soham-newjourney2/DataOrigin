"""Hash-chained audit log. log_event adds to the session; the caller commits with its own changes."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AuditEvent, utcnow_iso
from app.services.hashing import canonical, sha256_text

GENESIS_HASH = "0" * 64


def compute_event_hash(prev_hash: str, event_type: str, entity_type: str, entity_id: int,
                       payload: dict, created_at: str) -> str:
    return sha256_text(prev_hash + event_type + entity_type + str(entity_id) + canonical(payload) + created_at)


def _last_hash(db: Session) -> str:
    last = db.scalar(select(AuditEvent.event_hash).order_by(AuditEvent.id.desc()).limit(1))
    return last or GENESIS_HASH


def log_event(db: Session, event_type: str, entity_type: str, entity_id: int, payload: dict | None = None) -> AuditEvent:
    payload = payload or {}
    prev_hash = _last_hash(db)
    created_at = utcnow_iso()
    event = AuditEvent(
        event_type=event_type, entity_type=entity_type, entity_id=entity_id, payload_json=payload,
        prev_hash=prev_hash, created_at=created_at,
        event_hash=compute_event_hash(prev_hash, event_type, entity_type, entity_id, payload, created_at),
    )
    db.add(event)
    db.flush()
    return event


def verify_chain(db: Session) -> dict:
    """Walk events in id order; broken_at is the id of the first event whose link or hash is wrong."""
    prev_hash = GENESIS_HASH
    checked = 0
    for e in db.scalars(select(AuditEvent).order_by(AuditEvent.id)):
        checked += 1
        expected = compute_event_hash(prev_hash, e.event_type, e.entity_type, e.entity_id, e.payload_json, e.created_at)
        if e.prev_hash != prev_hash or e.event_hash != expected:
            return {"valid": False, "checked": checked, "broken_at": e.id}
        prev_hash = e.event_hash
    return {"valid": True, "checked": checked, "broken_at": None}
