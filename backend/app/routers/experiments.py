from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, current_settings
from app.db import get_db
from app.models import Experiment
from app.routers.versions import get_version_or_404
from app.schemas import ExperimentIn, ExperimentOut, ReproduceOut
from app.services import audit, ml, storage
from app.services.hashing import sha256_file

router = APIRouter(tags=["experiments"])


def get_experiment_or_404(db: Session, experiment_id: int) -> Experiment:
    exp = db.get(Experiment, experiment_id)
    if exp is None:
        raise HTTPException(404, f"experiment {experiment_id} not found")
    return exp


def _failed(exp: Experiment) -> JSONResponse:
    return JSONResponse(status_code=400, content={
        "detail": f"training failed: {exp.error}", "experiment": ExperimentOut.model_validate(exp).model_dump()})


def _file_matches(settings: Settings, version) -> bool:
    path = storage.abs_path(settings.storage_dir, version.file_path)
    return path.is_file() and sha256_file(path) == version.sha256


@router.post("/experiments", response_model=ExperimentOut, status_code=201,
             responses={400: {"description": "training failed (run saved with status=failed)"}})
def create_experiment(body: ExperimentIn, db: Session = Depends(get_db),
                      settings: Settings = Depends(current_settings)):
    version = get_version_or_404(db, body.version_id)
    if not _file_matches(settings, version):
        raise HTTPException(409, f"stored file of version {version.id} no longer matches its sha256")
    exp = ml.run_experiment(db, settings.storage_dir, version, body.name.strip(), body.task, body.algorithm,
                            body.target_column, body.hyperparams, body.seed, body.test_size)
    return _failed(exp) if exp.status == "failed" else exp


@router.get("/experiments", response_model=list[ExperimentOut])
def list_experiments(version_id: int | None = None, db: Session = Depends(get_db)):
    query = select(Experiment).order_by(Experiment.id.desc())
    if version_id is not None:
        query = query.where(Experiment.version_id == version_id)
    return list(db.scalars(query))


@router.get("/experiments/compare")
def compare_experiments(ids: str = Query(..., description="comma-separated, e.g. 1,2,3"),
                        db: Session = Depends(get_db)):
    try:
        id_list = list(dict.fromkeys(int(i) for i in ids.split(",") if i.strip()))
    except ValueError:
        raise HTTPException(422, "ids must be comma-separated integers") from None
    if len(id_list) < 2:
        raise HTTPException(422, "need at least 2 distinct experiment ids")
    return ml.compare([get_experiment_or_404(db, i) for i in id_list])


@router.get("/experiments/{experiment_id}", response_model=ExperimentOut)
def get_experiment(experiment_id: int, db: Session = Depends(get_db)):
    return get_experiment_or_404(db, experiment_id)


@router.post("/experiments/{experiment_id}/reproduce", response_model=ReproduceOut, status_code=201,
             responses={400: {"description": "re-run failed (saved with status=failed)"}})
def reproduce_experiment(experiment_id: int, db: Session = Depends(get_db),
                         settings: Settings = Depends(current_settings)):
    """Re-run with the identical config on the same version. Runs even if the file changed, and reports it."""
    original = get_experiment_or_404(db, experiment_id)
    if original.status != "completed":
        raise HTTPException(409, "only completed experiments can be reproduced")
    version = original.version
    hash_match = _file_matches(settings, version)
    new = ml.run_experiment(db, settings.storage_dir, version, f"{original.name} (repro)", original.task,
                            original.algorithm, original.target_column, original.hyperparams_json, original.seed,
                            original.test_size, reproduced_from_id=original.id)
    if new.status == "failed":
        return _failed(new)
    match, diffs = ml.metric_diffs(original.metrics_json, new.metrics_json)
    audit.log_event(db, "experiment.reproduced", "experiment", original.id, {
        "new_experiment_id": new.id, "match": match, "dataset_hash_match": hash_match})
    db.commit()
    return {"original_id": original.id, "new_experiment_id": new.id, "match": match, "metric_diffs": diffs,
            "dataset_hash_match": hash_match, "experiment": new}
