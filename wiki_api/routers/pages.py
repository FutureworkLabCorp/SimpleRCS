"""`/pages` — page CRUD, history, versions, and diff.

Two things to keep in mind before touching this file:

1. Handlers are plain `def`, not `async def`. `WikiStorage` does blocking
   file I/O (SimpleRCS operates on real file handles), and FastAPI's
   documented way to host blocking code is a sync path operation function —
   FastAPI runs those in a threadpool automatically instead of blocking the
   event loop. See https://fastapi.tiangolo.com/async/#path-operation-functions.

2. Route registration order matters. `{path:path}` is a greedy converter
   that matches slashes, so `GET /pages/{path:path}` would happily also
   match `/pages/foo/history` if it were registered before the `/history`
   route — Starlette takes the first full (path + method) match it finds
   while scanning routes in registration order. The `/history`,
   `/versions/{version}`, and `/diff` routes are therefore declared *before*
   the bare `GET /pages/{path:path}` below; don't reorder them.

   Registration order alone isn't sufficient for `/versions/{version}`,
   though: unlike the literal `/history`/`/diff` suffixes (which a valid
   `*.md` page path can never end with), the default `{version}` segment
   matches any non-slash text — including one ending in `.md` — so it could
   still swallow a genuine page path containing a `versions/` segment (e.g.
   `notes/versions/1.0.md`) ahead of the bare route below. The custom
   `version` path converter registered below closes that gap by requiring
   `{version}` to actually look like a SimpleRCS version string.
"""

from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Response
from starlette.convertors import StringConvertor, register_url_convertor

from wiki_api.dependencies import get_author, get_storage
from wiki_api.schemas import DiffRead, HistoryEntryRead, PageRead, PageSummary, PageWriteRequest
from wiki_api.storage.base import WikiStorage


class _VersionConvertor(StringConvertor):
    """Restricts a `{version}` path segment to SimpleRCS's `N.N` format.

    SimpleRCS version strings are always `int.int` (see `SimpleRCS.commit()`'s
    version-increment logic — it only ever bumps the last `.`-separated
    component), so this can never accidentally match a real page path, which
    always ends in `.md`.
    """

    regex = r"\d+\.\d+"


register_url_convertor("version", _VersionConvertor())

router = APIRouter(prefix="/pages", tags=["pages"])


@router.get("")
def list_pages(storage: Annotated[WikiStorage, Depends(get_storage)]) -> list[PageSummary]:
    return [PageSummary(**asdict(page)) for page in storage.list_pages()]


# -- suffixed routes: must come before the bare "/{path:path}" read below --


@router.get("/{path:path}/history")
def get_history(
    path: str,
    storage: Annotated[WikiStorage, Depends(get_storage)],
    limit: int | None = None,
) -> list[HistoryEntryRead]:
    return [HistoryEntryRead(**asdict(entry)) for entry in storage.get_history(path, limit=limit)]


@router.get("/{path:path}/versions/{version:version}")
def get_version(
    path: str,
    version: str,
    storage: Annotated[WikiStorage, Depends(get_storage)],
) -> PageRead:
    return PageRead(**asdict(storage.get_version(path, version)))


@router.get("/{path:path}/diff")
def diff_versions(
    path: str,
    a: str,
    b: str,
    storage: Annotated[WikiStorage, Depends(get_storage)],
) -> DiffRead:
    diff_text = storage.diff_versions(path, a, b)
    return DiffRead(path=path, version_a=a, version_b=b, diff=diff_text)


# -- bare page routes -------------------------------------------------------


@router.get("/{path:path}")
def read_page(
    path: str,
    response: Response,
    storage: Annotated[WikiStorage, Depends(get_storage)],
) -> PageRead:
    page = storage.read_page(path)
    response.headers["ETag"] = f'"{page.version}"'  # RFC 7232 entity-tag = quoted-string
    return PageRead(**asdict(page))


@router.put("/{path:path}")
def write_page(
    path: str,
    body: PageWriteRequest,
    response: Response,
    storage: Annotated[WikiStorage, Depends(get_storage)],
    author: Annotated[str, Depends(get_author)],
    if_match: Annotated[str | None, Header()] = None,
    if_none_match: Annotated[str | None, Header()] = None,
) -> PageRead:
    # Cosmetic only (201 vs 200) — not a concurrency check. The real
    # create-only / update-only guarantees come from if_match/if_none_match
    # plus the per-page lock inside storage.write_page().
    existed = storage.page_exists(path)
    page = storage.write_page(
        path,
        body.content,
        author=author,
        message=body.message,
        if_match=if_match,
        if_none_match=if_none_match,
    )
    response.status_code = 200 if existed else 201
    response.headers["ETag"] = f'"{page.version}"'  # RFC 7232 entity-tag = quoted-string
    return PageRead(**asdict(page))


@router.delete("/{path:path}", status_code=204)
def delete_page(
    path: str,
    storage: Annotated[WikiStorage, Depends(get_storage)],
    author: Annotated[str, Depends(get_author)],
) -> None:
    storage.delete_page(path, author=author)
