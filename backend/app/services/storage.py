from pathlib import Path


def version_rel_path(dataset_id: int, version_no: int, fmt: str) -> str:
    return f"datasets/{dataset_id}/v{version_no}.{fmt}"


def abs_path(storage_dir: Path, rel_path: str) -> Path:
    return Path(storage_dir) / rel_path


def save_version_file(storage_dir: Path, dataset_id: int, version_no: int, fmt: str, data: bytes) -> str:
    """Write a new immutable file ('xb' refuses to overwrite). Returns the path relative to storage_dir."""
    rel = version_rel_path(dataset_id, version_no, fmt)
    path = abs_path(storage_dir, rel)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "xb") as f:
        f.write(data)
    return rel


def model_rel_path(experiment_id: int) -> str:
    return f"models/{experiment_id}.joblib"
