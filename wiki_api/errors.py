"""Maps storage-layer exceptions to HTTP responses.

Centralizing this here keeps routers free of try/except boilerplate and
keeps `wiki_api.storage` importable/testable without any FastAPI dependency
(see `storage/exceptions.py`).
"""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from wiki_api.storage.exceptions import (
    InvalidPagePathError,
    PageConflictError,
    PageNotFoundError,
    VersionNotFoundError,
)


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(PageNotFoundError)
    async def _handle_not_found(request: Request, exc: PageNotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(VersionNotFoundError)
    async def _handle_version_not_found(request: Request, exc: VersionNotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(InvalidPagePathError)
    async def _handle_invalid_path(request: Request, exc: InvalidPagePathError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.exception_handler(PageConflictError)
    async def _handle_conflict(request: Request, exc: PageConflictError) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={
                "detail": str(exc),
                "expected_version": exc.expected_version,
                "current_version": exc.current_version,
            },
        )
