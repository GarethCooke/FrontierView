"""
DRY contract tests for the eval harness.

1. GT-path == tool-path: the GT computation in each generated question must
   produce the same numeric values as the corresponding tool dispatch call.
   Same spirit as the Phase 1/2 DRY equality tests in test_tools.py.

2. Eval-mode flag-off: run() with eval_mode=False must produce output
   identical to the current prod path — no eval_answer block, no addendum.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from agent.eval.questions.generated import GENERATED_QUESTIONS, _gt_cost
from agent.eval.scorer import parse_eval_answer
from agent.tools import dispatch
from api.parameters import SYMBOL_PARAMS


# ---------------------------------------------------------------------------
# 1. DRY contract: GT function resolves to same FV callables as tool dispatch
# ---------------------------------------------------------------------------


def _dispatch_cost_values(symbol: str, order_size: float, horizon_hours: float,
                           schedule_type: str, lambda_risk: float = 1e-6) -> dict[str, float]:
    """Call dispatch() and return the summary numeric values."""
    result = dispatch(
        "cost_and_variance",
        {
            "symbol": symbol,
            "order_size": order_size,
            "horizon_hours": horizon_hours,
            "schedule_type": schedule_type,
            "lambda_risk": lambda_risk,
        },
    )
    assert "summary" in result, f"Unexpected result shape: {result}"
    s = result["summary"]
    return {
        "expected_cost_bps": s["expected_cost_bps"],
        "variance_bps2": s["variance_bps2"],
    }


@pytest.mark.parametrize("symbol", list(SYMBOL_PARAMS))
def test_gt_twap_equals_dispatch(symbol: str) -> None:
    """GT twap path == dispatch('cost_and_variance', schedule_type='twap')."""
    gt = _gt_cost(symbol, 100_000.0, 2.0, "twap")
    via_tool = _dispatch_cost_values(symbol, 100_000.0, 2.0, "twap")

    assert round(gt["expected_cost_bps"], 4) == via_tool["expected_cost_bps"], (
        f"{symbol} TWAP expected_cost_bps mismatch: "
        f"GT={gt['expected_cost_bps']}, tool={via_tool['expected_cost_bps']}"
    )
    assert round(gt["variance_bps2"], 4) == via_tool["variance_bps2"], (
        f"{symbol} TWAP variance_bps2 mismatch"
    )


@pytest.mark.parametrize("symbol", list(SYMBOL_PARAMS))
def test_gt_ac_linear_equals_dispatch(symbol: str) -> None:
    """GT ac_linear path == dispatch('cost_and_variance', schedule_type='ac_linear')."""
    gt = _gt_cost(symbol, 100_000.0, 2.0, "ac_linear")
    via_tool = _dispatch_cost_values(symbol, 100_000.0, 2.0, "ac_linear")

    assert round(gt["expected_cost_bps"], 4) == via_tool["expected_cost_bps"], (
        f"{symbol} ac_linear expected_cost_bps mismatch"
    )
    assert round(gt["variance_bps2"], 4) == via_tool["variance_bps2"], (
        f"{symbol} ac_linear variance_bps2 mismatch"
    )


def test_generated_questions_gt_consistent_with_dispatch() -> None:
    """Every generated question whose gt_values includes expected_cost_bps must
    match what dispatch() would return for the same symbol / schedule."""
    for q in GENERATED_QUESTIONS:
        if "expected_cost_bps" not in q.gt_values:
            continue

        # Decode tool path from question id: gen_cost_twap_AAPL, gen_cost_ac_MSFT, etc.
        parts = q.id.split("_")
        if len(parts) < 4:
            continue
        # gen_cost_<schedule>_<SYMBOL> or gen_optimal_<SYMBOL>
        if parts[1] == "cost":
            schedule = parts[2]   # "twap" or "ac"
            symbol = parts[3]
            if schedule == "ac":
                schedule = "ac_linear"
        elif parts[1] == "optimal":
            schedule = "ac_linear"
            symbol = parts[2]
        else:
            continue

        if symbol not in SYMBOL_PARAMS:
            continue

        via_tool = _dispatch_cost_values(symbol, 100_000.0, 2.0, schedule)
        gt_cost = q.gt_values["expected_cost_bps"]

        assert abs(round(gt_cost, 4) - via_tool["expected_cost_bps"]) < 1e-6, (
            f"Q {q.id}: GT expected_cost_bps={gt_cost} "
            f"!= dispatch={via_tool['expected_cost_bps']}"
        )


# ---------------------------------------------------------------------------
# 2. Eval-mode flag-off: prod path unchanged
# ---------------------------------------------------------------------------


def _make_fake_response(text: str, stop_reason: str = "end_turn"):
    """Build a minimal mock Message that loop.run() will accept."""
    content_block = MagicMock()
    content_block.type = "text"
    content_block.text = text

    msg = MagicMock()
    msg.stop_reason = stop_reason
    msg.content = [content_block]
    return msg


def test_eval_mode_off_no_block() -> None:
    """With eval_mode=False the answer must NOT contain an <eval_answer> block."""
    fake_answer = "The cost is 12.3 bps."
    with patch("agent.loop.llm.call", return_value=_make_fake_response(fake_answer)):
        from agent.loop import run
        answer = run("What is the cost of AAPL?", eval_mode=False)

    assert "<eval_answer>" not in answer
    assert parse_eval_answer(answer) is None


def test_eval_mode_on_system_prompt_addendum() -> None:
    """With eval_mode=True the system prompt sent to llm.call includes the addendum."""
    fake_answer = (
        "The cost is 12.3456 bps.\n"
        '<eval_answer>{"values": {"expected_cost_bps": 12.3456}, "synthetic": false}</eval_answer>'
    )
    captured_prompts: list[str] = []

    def _spy_call(system_prompt, *args, **kwargs):
        captured_prompts.append(system_prompt)
        return _make_fake_response(fake_answer)

    with patch("agent.loop.llm.call", side_effect=_spy_call):
        from agent.loop import run
        answer = run("What is the cost of AAPL?", eval_mode=True)

    assert len(captured_prompts) == 1
    assert "EVAL MODE" in captured_prompts[0]
    assert "<eval_answer>" in answer


def test_eval_mode_off_system_prompt_unchanged() -> None:
    """With eval_mode=False the system prompt sent to llm.call has no eval addendum."""
    captured_prompts: list[str] = []

    def _spy_call(system_prompt, *args, **kwargs):
        captured_prompts.append(system_prompt)
        return _make_fake_response("42 bps.")

    with patch("agent.loop.llm.call", side_effect=_spy_call):
        from agent.loop import run
        run("What is the cost of AAPL?", eval_mode=False)

    assert len(captured_prompts) == 1
    assert "EVAL MODE" not in captured_prompts[0]
    assert "<eval_answer>" not in captured_prompts[0]


def test_eval_capture_records_tool_calls() -> None:
    """_eval_capture receives tool_call and tool_result events when tools are used."""
    tool_response = MagicMock()
    tool_response.stop_reason = "tool_use"
    tool_use = MagicMock()
    tool_use.type = "tool_use"
    tool_use.id = "tu_1"
    tool_use.name = "list_symbols"
    tool_use.input = {}
    tool_response.content = [tool_use]

    final_response = _make_fake_response("Available symbols: AAPL, MSFT.")

    call_count = 0

    def _two_shot(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return tool_response if call_count == 1 else final_response

    capture: list[dict] = []

    with patch("agent.loop.llm.call", side_effect=_two_shot):
        from agent.loop import run
        run("List available symbols.", _eval_capture=capture)

    tool_calls = [e for e in capture if e["type"] == "tool_call"]
    tool_results = [e for e in capture if e["type"] == "tool_result"]
    answers = [e for e in capture if e["type"] == "answer"]

    assert len(tool_calls) == 1
    assert tool_calls[0]["name"] == "list_symbols"
    assert len(tool_results) == 1
    assert len(answers) == 1
