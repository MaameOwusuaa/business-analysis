"""Application settings.

All configuration comes from environment variables (optionally loaded from a
local ``.env`` file that is git-ignored). Nothing secret is hard-coded; see
``.env.example`` for the full list of variables.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = Path(__file__).resolve().parent

try:  # python-dotenv is optional at runtime
    from dotenv import dotenv_values, load_dotenv

    load_dotenv(PROJECT_ROOT / ".env")
except ImportError:  # pragma: no cover
    dotenv_values = None
SQL_DIR = PACKAGE_ROOT / "sql"
USECASE_DIR = PACKAGE_ROOT / "usecases"


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    database_url: str
    storage_dir: Path
    log_level: str
    # Snwolley Agent API Toolkit
    snwolley_mode: str  # "live" or "mock"
    snwolley_base_url: str | None
    snwolley_chat_path: str
    snwolley_agent_id: str | None
    snwolley_response_path: str
    snwolley_timeout_s: float
    snwolley_fallback_to_mock: bool
    # Internal REST API
    api_token: str | None = field(default=None, repr=False)
    snwolley_api_key: str | None = field(default=None, repr=False)

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    storage = Path(os.getenv("STORAGE_DIR", PROJECT_ROOT / "storage")).resolve()
    storage.mkdir(parents=True, exist_ok=True)
    default_db = f"sqlite:///{storage / 'insightforge.db'}"
    mode = os.getenv("SNWOLLEY_MODE", "mock").strip().lower()
    if mode not in {"live", "mock"}:
        raise ValueError("SNWOLLEY_MODE must be 'live' or 'mock'")
    database_url = os.getenv("DATABASE_URL", default_db)
    template_markers = ("<password>", "<host>", "<username>", "<database>", "replace-with-password")
    if any(value in database_url for value in template_markers) and dotenv_values is not None:
        file_url = dotenv_values(PROJECT_ROOT / ".env").get("DATABASE_URL")
        if file_url and not any(value in file_url for value in template_markers):
            database_url = file_url
    if any(value in database_url for value in template_markers):
        raise ValueError(
            "DATABASE_URL still contains a template value. Replace the placeholder values in "
            ".env, or export a valid MySQL connection URL."
        )
    return Settings(
        database_url=database_url,
        storage_dir=storage,
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        snwolley_mode=mode,
        snwolley_base_url=os.getenv("SNWOLLEY_BASE_URL") or None,
        snwolley_chat_path=os.getenv("SNWOLLEY_CHAT_PATH", "/v1/agent/chat"),
        snwolley_agent_id=os.getenv("SNWOLLEY_AGENT_ID") or None,
        snwolley_response_path=os.getenv("SNWOLLEY_RESPONSE_PATH", "output"),
        snwolley_timeout_s=float(os.getenv("SNWOLLEY_TIMEOUT_S", "45")),
        snwolley_fallback_to_mock=_bool(os.getenv("SNWOLLEY_FALLBACK_TO_MOCK"), True),
        api_token=os.getenv("INSIGHTFORGE_API_TOKEN") or None,
        snwolley_api_key=os.getenv("SNWOLLEY_API_KEY") or None,
    )
