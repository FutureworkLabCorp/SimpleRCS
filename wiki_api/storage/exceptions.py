"""Exceptions raised by the wiki storage layer.

Kept independent of any web framework — `wiki_api.errors` maps these to
HTTP responses, so the storage layer stays importable and unit-testable
without FastAPI in the loop.
"""


class WikiStorageError(Exception):
    """Base class for all storage-layer errors."""


class PageNotFoundError(WikiStorageError):
    def __init__(self, path: str) -> None:
        self.path = path
        super().__init__(f"Page not found: {path!r}")


class VersionNotFoundError(WikiStorageError):
    def __init__(self, path: str, version: str) -> None:
        self.path = path
        self.version = version
        super().__init__(f"Version {version!r} not found for page {path!r}")


class InvalidPagePathError(WikiStorageError):
    def __init__(self, path: str, reason: str) -> None:
        self.path = path
        self.reason = reason
        super().__init__(f"Invalid page path {path!r}: {reason}")


class PageConflictError(WikiStorageError):
    """Raised when a write's `If-Match` / `If-None-Match` precondition fails."""

    def __init__(self, path: str, expected_version: str | None, current_version: str | None) -> None:
        self.path = path
        self.expected_version = expected_version
        self.current_version = current_version
        super().__init__(
            f"Version conflict on {path!r}: expected {expected_version!r}, found {current_version!r}",
        )
