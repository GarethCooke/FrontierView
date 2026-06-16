from unittest.mock import patch

from agent.loop import run
from agent.tests.helpers import _max_tokens_response, _text_response, _tool_response


def test_loop_dispatches_tool_then_terminates():
    """Loop must: dispatch tool, append result, return the final text answer."""
    tool_args = {
        "symbol": "AAPL",
        "order_size": 100_000,
        "horizon_hours": 2.0,
        "lambda_risk": 1e-6,
    }
    responses = [
        _tool_response("optimal_schedule", "toolu_abc123", tool_args),
        _text_response("The optimal schedule has an expected cost of 8.5 bps."),
    ]

    with patch("agent.loop.llm.call", side_effect=responses) as mock_call:
        answer = run("What is the optimal schedule for AAPL?")

    assert mock_call.call_count == 2
    assert "8.5" in answer


def test_loop_stops_after_max_iters():
    """Loop must terminate gracefully at MAX_ITERS, never loop forever."""
    tool_args = {
        "symbol": "AAPL",
        "order_size": 100_000,
        "horizon_hours": 2.0,
        "lambda_risk": 1e-6,
    }
    always_tool = _tool_response("optimal_schedule", "toolu_inf", tool_args)

    with patch("agent.loop.llm.call", return_value=always_tool):
        answer = run("What is the optimal schedule?")

    # Phase 2: duplicate-call guard fires before MAX_ITERS — accept either graceful exit
    assert "Stopped" in answer or "Aborted" in answer


def test_loop_handles_max_tokens():
    """A max_tokens stop must trigger compact+retry, not crash; final answer is returned."""
    with patch("agent.loop.llm.call", side_effect=[
        _max_tokens_response("Partial answer about AAPL..."),
        _text_response("The full answer after compaction."),
    ]) as mock_call:
        answer = run("What is the optimal schedule for AAPL?")

    assert mock_call.call_count == 2
    assert "full answer" in answer.lower()
    assert "[TRUNCATED]" not in answer


def test_loop_cancels_before_any_model_call():
    """should_cancel() True at the first poll must return without calling the model."""
    with patch("agent.loop.llm.call") as mock_call:
        answer = run("What is the optimal schedule?", should_cancel=lambda: True)

    assert mock_call.call_count == 0, "model must not be called once cancelled"
    assert "cancel" in answer.lower()


def test_loop_cancels_after_first_iteration():
    """Cancellation takes effect at the next iteration boundary, not mid-call."""
    tool_args = {
        "symbol": "AAPL",
        "order_size": 100_000,
        "horizon_hours": 2.0,
        "lambda_risk": 1e-6,
    }
    state = {"calls": 0}

    def _spy(*args, **kwargs):
        state["calls"] += 1
        return _tool_response("optimal_schedule", f"toolu_{state['calls']}", tool_args)

    # Cancel only after the first model call has happened.
    def _should_cancel() -> bool:
        return state["calls"] >= 1

    with patch("agent.loop.llm.call", side_effect=_spy):
        answer = run("What is the optimal schedule?", should_cancel=_should_cancel)

    assert state["calls"] == 1, "exactly one in-flight model call should complete"
    assert "cancel" in answer.lower()


def test_loop_recovers_from_tool_error():
    """A tool error must be fed back to the model as an observation, not crash the loop."""
    error_args = {
        "symbol": "INVALID",
        "order_size": 100_000,
        "horizon_hours": 2.0,
        "lambda_risk": 1e-6,
    }
    responses = [
        _tool_response("optimal_schedule", "toolu_err", error_args),
        _text_response("I encountered an error; the symbol INVALID is not supported."),
    ]

    with patch("agent.loop.llm.call", side_effect=responses):
        answer = run("What is the cost for INVALID?")

    assert "INVALID" in answer or "error" in answer.lower() or "not supported" in answer
