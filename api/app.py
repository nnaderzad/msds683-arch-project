"""FastAPI app for serving event-demand gold data (and the built web dashboard)."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from api.feedback import client_hash, get_sink
from api.gold import get_repository
from api.text2sql import RateLimiter, get_service, rate_limiter

logger = logging.getLogger(__name__)


def _client_key(request: Request) -> str:
    """Best-effort client identity for rate limiting (first X-Forwarded-For hop)."""
    forwarded = request.headers.get("x-forwarded-for", "")
    return forwarded.split(",")[0].strip() or (
        request.client.host if request.client else "unknown"
    )


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


@app.get("/genres")
def genres() -> list[str]:
    """Distinct primary genres, for the search panel's dropdown."""
    return get_repository().genres()


@app.get("/search")
def search_shows(
    q: str | None = Query(default=None, max_length=80),
    genre: str | None = Query(default=None, max_length=40),
    state: str | None = Query(default=None, max_length=2),
    dma: str | None = Query(default=None, max_length=5),
    max_price: float | None = Query(default=None, ge=0, le=100000),
    days_ahead: int | None = Query(default=None, ge=0, le=365),
    limit: int = Query(default=25, ge=1, le=100),
) -> list[dict[str, Any]]:
    """Filtered show summaries for the dashboard's manual search fields."""
    return get_repository().search(
        q=q, genre=genre, state=state, dma=dma,
        max_price=max_price, days_ahead=days_ahead, limit=limit,
    )


@app.get("/show/{event_id}")
def get_show(event_id: str) -> dict[str, Any]:
    """Return one show summary plus history and forecast series."""
    show = get_repository().get_show(event_id)
    if show is not None:
        return show

    raise HTTPException(status_code=404, detail=f"Show not found: {event_id}")


class AskTurn(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    answer: str = Field(max_length=2000)


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=500)
    # "real" = the honest star schema; "synth" = the clearly-labeled synthetic
    # sandbox (event_demand_synth) with sellout/resale infill.
    dataset: Literal["real", "synth"] = "real"
    # Prior completed exchanges (older first) so follow-up questions resolve.
    history: list[AskTurn] = Field(default_factory=list, max_length=3)


@app.post("/ask")
def ask(req: AskRequest, request: Request) -> dict[str, Any]:
    """Text-to-SQL agent: natural-language question -> guardrailed SQL -> answer.

    Always returns HTTP 200 with a ``status`` discriminator
    (ok | refused | blocked | rate_limited | error) so the demo UI renders
    blocked/refused attempts as first-class outcomes, never a raw 500.
    """
    limited = rate_limiter.check(_client_key(request))
    if limited is not None:
        return {"status": "rate_limited", "question": req.question, "answer": limited}
    return get_service(req.dataset).ask(
        req.question, history=[turn.model_dump() for turn in req.history]
    )


class AskFeedback(BaseModel):
    verdict: Literal["up", "down"]
    question: str = Field(min_length=1, max_length=500)
    sql: str | None = Field(default=None, max_length=6000)
    answer: str | None = Field(default=None, max_length=2000)
    dataset: str = Field(default="real", max_length=10)
    model: str | None = Field(default=None, max_length=100)
    status: str | None = Field(default=None, max_length=20)
    latency_ms: int | None = Field(default=None, ge=0)
    bytes_processed: int | None = Field(default=None, ge=0)


# Looser than the /ask limiter (feedback is cheap) but still bounded; separate
# instance so voting never consumes the question budget.
feedback_limiter = RateLimiter(per_minute=12, daily_cap=1000)


@app.post("/ask_feedback")
def ask_feedback(fb: AskFeedback, request: Request) -> dict[str, str]:
    """Record a thumbs-up/down on an /ask answer (fire-and-forget from the UI).

    Rows land in the separate ops dataset (see api/feedback.py) and are mined
    offline into the eval set — always HTTP 200, never a raw 500.
    """
    key = _client_key(request)
    limited = feedback_limiter.check(key)
    if limited is not None:
        return {"status": "rate_limited"}
    row = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "client_hash": client_hash(key),
        **fb.model_dump(),
    }
    try:
        get_sink().record(row)
    except Exception:  # noqa: BLE001 - losing one vote is fine; failing the UI is not
        logger.exception("ask_feedback insert failed")
        return {"status": "error"}
    return {"status": "ok"}


# Serve the built web dashboard from the same origin, when present (the Docker image copies
# the Vite build into ./static). Mounted LAST so the API routes above always win; the SPA
# and its assets are served for every other path. No-op for local API-only dev.
_static = Path(__file__).parent / "static"
if _static.exists():
    app.mount("/", StaticFiles(directory=_static, html=True), name="web")
