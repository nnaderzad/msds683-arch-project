"""FastAPI app for serving event-demand gold data (and the built web dashboard)."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from api.gold import get_repository

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Pre-warm the gold repository so the first user request isn't a cold SELECT *.

    Pairs with Cloud Run ``--min-instances 1``: the one warm instance loads gold once at
    startup. A transient BigQuery hiccup must not block startup — fall back to lazy load.
    """
    try:
        get_repository()
    except Exception:  # noqa: BLE001 - never fail startup on a transient gold-load error
        logger.exception("Gold pre-warm failed; falling back to lazy load on first request.")
    yield


app = FastAPI(
    title="Event Demand API",
    description="API for show-level demand signals and precomputed price forecasts.",
    version="0.2.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, str]:
    """Return a simple health check for local and deployed smoke tests."""
    return {"status": "ok"}


@app.get("/shows")
def list_shows() -> list[dict[str, Any]]:
    """Return show summaries for the frontend dropdown."""
    return get_repository().list_shows()


@app.get("/show/{event_id}")
def get_show(event_id: str) -> dict[str, Any]:
    """Return one show summary plus history and forecast series."""
    show = get_repository().get_show(event_id)
    if show is not None:
        return show

    raise HTTPException(status_code=404, detail=f"Show not found: {event_id}")


# Serve the built web dashboard from the same origin, when present (the Docker image copies
# the Vite build into ./static). Mounted LAST so the API routes above always win; the SPA
# and its assets are served for every other path. No-op for local API-only dev.
_static = Path(__file__).parent / "static"
if _static.exists():
    app.mount("/", StaticFiles(directory=_static, html=True), name="web")
