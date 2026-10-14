from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


SessionLocal = sessionmaker(autoflush=False, expire_on_commit=False)
_engine: Engine | None = None


def _ensure_sqlite_dir(url: str) -> None:
    prefix = "sqlite:///"
    if url.startswith(prefix) and not url.endswith(":memory:"):
        Path(url[len(prefix):]).parent.mkdir(parents=True, exist_ok=True)


def _enable_sqlite_fks(dbapi_conn, _record) -> None:
    dbapi_conn.execute("PRAGMA foreign_keys=ON")


def init_db(url: str) -> Engine:
    """Create the engine, bind sessions to it and create all tables."""
    global _engine
    _ensure_sqlite_dir(url)
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    if _engine is not None:
        _engine.dispose()
    _engine = create_engine(url, connect_args=connect_args)
    if url.startswith("sqlite"):
        event.listen(_engine, "connect", _enable_sqlite_fks)
    SessionLocal.configure(bind=_engine)
    import app.models  # noqa: F401  (registers tables on Base.metadata)
    Base.metadata.create_all(_engine)
    return _engine


def dispose_db() -> None:
    global _engine
    if _engine is not None:
        _engine.dispose()
        _engine = None


def get_db() -> Iterator[Session]:
    with SessionLocal() as session:
        yield session


def db_ok() -> bool:
    try:
        with SessionLocal() as session:
            session.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
