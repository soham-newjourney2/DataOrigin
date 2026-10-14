from fastapi import APIRouter

from app.services.ml import algorithms_meta
from app.services.transforms import OPS

router = APIRouter(tags=["meta"])


@router.get("/meta/algorithms")
def algorithms():
    """Allowed ML algorithms (with default hyperparams) and transform ops."""
    return {
        "transform_ops": [{"op": name, **spec} for name, spec in OPS.items()],
        "algorithms": algorithms_meta(),
    }
