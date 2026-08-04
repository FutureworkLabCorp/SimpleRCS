"""Shared fixtures for wiki_api tests."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from wiki_api.config import Settings, get_settings
from wiki_api.dependencies import _local_storage
from wiki_api.main import app


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    """A TestClient wired to an isolated, per-test storage root.

    Overrides `get_settings` (FastAPI's documented pattern — see
    https://fastapi.tiangolo.com/advanced/settings/#settings-in-a-dependency)
    rather than env vars, and clears the `_local_storage` lru_cache so each
    test also gets its own `LocalFileStorage` instance (and lock table).
    """
    _local_storage.cache_clear()

    def _override_settings() -> Settings:
        return Settings(root=tmp_path / "wiki", history_dirname=".srcs")

    app.dependency_overrides[get_settings] = _override_settings
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.clear()
        _local_storage.cache_clear()
