from unittest.mock import MagicMock, patch

from agent.config import MAX_ITERS
from agent.loop import run


def _tool_response(tool_name: str, tool_id: str, tool_input: dict) -> MagicMock:
    block = MagicMock()
    block.type = "tool_use"
    block.name = tool_name
    block.id = tool_id
    block.input = tool_input

    response = MagicMock()
    response.stop_reason = "tool_use"
    response.content = [block]
    return response


def _text_response(text: str) -> MagicMock:
    block = MagicMock()
    block.type = "text"
    block.text = text

    response = MagicMock()
    response.stop_reason = "end_turn"
    response.content = [block]
    return response


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

    assert "Stopped" in answer
    assert str(MAX_ITERS) in answer


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
