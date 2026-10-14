"""All §3 tables. Timestamps are UTC ISO-8601 strings (exact round-trip, needed by the audit hash chain)."""
from datetime import datetime, timezone

from sqlalchemy import JSON, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


class Dataset(Base):
    __tablename__ = "datasets"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    description: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String(40), default=utcnow_iso)

    versions: Mapped[list["DatasetVersion"]] = relationship(
        back_populates="dataset", order_by="DatasetVersion.version_no"
    )


class DatasetVersion(Base):
    __tablename__ = "dataset_versions"
    __table_args__ = (UniqueConstraint("dataset_id", "version_no"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("datasets.id"), index=True)
    version_no: Mapped[int] = mapped_column(Integer)
    parent_version_id: Mapped[int | None] = mapped_column(ForeignKey("dataset_versions.id"))
    file_path: Mapped[str] = mapped_column(String(500))  # relative to STORAGE_DIR
    file_format: Mapped[str] = mapped_column(String(10))
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    size_bytes: Mapped[int] = mapped_column(Integer)
    row_count: Mapped[int] = mapped_column(Integer)
    col_count: Mapped[int] = mapped_column(Integer)
    schema_json: Mapped[list] = mapped_column(JSON)
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String(40), default=utcnow_iso)

    dataset: Mapped[Dataset] = relationship(back_populates="versions")


class Transformation(Base):
    __tablename__ = "transformations"
    id: Mapped[int] = mapped_column(primary_key=True)
    input_version_id: Mapped[int] = mapped_column(ForeignKey("dataset_versions.id"), index=True)
    output_version_id: Mapped[int] = mapped_column(ForeignKey("dataset_versions.id"), index=True)
    ops_json: Mapped[list] = mapped_column(JSON)
    created_at: Mapped[str] = mapped_column(String(40), default=utcnow_iso)


class QualityReport(Base):
    __tablename__ = "quality_reports"
    id: Mapped[int] = mapped_column(primary_key=True)
    version_id: Mapped[int] = mapped_column(ForeignKey("dataset_versions.id"), index=True)
    score: Mapped[float] = mapped_column(Float)
    report_json: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[str] = mapped_column(String(40), default=utcnow_iso)


class Experiment(Base):
    __tablename__ = "experiments"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    version_id: Mapped[int] = mapped_column(ForeignKey("dataset_versions.id"), index=True)
    task: Mapped[str] = mapped_column(String(20))
    algorithm: Mapped[str] = mapped_column(String(60))
    target_column: Mapped[str] = mapped_column(String(200))
    hyperparams_json: Mapped[dict] = mapped_column(JSON, default=dict)
    seed: Mapped[int] = mapped_column(Integer, default=42)
    test_size: Mapped[float] = mapped_column(Float, default=0.2)
    metrics_json: Mapped[dict | None] = mapped_column(JSON)
    env_json: Mapped[dict | None] = mapped_column(JSON)
    model_path: Mapped[str | None] = mapped_column(String(500))
    model_sha256: Mapped[str | None] = mapped_column(String(64))
    commitment_sha256: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20))
    error: Mapped[str | None] = mapped_column(Text)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    reproduced_from_id: Mapped[int | None] = mapped_column(ForeignKey("experiments.id"))
    created_at: Mapped[str] = mapped_column(String(40), default=utcnow_iso)

    version: Mapped[DatasetVersion] = relationship()


class ChainRecord(Base):
    __tablename__ = "chain_records"
    __table_args__ = (Index("ix_chain_artifact", "artifact_type", "artifact_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    artifact_type: Mapped[str] = mapped_column(String(30))
    artifact_id: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20))
    tx_hash: Mapped[str | None] = mapped_column(String(80))
    block_number: Mapped[int | None] = mapped_column(Integer)
    chain_id: Mapped[int | None] = mapped_column(Integer)
    contract_address: Mapped[str | None] = mapped_column(String(60))
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String(40), default=utcnow_iso)
    registered_at: Mapped[str | None] = mapped_column(String(40))


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_entity", "entity_type", "entity_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    event_type: Mapped[str] = mapped_column(String(60))
    entity_type: Mapped[str] = mapped_column(String(30))
    entity_id: Mapped[int] = mapped_column(Integer)
    payload_json: Mapped[dict] = mapped_column(JSON)
    prev_hash: Mapped[str] = mapped_column(String(64))
    event_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[str] = mapped_column(String(40))
