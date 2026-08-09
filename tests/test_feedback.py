"""Offline tests for POST /ask_feedback and the injectable feedback sink."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api import feedback as fb
from api.app import app, feedback_limiter
from api.gold import GoldFrames, GoldRepository, set_repository


class CapturingSink:
    def __init__(self, fail: bool = False):
        self.rows: list[dict] = []
        self.fail = fail

    def record(self, row: dict) -> None:
        if self.fail:
            raise RuntimeError("insert exploded")
        self.rows.append(row)


@pytest.fixture(autouse=True)
def offline_app_state():
    empty = pd.DataFrame()
    set_repository(GoldRepository(GoldFrames(empty, empty, empty, empty, empty)))
    sink = CapturingSink()
    fb.set_sink(sink)
    yield sink
    fb.set_sink(None)
    set_repository(None)


@pytest.fixture
def client():
    return TestClient(app)


def test_feedback_recorded_with_hash_not_ip(client, offline_app_state):
    response = client.post(
        "/ask_feedback",
        json={
            "verdict": "down",
            "question": "What EDM shows are you tracking in San Francisco?",
            "sql": "SELECT 1",
            "answer": "There are none.",
            "dataset": "real",
            "model": "gemini-2.5-flash",
            "status": "ok",
            "latency_ms": 2100,
            "bytes_processed": 1024,
        },
        headers={"x-forwarded-for": "203.0.113.9"},
    )
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    row = offline_app_state.rows[0]
    assert row["verdict"] == "down"
    assert row["question"].startswith("What EDM shows")
    assert row["client_hash"] == fb.client_hash("203.0.113.9")
    assert "203.0.113.9" not in str(row)  # never store the raw IP
    assert row["ts"].endswith("+00:00")  # UTC timestamp


def test_feedback_minimal_payload_ok(client, offline_app_state):
    response = client.post(
        "/ask_feedback", json={"verdict": "up", "question": "cheapest Everclear ticket?"}
    )
    assert response.json() == {"status": "ok"}
    row = offline_app_state.rows[0]
    assert row["sql"] is None and row["answer"] is None
    assert row["dataset"] == "real"


def test_feedback_rejects_bad_verdict(client):
    response = client.post("/ask_feedback", json={"verdict": "meh", "question": "hello?"})
    assert response.status_code == 422


def test_feedback_sink_failure_returns_error_status(client):
    fb.set_sink(CapturingSink(fail=True))
    response = client.post("/ask_feedback", json={"verdict": "up", "question": "hi there"})
    assert response.status_code == 200
    assert response.json() == {"status": "error"}


def test_feedback_rate_limited_first_class(client, monkeypatch):
    monkeypatch.setattr(feedback_limiter, "check", lambda key: "slow down")
    response = client.post("/ask_feedback", json={"verdict": "up", "question": "hi there"})
    assert response.json() == {"status": "rate_limited"}


def test_feedback_votes_do_not_consume_ask_budget(client, offline_app_state):
    from api.text2sql import rate_limiter

    before = rate_limiter._day_count
    client.post("/ask_feedback", json={"verdict": "up", "question": "hi there"})
    assert rate_limiter._day_count == before
