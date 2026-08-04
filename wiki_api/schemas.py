"""Pydantic request/response models for the `/pages` API."""

from pydantic import BaseModel, Field


class PageSummary(BaseModel):
    """One row of the `GET /pages` listing."""

    path: str
    version: str
    author: str
    date: str


class PageRead(BaseModel):
    path: str
    content: str
    version: str
    author: str
    date: str
    message: str = ""


#: Placeholder POC-scope cap (~20MB of text) so a single PUT can't force the
#: server to buffer an unbounded body in memory before anything gets to
#: reject it. A real deployment likely also wants an ASGI/reverse-proxy-level
#: body-size limit in front of this (Pydantic only sees the body after
#: Starlette has already read and decoded it), which is out of scope here.
_MAX_CONTENT_CHARS = 20_000_000


class PageWriteRequest(BaseModel):
    content: str = Field(max_length=_MAX_CONTENT_CHARS)
    message: str = Field(default="", max_length=2000, description="Optional commit message describing the edit.")


class HistoryEntryRead(BaseModel):
    version: str
    author: str
    date: str
    message: str = ""


class DiffRead(BaseModel):
    path: str
    version_a: str
    version_b: str
    diff: str
