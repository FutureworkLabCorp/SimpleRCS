"""Runtime configuration for the wiki API, overridable via `WIKI_*` env vars."""

from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings, sourced from environment variables (prefix `WIKI_`).

    Example:
        WIKI_ROOT=/data/wiki uv run python -m uvicorn wiki_api.main:app
    """

    model_config = SettingsConfigDict(env_prefix="WIKI_")

    root: Path = Path("./wiki_data")
    """Directory holding the live `.md` pages that make up the wiki tree."""

    history_dirname: str = ".srcs"
    """Hidden directory (relative to `root`) mirroring `root`'s layout, holding
    one SimpleRCS `.srcs` history file per page. Kept out of `root` itself so
    `root` stays a clean, browsable file tree — see GPC-185 (open decision)."""

    @field_validator("history_dirname")
    @classmethod
    def _history_dirname_must_be_a_single_hidden_segment(cls, value: str) -> str:
        # LocalFileStorage._normalize_rel_path only ever rejects page path
        # segments that start with '.', on the assumption that history_dirname
        # is one of them (that's what keeps a page from ever aliasing into the
        # history tree — see local.py's list_pages() exclusion filter). A
        # dirname that doesn't start with '.', or that isn't a single path
        # segment, would silently break that assumption instead of failing
        # loudly, so reject it here at config-load time.
        if not value.startswith(".") or "/" in value or value in (".", ".."):
            raise ValueError("history_dirname must be a single hidden path segment, e.g. '.srcs'")
        return value


@lru_cache
def get_settings() -> Settings:
    """Cached settings accessor, used as a FastAPI dependency.

    Tests override this via `app.dependency_overrides[get_settings]` rather
    than mutating env vars, so the cache never leaks state between tests.
    """
    return Settings()
