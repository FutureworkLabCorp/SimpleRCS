"""FastAPI application entrypoint for the wiki backend (GPC-188).

Run locally:
    uv run --extra api python -m uvicorn wiki_api.main:app --reload

Point it at a specific storage root (defaults to ./wiki_data):
    WIKI_ROOT=/path/to/wiki/data uv run --extra api python -m uvicorn wiki_api.main:app --reload
"""

from fastapi import FastAPI

from wiki_api.errors import register_exception_handlers
from wiki_api.routers.pages import router as pages_router

app = FastAPI(
    title="SimpleRCS Wiki API",
    description="File-system-backed wiki, version-controlled per page with SimpleRCS (GPC-188 POC).",
    version="0.1.0",
)

register_exception_handlers(app)
app.include_router(pages_router)


@app.get("/health", tags=["meta"])
async def health() -> dict[str, str]:
    return {"status": "ok"}
