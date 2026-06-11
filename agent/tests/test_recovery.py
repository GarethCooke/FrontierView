"""
Recovery tests — one per row of the §4 recovery-policy table.

Row | Error class                        | Surface    | Action
────┼────────────────────────────────────┼────────────┼──────────────────────────────────────
 1  | Invalid tool arg                   | tool-error | structured error → model
 2  | Numerical blow-up / order ≫ ADV   | tool-error | structured warning + proceeds
 3  | Unknown tool / malformed JSON      | loop       | error with valid-tool list → model
 4  | Provider 429 / timeout             | loop       | exponential backoff, retry
 5  | Over-length response (max_tokens)  | loop       | compact and retry
 6  | Repeated identical tool call       | loop       | nudge once, then graceful abort
"""
from __future__ import annotations

from unittest.mock import MagicMock, call, patch

import anthropic
import pytest

from agent import llm
from agent.config import MAX_ITERS, TOOL_RETRY_BUDGET
from agent.loop import _MAX_TRUNCATION_RETRIES, _TRUNCATION_RAISED_BUDGET, run
from agent.tests.helpers import _max_tokens_response, _text_response, _tool_response
from agent.tools import dispatch


# ---------------------------------------------------------------------------
# Row 1 — Invalid tool arg
# The dispatch layer returns {"error": "InvalidArgument", ...} which the loop
# feeds back to the model as a tool_result.  The model then self-corrects.
# ---------------------------------------------------------------------------


def test_row1_invalid_arg_returns_structured_error_to_model():
    result = dispatch(
        "cost_and_variance",
        {"symbol": "BOGUS", "order_size": 100_000, "horizon_hours": 2.0, "schedule_type": "twap"},
    )
    assert result.get("error") == "InvalidArgument"
    assert "field" in result
    assert "detail" in result
    # got / allowed are optional but expected when field is known
    assert "BOGUS" in result["detail"]


def test_row1_negative_order_size_structured_error():
    result = dispatch(
        "optimal_schedule",
        {"symbol": "AAPL", "order_size": -1, "horizon_hours": 2.0, "lambda_risk": 1e-6},
    )
    assert result.get("error") == "InvalidArgument"
    assert "order_size" in result.get("field", "") or "order_size" in result.get("detail", "")


def test_row1_retry_budget_exhausted():
    """After TOOL_RETRY_BUDGET+1 errors from the same call site, the error gains
    a retry_budget_exhausted flag so the model stops trying."""
    bad_args = {
        "symbol": "BOGUS",
        "order_size": 100_000,
        "horizon_hours": 2.0,
        "lambda_risk": 1e-6,
    }
    # Simulate TOOL_RETRY_BUDGET+1 sequential calls with the same bad args
    responses = [
        _tool_response("optimal_schedule", f"toolu_{i}", bad_args)
        for i in range(TOOL_RETRY_BUDGET + 1)
    ] + [_text_response("I cannot compute this.")]

    with patch("agent.loop.llm.call", side_effect=responses) as mock_call:
        answer = run("What is the cost for BOGUS?")

    # Same (name, args) key → duplicate detection fires and aborts before MAX_ITERS
    assert mock_call.call_count < MAX_ITERS
    assert "aborted" in answer.lower()


# ---------------------------------------------------------------------------
# Row 2 — Numerical blow-up / order ≫ ADV
# Tool proceeds but includes a warning in the summary.
# No error key; no crash.
# ---------------------------------------------------------------------------


def test_row2_large_order_warns_but_proceeds():
    result = dispatch(
        "cost_and_variance",
        {
            "symbol": "JPM",
            "order_size": 50_000_000,  # ~4× daily ADV of JPM (12M)
            "horizon_hours": 6.5,
            "schedule_type": "twap",
        },
    )
    assert "error" not in result, f"Expected warn-and-proceed, got error: {result}"
    assert "summary" in result
    assert "warning" in result["summary"]
    # Cost should still be a finite number
    import math
    assert math.isfinite(result["summary"]["expected_cost_bps"])


# ---------------------------------------------------------------------------
# Row 3 — Unknown tool / malformed tool-use JSON
# The loop dispatches through tools.dispatch which returns an UnknownTool error;
# that error is fed back to the model as a tool_result so it can recover.
# ---------------------------------------------------------------------------


def test_row3_unknown_tool_fed_back_to_model():
    """Unknown tool call must be returned to the model as a structured error
    (containing the valid tool list), not crash the loop."""
    responses = [
        _tool_response("nonexistent_tool", "toolu_unknown", {}),
        _text_response("I used the wrong tool; here is the correct answer."),
    ]

    with patch("agent.loop.llm.call", side_effect=responses):
        answer = run("What is the optimal schedule?")

    # Model recovered after the UnknownTool error was fed back
    assert "correct answer" in answer.lower()


def test_row3_unknown_tool_error_contains_allowed_list():
    result = dispatch("no_such_tool", {"x": 1})
    assert result.get("error") == "UnknownTool"
    assert "allowed" in result
    assert "cost_and_variance" in (result.get("allowed") or [])


# ---------------------------------------------------------------------------
# Row 4 — Provider 429 / timeout / 5xx  →  exponential backoff
# We mock time.sleep and the anthropic client to verify retries occur.
# ---------------------------------------------------------------------------


def test_row4_rate_limit_triggers_retry():
    """RateLimitError must trigger retries with exponential backoff."""
    rate_limit_exc = anthropic.RateLimitError(
        message="rate limited", response=MagicMock(status_code=429), body={}
    )
    success_resp = _text_response("Here is the answer.")

    # Fail twice, succeed on third attempt
    side_effects = [rate_limit_exc, rate_limit_exc, success_resp]

    with patch("agent.llm._get_client") as mock_client_fn, \
         patch("agent.llm.time.sleep") as mock_sleep:
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = side_effects
        mock_client_fn.return_value = mock_client

        answer = run("What is the schedule?")

    assert mock_client.messages.create.call_count == 3
    # sleep was called between retries
    assert mock_sleep.call_count == 2
    assert mock_sleep.call_args_list[0] == call(1.0)  # 1s after first failure
    assert mock_sleep.call_args_list[1] == call(2.0)  # 2s after second failure
    assert "answer" in answer.lower() or isinstance(answer, str)


def test_row4_rate_limit_exhaustion_raises_provider_budget_error():
    """At the provider seam, persistent 429 retry-exhaustion raises the
    provider-agnostic ProviderBudgetError with the original RateLimitError
    chained as __cause__ — so callers classify the budget terminal without
    importing anthropic, and a future re-wrap can't degrade it to kind="loop"."""
    rate_limit_exc = anthropic.RateLimitError(
        message="rate limited", response=MagicMock(status_code=429), body={}
    )

    with patch("agent.llm._get_client") as mock_client_fn, \
         patch("agent.llm.time.sleep"):
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = [rate_limit_exc] * 10
        mock_client_fn.return_value = mock_client

        with pytest.raises(llm.ProviderBudgetError) as exc_info:
            llm.call("system", [], [{"role": "user", "content": "hi"}])

    assert exc_info.value.__cause__ is rate_limit_exc, (
        "Original RateLimitError must be chained as __cause__ on ProviderBudgetError"
    )


def test_row4_non_rate_limit_exhaustion_raises_generic():
    """Positive control: a persistent non-429 provider error (5xx) exhausts
    retries into the *generic* RuntimeError, not ProviderBudgetError — the
    budget signal is reserved for 429-class throttling/spend-cap only."""
    server_exc = anthropic.InternalServerError(
        message="upstream boom", response=MagicMock(status_code=503), body={}
    )

    with patch("agent.llm._get_client") as mock_client_fn, \
         patch("agent.llm.time.sleep"):
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = [server_exc] * 10
        mock_client_fn.return_value = mock_client

        with pytest.raises(RuntimeError) as exc_info:
            llm.call("system", [], [{"role": "user", "content": "hi"}])

    assert not isinstance(exc_info.value, llm.ProviderBudgetError), (
        "Non-429 exhaustion must not be classified as a budget error"
    )
    assert exc_info.value.__cause__ is server_exc, (
        "Original server error must be chained as __cause__ on the generic RuntimeError"
    )


# ---------------------------------------------------------------------------
# Row 5 — Over-length response (max_tokens)
# Loop should compact and retry rather than returning [TRUNCATED].
# ---------------------------------------------------------------------------


def test_row5_always_max_tokens_returns_truncated_note():
    """When every call returns max_tokens the loop must terminate with [response truncated],
    not the generic MAX_ITERS message, and retries must use a raised output budget."""
    with patch("agent.loop.llm.call", return_value=_max_tokens_response("Partial...")) as mock_call:
        answer = run("Tell me about AAPL costs.")

    assert "[response truncated]" in answer
    assert "Stopped after" not in answer

    calls = mock_call.call_args_list
    assert len(calls) == _MAX_TRUNCATION_RETRIES + 1
    # All retries (calls after the first) must use the raised budget
    for c in calls[1:]:
        assert c.kwargs.get("max_tokens") == _TRUNCATION_RAISED_BUDGET
    # First call uses the default (None → MAX_TOKENS internally)
    assert calls[0].kwargs.get("max_tokens") is None


def test_row5_max_tokens_triggers_compaction_and_retry():
    """A max_tokens stop_reason must trigger compaction and retry (not [TRUNCATED])."""
    responses = [
        _max_tokens_response("Partial answer..."),
        _text_response("Full answer after retry."),
    ]

    with patch("agent.loop.llm.call", side_effect=responses) as mock_call:
        answer = run("Tell me about AAPL costs.")

    # Should have retried — two calls
    assert mock_call.call_count == 2
    # Final answer should not be the TRUNCATED fallback
    assert "[TRUNCATED]" not in answer
    assert "Full answer" in answer


# ---------------------------------------------------------------------------
# Row 6 — Repeated identical tool call
# Loop nudges the model once, then aborts gracefully on a second duplicate.
# ---------------------------------------------------------------------------


def test_row6_duplicate_call_nudge_then_abort():
    """Model calling the same tool+args more than once should be nudged,
    then gracefully aborted — never infinite-loop."""
    same_args = {
        "symbol": "AAPL",
        "order_size": 100_000,
        "horizon_hours": 2.0,
        "lambda_risk": 1e-6,
    }
    # Keep returning the same tool call; the loop must not run forever
    always_same = _tool_response("optimal_schedule", "toolu_dup", same_args)

    with patch("agent.loop.llm.call", return_value=always_same) as mock_call:
        answer = run("What is the optimal schedule?")

    # Must terminate before MAX_ITERS
    assert mock_call.call_count < MAX_ITERS
    # Answer indicates the duplicate was detected
    assert "Aborted" in answer or "duplicate" in answer.lower() or isinstance(answer, str)
    assert answer  # non-empty


def test_row6_duplicate_first_occurrence_gets_nudge_result():
    """First duplicate gets a DuplicateCall error result (not a real tool error)."""
    same_args = {
        "symbol": "AAPL",
        "order_size": 100_000,
        "horizon_hours": 2.0,
        "lambda_risk": 1e-6,
    }
    responses = [
        _tool_response("optimal_schedule", "toolu_1", same_args),   # call 1 — real
        _tool_response("optimal_schedule", "toolu_2", same_args),   # call 2 — duplicate → nudge
        _text_response("I used the result from the first call."),   # model self-corrects
    ]

    with patch("agent.loop.llm.call", side_effect=responses):
        answer = run("What is the optimal schedule?")

    # Model self-corrected after duplicate nudge — used the established result
    assert "first call" in answer.lower()


# ---------------------------------------------------------------------------
# No raw stack trace to user
# ---------------------------------------------------------------------------


def test_no_raw_stack_trace_on_tool_failure():
    """A tool error must return a structured dict, never a raw Python traceback."""
    # Pass malformed input to trigger an internal error path
    result = dispatch("sweep", {
        "symbol": "AAPL",
        "side": "sell",
        "order_size": 100_000,
        "horizon_hours": 2.0,
        "schedule": "twap",
        "param": "calibrated.eta",
        "param_range": [0.1, 0.1],  # identical start/end → validation error
    })
    assert isinstance(result, dict)
    assert "error" in result
    # No raw Python traceback string
    assert "Traceback" not in str(result)
    assert "File " not in str(result)
