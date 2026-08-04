"""Storage-layer abstraction the routers depend on.

`LocalFileStorage` is the only implementation today (POC scope — GPC-186).
The interface is kept narrow and *synchronous* on purpose: FastAPI's
documented way to host blocking I/O (SimpleRCS operates on real file
handles) is a plain `def` path operation function, which FastAPI runs in a
threadpool automatically instead of blocking the event loop — see
https://fastapi.tiangolo.com/async/#path-operation-functions. Keeping this
interface sync means callers don't need to think about that; they just
avoid declaring their route as `async def`.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class PageContent:
    """A single version of a page, including its content."""

    path: str
    content: str
    version: str
    author: str
    date: str
    message: str = ""


@dataclass(frozen=True)
class PageInfo:
    """A page's identity, without content — used for tree/listing views."""

    path: str
    version: str
    author: str
    date: str


@dataclass(frozen=True)
class HistoryEntry:
    """One row of a page's edit history (a SimpleRCS `log()` entry)."""

    version: str
    author: str
    date: str
    message: str = ""


class WikiStorage(ABC):
    """Storage backend contract used by the `/pages` router."""

    @abstractmethod
    def list_pages(self) -> list[PageInfo]: ...

    @abstractmethod
    def page_exists(self, path: str) -> bool: ...

    @abstractmethod
    def read_page(self, path: str) -> PageContent: ...

    @abstractmethod
    def write_page(
        self,
        path: str,
        content: str,
        *,
        author: str,
        message: str = "",
        if_match: str | None = None,
        if_none_match: str | None = None,
    ) -> PageContent:
        """Creates or updates a page.

        `if_match`: if given, the write is rejected with `PageConflictError`
        unless it equals the page's current version (optimistic concurrency
        on update).
        `if_none_match`: if given as `"*"`, the write is rejected with
        `PageConflictError` if the page already exists (create-only).
        """
        ...

    @abstractmethod
    def delete_page(self, path: str, *, author: str) -> None:
        """Removes the live page.

        Its `.srcs` history is intentionally kept (with a tombstone commit)
        so `get_history()` still reflects the deletion — see GPC-188 notes.
        """
        ...

    @abstractmethod
    def get_history(self, path: str, limit: int | None = None) -> list[HistoryEntry]: ...

    @abstractmethod
    def get_version(self, path: str, version: str) -> PageContent: ...

    @abstractmethod
    def diff_versions(self, path: str, version_a: str, version_b: str) -> str: ...
