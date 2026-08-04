"""FastAPI dependency providers.

`get_storage` is memoized per `(root, history_dirname)` — the per-page lock
table that makes `LocalFileStorage` safe under concurrent requests lives on
the instance, so handing out a fresh instance per request would silently
defeat the locking. `get_settings` (config.py) is itself `lru_cache`d, so in
practice this resolves to one storage instance per process.
"""

from functools import lru_cache
from typing import Annotated

from fastapi import Depends, Header

from wiki_api.config import Settings, get_settings
from wiki_api.storage.base import WikiStorage
from wiki_api.storage.local import LocalFileStorage


@lru_cache
def _local_storage(root: str, history_dirname: str) -> LocalFileStorage:
    return LocalFileStorage(root=root, history_dirname=history_dirname)


async def get_storage(settings: Annotated[Settings, Depends(get_settings)]) -> WikiStorage:
    # async, not sync: this does no I/O of its own (just an lru_cache
    # lookup), so it belongs on the event loop rather than costing a
    # threadpool slot. The blocking work happens later, inside the sync
    # route handlers that call into `storage`.
    return _local_storage(str(settings.root), settings.history_dirname)


async def get_author(x_wiki_author: Annotated[str | None, Header()] = None) -> str:
    """Resolves the acting author for a write from an `X-Wiki-Author` header.

    POC-scope only — there is no real authentication yet. Tracked as an open
    question on GPC-188; swap this dependency out once that's decided.
    """
    return x_wiki_author or "anonymous"
