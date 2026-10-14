"""Lineage graph (versions + experiments) and downstream impact."""
from collections import deque

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ChainRecord, DatasetVersion, Experiment, QualityReport, Transformation
from app.services.transforms import summarize_ops

MAIN_METRIC = {"classification": "accuracy", "regression": "r2"}


def chain_status(db: Session, artifact_type: str, artifact_id: int) -> str:
    record = db.scalar(
        select(ChainRecord).where(ChainRecord.artifact_type == artifact_type, ChainRecord.artifact_id == artifact_id)
        .order_by(ChainRecord.id.desc()).limit(1)
    )
    return record.status if record else "unregistered"


def main_metric(exp: Experiment) -> dict | None:
    name = MAIN_METRIC.get(exp.task)
    value = (exp.metrics_json or {}).get(name) if name else None
    return {"name": name, "value": value} if value is not None else None


def _latest_scores(db: Session, version_ids: list[int]) -> dict[int, float]:
    rows = db.execute(
        select(QualityReport.version_id, QualityReport.score).where(QualityReport.version_id.in_(version_ids))
        .order_by(QualityReport.id)
    ).all()
    return {vid: score for vid, score in rows}  # later rows overwrite → latest wins


def version_node(db: Session, v: DatasetVersion, score: float | None) -> dict:
    return {"id": f"v{v.id}", "type": "version", "label": f"v{v.version_no}", "version_id": v.id,
            "sha_short": v.sha256[:12], "quality_score": score, "rows": v.row_count,
            "chain_status": chain_status(db, "dataset_version", v.id)}


def experiment_node(db: Session, e: Experiment) -> dict:
    return {"id": f"e{e.id}", "type": "experiment", "label": e.name, "experiment_id": e.id,
            "algorithm": e.algorithm, "status": e.status, "main_metric": main_metric(e),
            "chain_status": chain_status(db, "experiment", e.id)}


def dataset_graph(db: Session, dataset_id: int) -> dict:
    versions = list(db.scalars(
        select(DatasetVersion).where(DatasetVersion.dataset_id == dataset_id).order_by(DatasetVersion.version_no)
    ))
    ids = [v.id for v in versions]
    scores = _latest_scores(db, ids)
    nodes = [version_node(db, v, scores.get(v.id)) for v in versions]
    edges = [
        {"source": f"v{t.input_version_id}", "target": f"v{t.output_version_id}", "type": "derived",
         "label": summarize_ops(t.ops_json)}
        for t in db.scalars(select(Transformation).where(Transformation.output_version_id.in_(ids))
                            .order_by(Transformation.id))
    ]
    for e in db.scalars(select(Experiment).where(Experiment.version_id.in_(ids)).order_by(Experiment.id)):
        nodes.append(experiment_node(db, e))
        edges.append({"source": f"v{e.version_id}", "target": f"e{e.id}", "type": "trained_on", "label": None})
    return {"dataset_id": dataset_id, "nodes": nodes, "edges": edges}


def impact(db: Session, version: DatasetVersion) -> dict:
    """BFS downstream over derived versions and the experiments trained on any reached version."""
    depth = {version.id: 0}
    queue = deque([version.id])
    versions, experiments = [], []
    while queue:
        vid = queue.popleft()
        for e in db.scalars(select(Experiment).where(Experiment.version_id == vid).order_by(Experiment.id)):
            experiments.append({"id": e.id, "name": e.name, "version_id": vid, "algorithm": e.algorithm,
                                "status": e.status, "depth": depth[vid] + 1})
        for t in db.scalars(select(Transformation).where(Transformation.input_version_id == vid)
                            .order_by(Transformation.id)):
            child = t.output_version_id
            if child in depth:
                continue
            depth[child] = depth[vid] + 1
            v = db.get(DatasetVersion, child)
            versions.append({"id": v.id, "dataset_id": v.dataset_id, "version_no": v.version_no,
                             "parent_version_id": vid, "ops": summarize_ops(t.ops_json), "depth": depth[child]})
            queue.append(child)
    return {"version_id": version.id, "versions": versions, "experiments": experiments,
            "total": len(versions) + len(experiments)}
