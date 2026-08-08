"""FastAPI app for serving event-demand gold data (and the built web dashboard)."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from api.gold import get_repository
from api.text2sql import get_service, rate_limiter

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


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=500)


@app.post("/ask")
def ask(req: AskRequest, request: Request) -> dict[str, Any]:
    """Text-to-SQL agent: natural-language question -> guardrailed SQL -> answer.

    Always returns HTTP 200 with a ``status`` discriminator
    (ok | refused | blocked | rate_limited | error) so the demo UI renders
    blocked/refused attempts as first-class outcomes, never a raw 500.
    """
    forwarded = request.headers.get("x-forwarded-for", "")
    client_key = forwarded.split(",")[0].strip() or (
        request.client.host if request.client else "unknown"
    )
    limited = rate_limiter.check(client_key)
    if limited is not None:
        return {"status": "rate_limited", "question": req.question, "answer": limited}
    return get_service().ask(req.question)


# Serve the built web dashboard from the same origin, when present (the Docker image copies
# the Vite build into ./static). Mounted LAST so the API routes above always win; the SPA
# and its assets are served for every other path. No-op for local API-only dev.
_static = Path(__file__).parent / "static"
if _static.exists():
    app.mount("/", StaticFiles(directory=_static, html=True), name="web")
