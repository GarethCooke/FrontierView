"""
Phase 4b guardrails — acceptance tests.

T1  DRY equality: served allowlist == CURATED_QUESTIONS (keys + text).
T2  No free-text surface: extra field → 422; unknown id → 404; valid id passes.
T3  Gate: disabled flag → 404 on both POST /agent and GET /agent/questions.
T4  Rate limit, minute window: 5 pass, 6th → 429; clock advance → pass again.
T5  Rate limit, day window: 50 pass, 51st → 429 independent of minute window.
T6  Per-IP isolation: exhausted IP-A does not throttle IP-B.
T7  Exemption: GET /agent/questions unaffected by exhausted POST rate limit.
T8  Budget terminal: persistent 429-class LLM error → error(kind="budget") with
    locked message → run_finished(turns=None); non-429 error keeps kind="loop".
T9  Stream-contract regression: valid question_id produces correct 4a sequence.
"""
from __future__ import annotations

import json
import os
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from agent.llm import ProviderBudgetError
from agent.tests.helpers import _text_response, _tool_response


# ---------------------------------------------------------------------------
# SSE parse helper (mirrors test_streaming.py)
# ---------------------------------------------------------------------------

def _parse_sse(text: str) -> list[dict[str, Any]]:
    events = []
    for block in text.split("\n\n"):
        block = block.strip()
        if not block or block.startswith(": keepalive"):
            continue
        lines = block.splitlines()
        data_line = next((l for l in lines if l.startswith("data: ")), None)
        if data_line:
            events.append(json.loads(data_line[len("data: "):]))
    return events


# ---------------------------------------------------------------------------
# Shared mock LLM responses
# ---------------------------------------------------------------------------

_TOOL_ARGS = {
    "symbol": "AAPL",
    "order_size": 100_000,
    "horizon_hours": 2.0,
    "lambda_risk": 1e-6,
}


def _two_shot():
    return [
        _tool_response("optimal_schedule", "toolu_1", _TOOL_ARGS),
        _text_response("The optimal schedule has an expected cost of 8.5 bps."),
    ]


# ---------------------------------------------------------------------------
# T1 — DRY equality
# ---------------------------------------------------------------------------

def test_allowlist_dry_matches_curated_questions():
    """Served allowlist keys and texts must exactly equal CURATED_QUESTIONS."""
    from api.agent_routes import ALLOWED
    from agent.eval.questions.curated import CURATED_QUESTIONS

    assert set(ALLOWED.keys()) == {q.id for q in CURATED_QUESTIONS}, (
        "ALLOWED keys must match exactly the IDs in CURATED_QUESTIONS"
    )
    for q in CURATED_QUESTIONS:
        assert ALLOWED[q.id] == q.text, (
            f"Text mismatch for {q.id!r}: ALLOWED has {ALLOWED[q.id]!r}, "
            f"CURATED_QUESTIONS has {q.text!r}"
        )


# ---------------------------------------------------------------------------
# T2 — No free-text surface
# ---------------------------------------------------------------------------

def test_no_free_text_surface(monkeypatch):
    """Extra fields → 422; unknown id → 404; valid id passes validation."""
    monkeypatch.setenv("AGENT_PUBLIC_ENABLED", "1")
    from api.main import app

    with TestClient(app) as client:
        # Body with "text" extra field → 422 (extra="forbid")
        r = client.post("/agent", json={"question_id": "cur_cost_aapl_natural", "text": "injected"})
        assert r.status_code == 422, f"Expected 422 for extra 'text' field, got {r.status_code}"

        # Body with "prompt" extra field → 422
        r = client.post("/agent", json={"prompt": "injected"})
        assert r.status_code == 422, f"Expected 422 for 'prompt' field, got {r.status_code}"

        # Body with "question" extra field → 422
        r = client.post("/agent", json={"question": "free text"})
        assert r.status_code == 422, f"Expected 422 for 'question' field, got {r.status_code}"

        # Well-formed body but unknown id → 404
        r = client.post("/agent", json={"question_id": "does_not_exist_xyz_123"})
        assert r.status_code == 404, f"Expected 404 for unknown question_id, got {r.status_code}"
        assert r.json()["error"] == "unknown question_id"

        # Positive control: known id → 200
        with patch("agent.loop.llm.call", side_effect=_two_shot()):
            r = client.post("/agent", json={"question_id": "cur_cost_aapl_natural"})
        assert r.status_code == 200, f"Expected 200 for valid question_id, got {r.status_code}"


# ---------------------------------------------------------------------------
# T3 — Gate
# ---------------------------------------------------------------------------

def test_gate_disables_both_routes(monkeypatch):
    """With AGENT_PUBLIC_ENABLED unset, POST /agent and GET /agent/questions both 404."""
    monkeypatch.delenv("AGENT_PUBLIC_ENABLED", raising=False)
    from api.main import app

    with TestClient(app) as client:
        r = client.post("/agent", json={"question_id": "cur_cost_aapl_natural"})
        assert r.status_code == 404, f"POST /agent must be 404 when gate off; got {r.status_code}"

        r = client.get("/agent/questions")
        assert r.status_code == 404, f"GET /agent/questions must be 404 when gate off; got {r.status_code}"


# ---------------------------------------------------------------------------
# T4 — Rate limit, minute window
# ---------------------------------------------------------------------------

def test_rate_limit_minute_window():
    """5 requests pass; 6th → 429 with Retry-After; clock advance past window → pass again."""
    from api.rate_limiter import RateLimiter

    fake_now = [0.0]
    limiter = RateLimiter(clock=lambda: fake_now[0])
    ip = "203.0.113.1"

    for i in range(5):
        allowed, _ = limiter.check(ip)
        assert allowed, f"Request {i + 1} should be allowed"

    allowed, retry_after = limiter.check(ip)
    assert not allowed, "6th request must be rejected"
    assert retry_after > 0, "Retry-After must be positive"

    # Advance past the 60-second window
    fake_now[0] = 61.0
    allowed, _ = limiter.check(ip)
    assert allowed, "Request must pass after minute window expires"


# ---------------------------------------------------------------------------
# T5 — Rate limit, day window
# ---------------------------------------------------------------------------

def test_rate_limit_day_window():
    """50 requests pass (with minute-window advances between bursts); 51st → 429."""
    from api.rate_limiter import RateLimiter

    fake_now = [0.0]
    limiter = RateLimiter(clock=lambda: fake_now[0])
    ip = "203.0.113.2"

    for i in range(50):
        # Advance the minute clock between bursts so the minute window never blocks
        if i % 5 == 0 and i > 0:
            fake_now[0] += 61
        allowed, _ = limiter.check(ip)
        assert allowed, f"Request {i + 1} should be allowed by day window"

    # Advance past any remaining minute window before hitting the day limit
    fake_now[0] += 61
    allowed, retry_after = limiter.check(ip)
    assert not allowed, "51st request must be rejected by day window"
    assert retry_after > 0, "Retry-After must be positive for day-window rejection"

    # Advance past the day window
    fake_now[0] += 86_401
    allowed, _ = limiter.check(ip)
    assert allowed, "Request must pass after day window expires"


# ---------------------------------------------------------------------------
# T6 — Per-IP isolation
# ---------------------------------------------------------------------------

def test_rate_limit_per_ip_isolation():
    """Exhausting IP-A must not affect IP-B."""
    from api.rate_limiter import RateLimiter

    fake_now = [0.0]
    limiter = RateLimiter(clock=lambda: fake_now[0])

    ip_a = "10.0.0.1"
    ip_b = "10.0.0.2"

    for _ in range(5):
        limiter.check(ip_a)

    allowed_a, _ = limiter.check(ip_a)
    assert not allowed_a, "IP-A must be rate limited after 5 requests"

    allowed_b, _ = limiter.check(ip_b)
    assert allowed_b, "IP-B must not be affected by IP-A exhaustion"


# ---------------------------------------------------------------------------
# T7 — GET /agent/questions exempt from rate limiting
# ---------------------------------------------------------------------------

def test_questions_endpoint_exempt_from_rate_limit(monkeypatch):
    """GET /agent/questions must pass even when POST /agent is rate-limited for the IP."""
    monkeypatch.setenv("AGENT_PUBLIC_ENABLED", "1")
    from api.main import app
    from api import agent_routes
    from api.rate_limiter import RateLimiter

    # Build a fresh limiter and pre-exhaust the minute window for a specific IP
    target_ip = "203.0.113.42"
    fake_now = [0.0]
    test_limiter = RateLimiter(clock=lambda: fake_now[0])
    for _ in range(5):
        test_limiter.check(target_ip)  # exhaust minute window

    original_limiter = agent_routes._rate_limiter
    agent_routes._rate_limiter = test_limiter
    try:
        with TestClient(app) as client:
            headers = {"X-Forwarded-For": target_ip}

            # POST must be rate limited
            r = client.post(
                "/agent",
                json={"question_id": "cur_cost_aapl_natural"},
                headers=headers,
            )
            assert r.status_code == 429, f"POST must be 429 when exhausted; got {r.status_code}"
            assert "Retry-After" in r.headers

            # GET must not be rate limited
            r = client.get("/agent/questions", headers=headers)
            assert r.status_code == 200, f"GET /agent/questions must be exempt; got {r.status_code}"
            data = r.json()
            assert isinstance(data, list) and len(data) > 0
            assert all("id" in item and "text" in item for item in data)
    finally:
        agent_routes._rate_limiter = original_limiter


# ---------------------------------------------------------------------------
# T8 — Budget terminal
# ---------------------------------------------------------------------------

_LOCKED_BUDGET_MESSAGE = (
    "The demo's usage budget has been reached for now — likely the monthly cap. "
    "It resets at the start of next month; the rest of FrontierView works as "
    "normal in the meantime."
)


def test_budget_terminal_classification(monkeypatch):
    """Persistent 429 → error(kind='budget', locked message) → run_finished(turns=None).

    Patches the provider seam's public signal (``ProviderBudgetError``) directly —
    no hand-built mirror of llm.py's wrapper.  The seam test in test_recovery.py
    proves llm.py actually raises this on 429-class retry exhaustion.
    """
    monkeypatch.setenv("AGENT_PUBLIC_ENABLED", "1")
    from api.main import app

    # Message is immaterial — the route classifies on type, not text, and asserts
    # its own locked wording.  No mirror of llm.py's wrapper string here.
    budget_exc = ProviderBudgetError("retries exhausted on 429-class error")

    with patch("agent.loop.llm.call", side_effect=budget_exc):
        with TestClient(app) as client:
            resp = client.post("/agent", json={"question_id": "cur_cost_aapl_natural"})

    assert resp.status_code == 200
    frames = _parse_sse(resp.text)
    types = [f["type"] for f in frames]

    error_events = [f for f in frames if f["type"] == "error"]
    assert error_events, "Expected at least one error event"
    assert error_events[0]["kind"] == "budget", (
        f"Expected kind='budget' for rate-limit error; got {error_events[0]['kind']!r}"
    )
    assert error_events[0]["message"] == _LOCKED_BUDGET_MESSAGE, (
        f"Budget message must be the locked wording, not the raw exception string.\n"
        f"Got: {error_events[0]['message']!r}"
    )

    assert types[-1] == "run_finished"
    assert frames[-1]["turns"] is None

    # Positive control: non-429 exception must still yield kind="loop"
    with patch("agent.loop.llm.call", side_effect=RuntimeError("unrelated failure")):
        with TestClient(app) as client:
            resp2 = client.post("/agent", json={"question_id": "cur_cost_aapl_natural"})

    frames2 = _parse_sse(resp2.text)
    error_events2 = [f for f in frames2 if f["type"] == "error"]
    assert error_events2, "Expected error event on non-budget failure"
    assert error_events2[0]["kind"] == "loop", (
        f"Non-429 error must keep kind='loop'; got {error_events2[0]['kind']!r}"
    )


# ---------------------------------------------------------------------------
# T9 — Stream-contract regression: valid question_id → 4a event sequence
# ---------------------------------------------------------------------------

def test_stream_contract_regression_valid_question_id(monkeypatch):
    """A valid question_id must produce the correct 4a lifecycle event sequence."""
    monkeypatch.setenv("AGENT_PUBLIC_ENABLED", "1")
    from api.main import app
    from api.agent_routes import ALLOWED

    _QUESTION_ID = "cur_cost_aapl_natural"

    with patch("agent.loop.llm.call", side_effect=_two_shot()):
        with TestClient(app) as client:
            resp = client.post("/agent", json={"question_id": _QUESTION_ID})

    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers["content-type"]

    frames = _parse_sse(resp.text)
    types = [f["type"] for f in frames]

    assert types[0] == "run_started"
    assert types[-2] == "final_answer"
    assert types[-1] == "run_finished"

    # run_started question must be the resolved canonical text, not the ID
    assert frames[0]["question"] == ALLOWED[_QUESTION_ID]
    assert frames[0]["question"] != _QUESTION_ID

    # seq values must be 0, 1, 2, …
    seqs = [f["seq"] for f in frames]
    assert seqs == list(range(len(seqs))), f"seq must be monotonically increasing from 0; got {seqs}"

    # run_finished.turns must be a positive integer on success
    assert isinstance(frames[-1]["turns"], int) and frames[-1]["turns"] > 0
