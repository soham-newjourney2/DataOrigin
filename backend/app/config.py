"""Settings: defaults < backend/.env < process environment. Relative paths resolve against backend/."""
import os
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import Request

BACKEND_DIR = Path(__file__).resolve().parent.parent

DEFAULTS = {
    "DATABASE_URL": "sqlite:///./dataorigin.db",
    "STORAGE_DIR": "../storage",
    "MAX_UPLOAD_MB": "20",
    "CHAIN_ENABLED": "true",
    "CHAIN_RPC_URL": "http://127.0.0.1:8545",
    "CHAIN_DEPLOYMENT_FILE": "app/chain_deployment.json",
    "DEMO_MODE": "true",
    "CORS_ORIGINS": "http://localhost:8000",
    "FRONTEND_DIR": "../frontend",
    # Public-network mode (e.g. Sepolia). Leave empty for the local Hardhat setup.
    "CHAIN_CONTRACT_ADDRESS": "",   # set → use this address + the bundled ABI instead of CHAIN_DEPLOYMENT_FILE
    "CHAIN_PRIVATE_KEY": "",        # set → sign transactions with this key instead of the node's unlocked account
    "CHAIN_START_BLOCK": "0",       # block the contract was deployed in (bounds event-log searches)
    "CHAIN_RECEIPT_TIMEOUT": "120",  # seconds to wait for a transaction to be mined
}


@dataclass(frozen=True)
class Settings:
    database_url: str
    storage_dir: Path
    max_upload_mb: int
    chain_enabled: bool
    chain_rpc_url: str
    chain_deployment_file: Path
    demo_mode: bool
    cors_origins: list[str]
    frontend_dir: Path
    chain_contract_address: str = ""
    chain_private_key: str = field(default="", repr=False)  # never printed or logged
    chain_start_block: int = 0
    chain_receipt_timeout: int = 120


def parse_env_file(path: Path) -> dict[str, str]:
    """Minimal KEY=VALUE parser: skips blanks and # comments, strips optional quotes."""
    if not path.is_file():
        return {}
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("'\"")
    return values


def resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (BACKEND_DIR / path).resolve()


def resolve_database_url(url: str) -> str:
    """Make a relative sqlite path absolute (relative to backend/) so cwd doesn't matter."""
    prefix = "sqlite:///"
    if not url.startswith(prefix) or url.endswith(":memory:"):
        return url
    return prefix + resolve_path(url[len(prefix):]).as_posix()


def _as_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def get_settings() -> Settings:
    raw = {**DEFAULTS, **parse_env_file(BACKEND_DIR / ".env")}
    raw.update({k: os.environ[k] for k in DEFAULTS if k in os.environ})
    return Settings(
        database_url=resolve_database_url(raw["DATABASE_URL"]),
        storage_dir=resolve_path(raw["STORAGE_DIR"]),
        max_upload_mb=int(raw["MAX_UPLOAD_MB"]),
        chain_enabled=_as_bool(raw["CHAIN_ENABLED"]),
        chain_rpc_url=raw["CHAIN_RPC_URL"],
        chain_deployment_file=resolve_path(raw["CHAIN_DEPLOYMENT_FILE"]),
        demo_mode=_as_bool(raw["DEMO_MODE"]),
        cors_origins=[o.strip() for o in raw["CORS_ORIGINS"].split(",") if o.strip()],
        frontend_dir=resolve_path(raw["FRONTEND_DIR"]),
        chain_contract_address=raw["CHAIN_CONTRACT_ADDRESS"].strip(),
        chain_private_key=raw["CHAIN_PRIVATE_KEY"].strip(),
        chain_start_block=int(raw["CHAIN_START_BLOCK"] or 0),
        chain_receipt_timeout=int(raw["CHAIN_RECEIPT_TIMEOUT"] or 120),
    )


def current_settings(request: Request) -> Settings:
    """FastAPI dependency: the settings the running app was built with."""
    return request.app.state.settings
