import mimetypes
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import Settings, get_settings
from app.db import db_ok, dispose_db, init_db
from app.routers import audit, chain, datasets, dev, experiments, lineage, meta, quality, reports, versions
from app.services import chain as chain_service
from app.services.loader import LoadError
from app.services.ml import ExperimentError
from app.services.transforms import TransformError
from app.services.versions import ConflictError


def _register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(LoadError)
    async def load_error(_: Request, exc: LoadError):
        return JSONResponse({"detail": str(exc)}, status_code=422)

    @app.exception_handler(TransformError)
    @app.exception_handler(ExperimentError)
    async def unprocessable(_: Request, exc: Exception):
        return JSONResponse({"detail": str(exc)}, status_code=422)

    @app.exception_handler(ConflictError)
    async def conflict_error(_: Request, exc: ConflictError):
        return JSONResponse({"detail": str(exc)}, status_code=409)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings.storage_dir.mkdir(parents=True, exist_ok=True)
        init_db(settings.database_url)
        chain_service.resync_on_startup(settings)  # re-anchor records lost to a Hardhat node restart
        yield
        dispose_db()

    app = FastAPI(title="DataOrigin", lifespan=lifespan)
    app.state.settings = settings
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    _register_error_handlers(app)

    api = APIRouter(prefix="/api")

    @api.get("/health")
    def health(request: Request):
        return {
            "status": "ok",
            "db": "ok" if db_ok() else "error",
            "chain": chain_service.health(request.app.state.settings),
        }

    for module in (datasets, versions, quality, lineage, experiments, chain, reports, meta, audit, dev):
        api.include_router(module.router)
    app.include_router(api)

    # Mounted last so /api routes take precedence. Pin MIME types: the Windows registry can map .js to
    # text/plain, which browsers refuse for ES modules.
    mimetypes.add_type("text/javascript", ".js")
    mimetypes.add_type("text/css", ".css")
    if settings.frontend_dir.is_dir():
        app.mount("/", StaticFiles(directory=settings.frontend_dir, html=True), name="frontend")
    return app


app = create_app()
