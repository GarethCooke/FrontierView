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

from agent.eval.questions.generated import GENERATED_QUESTIONS
from agent.eval.questions.gt import gt_cost, gt_with_param_override
from agent.eval.scorer import parse_eval_answer
from agent.tools import dispatch
from api.parameters import SYMBOL_PARAMS


# ---------------------------------------------------------------------------
# 1. DRY contract: GT function resolves to same FV callables as tool dispatch
# ---------------------------------------------------------------------------


def _dispatch_cost_values(symbol: str, order_size: float, horizon_hours: float,
                           schedule_type: str, lambda_risk: float = 1e-6) -> dict[str, float]:
    """Call dispatch('cost_and_variance') and return the summary numeric values."""
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


def _dispatch_optimal_values(symbol: str, order_size: float, horizon_hours: float,
                              lambda_risk: float = 1e-6) -> dict[str, float]:
    """Call dispatch('optimal_schedule') and return the summary numeric values."""
    result = dispatch(
        "optimal_schedule",
        {
            "symbol": symbol,
            "order_size": order_size,
            "horizon_hours": horizon_hours,
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
    gt = gt_cost(symbol, 100_000.0, 2.0, "twap")
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
    gt = gt_cost(symbol, 100_000.0, 2.0, "ac_linear")
    via_tool = _dispatch_cost_values(symbol, 100_000.0, 2.0, "ac_linear")

    assert round(gt["expected_cost_bps"], 4) == via_tool["expected_cost_bps"], (
        f"{symbol} ac_linear expected_cost_bps mismatch"
    )
    assert round(gt["variance_bps2"], 4) == via_tool["variance_bps2"], (
        f"{symbol} ac_linear variance_bps2 mismatch"
    )


# B1 — optimal_schedule GT verified: confirmed it wraps schedule_ac_linear → same path as
# gt_cost(..., "ac_linear"). Locked in by this parametrised DRY test.
@pytest.mark.parametrize("symbol", ["AAPL", "SPY", "JPM"])
def test_optimal_schedule_gt_equals_dispatch(symbol: str) -> None:
    """GT ac_linear == dispatch('optimal_schedule') for all gen_optimal_* symbols."""
    gt = gt_cost(symbol, 100_000.0, 2.0, "ac_linear")
    via_tool = _dispatch_optimal_values(symbol, 100_000.0, 2.0)

    assert round(gt["expected_cost_bps"], 4) == via_tool["expected_cost_bps"], (
        f"{symbol} optimal GT expected_cost_bps mismatch: "
        f"GT={gt['expected_cost_bps']}, tool={via_tool['expected_cost_bps']}"
    )
    assert round(gt["variance_bps2"], 4) == via_tool["variance_bps2"], (
        f"{symbol} optimal GT variance_bps2 mismatch"
    )


# M2 — sweep GT DRY equality
def test_sweep_gt_equals_dispatch() -> None:
    """Sweep GT endpoint values == dispatch('sweep') summary endpoints."""
    sym, order, horizon = "AAPL", 100_000.0, 2.0
    sigma_lo, sigma_hi = 0.010, 0.025

    gt_lo = gt_with_param_override(sym, order, horizon, sigma=sigma_lo)
    gt_hi = gt_with_param_override(sym, order, horizon, sigma=sigma_hi)

    result = dispatch("sweep", {
        "symbol": sym,
        "side": "buy",
        "order_size": order,
        "horizon_hours": horizon,
        "schedule": "twap",
        "param": "market.sigma",
        "param_range": [sigma_lo, sigma_hi],
    })
    assert "summary" in result, f"Unexpected result: {result}"
    s = result["summary"]

    assert round(gt_lo, 4) == s["cost_at_range_start_bps"], (
        f"sweep GT start mismatch: GT={gt_lo}, tool={s['cost_at_range_start_bps']}"
    )
    assert round(gt_hi, 4) == s["cost_at_range_end_bps"], (
        f"sweep GT end mismatch: GT={gt_hi}, tool={s['cost_at_range_end_bps']}"
    )
    assert round(gt_hi - gt_lo, 4) == s["cost_delta_bps"], (
        f"sweep GT delta mismatch"
    )


# M2 — compare_schedules GT DRY equality
def test_compare_gt_equals_dispatch() -> None:
    """compare_schedules cheapest GT == dispatch('compare_schedules') cheapest_cost_bps."""
    sym, order, horizon = "AAPL", 100_000.0, 2.0

    gt_cheapest = min(
        gt_cost(sym, order, horizon, "twap")["expected_cost_bps"],
        gt_cost(sym, order, horizon, "ac_linear")["expected_cost_bps"],
    )

    result = dispatch("compare_schedules", {
        "symbol": sym,
        "side": "buy",
        "order_size": order,
        "horizon_hours": horizon,
        "schedules": ["twap", "ac_linear"],
    })
    assert "summary" in result, f"Unexpected result: {result}"
    tool_cheapest = result["summary"]["cheapest_cost_bps"]

    assert round(gt_cheapest, 4) == tool_cheapest, (
        f"compare_schedules GT cheapest mismatch: GT={gt_cheapest}, tool={tool_cheapest}"
    )


# M3 — vacuous-pass fix: assert exact count of questions covered
def test_generated_questions_gt_consistent_with_dispatch() -> None:
    """Every generated question with expected_cost_bps GT must match dispatch().

    Counts covered questions to prevent silent skips (M3):
      5 gen_cost_twap_* + 5 gen_cost_ac_* + 3 gen_optimal_* = 13 expected.
    """
    checked = 0
    for q in GENERATED_QUESTIONS:
        if "expected_cost_bps" not in q.gt_values:
            continue

        parts = q.id.split("_")
        # gen_cost_twap_AAPL or gen_cost_ac_MSFT
        if parts[1] == "cost" and len(parts) == 4:
            schedule = parts[2]
            symbol = parts[3]
            if schedule == "ac":
                schedule = "ac_linear"
        # gen_optimal_AAPL
        elif parts[1] == "optimal" and len(parts) == 3:
            schedule = "ac_linear"
            symbol = parts[2]
        else:
            continue

        if symbol not in SYMBOL_PARAMS:
            continue

        via_tool = _dispatch_cost_values(symbol, 100_000.0, 2.0, schedule)
        gt_val = q.gt_values["expected_cost_bps"]

        assert abs(round(gt_val, 4) - via_tool["expected_cost_bps"]) < 1e-6, (
            f"Q {q.id}: GT expected_cost_bps={gt_val} "
            f"!= dispatch={via_tool['expected_cost_bps']}"
        )
        checked += 1

    assert checked > 0, "No questions were checked — ID convention may have changed"
    assert checked == 13, (
        f"Expected 13 questions with expected_cost_bps GT, checked {checked}. "
        "Update this count if the question set changes."
    )


# S2 — acceptable_tool_paths: gen_cost_ac_* must not fail when optimal_schedule is used
def test_check_tool_path_accepts_alternative_for_gen_cost_ac() -> None:
    """check_tool_path passes gen_cost_ac_* when optimal_schedule is used (S2)."""
    from agent.eval import layer2

    q = next(q for q in GENERATED_QUESTIONS if q.id == "gen_cost_ac_AAPL")
    assert ["optimal_schedule"] in q.acceptable_tool_paths, (
        "gen_cost_ac_AAPL must declare optimal_schedule as an acceptable path"
    )

    # Simulate a capture that used optimal_schedule but NOT cost_and_variance
    capture = [
        {"type": "tool_call", "name": "optimal_schedule", "args": {}, "key": "k1"},
        {"type": "tool_result", "name": "optimal_schedule", "key": "k1",
         "summary": {"expected_cost_bps": 8.0}, "error": None},
        {"type": "answer", "text": "Cost is 8.0 bps."},
    ]
    result = layer2.check_tool_path(capture, q)
    assert result["passed"] is True, (
        f"check_tool_path should pass on acceptable alternative; got: {result}"
    )


# N1 — synthetic caveat path covered by unit test
def test_caveat_presence_synthetic_covered() -> None:
    """check_caveat_presence fires correctly on synthetic=True questions (N1)."""
    from agent.eval import layer2
    from agent.eval.questions.types import Question

    # TODO: add synthetic=True eval questions when a recovery-demo tool is added

    q_synth = Question(
        id="test_synthetic",
        text="Recover eta from synthetic trades.",
        expected_tool_path=[],
        gt_values={},
        synthetic=True,
    )

    # Pass: answer contains a synthetic keyword
    capture_pass = [{"type": "answer", "text": "The recovery estimate was derived from synthetic trades."}]
    result = layer2.check_caveat_presence(capture_pass, q_synth)
    assert result["passed"] is True, "Should pass when answer contains 'synthetic'"
    assert result["required"] is True

    # Fail: answer lacks any caveat keyword
    capture_fail = [{"type": "answer", "text": "The estimate is 5 bps."}]
    result = layer2.check_caveat_presence(capture_fail, q_synth)
    assert result["passed"] is False, "Should fail when answer has no caveat keyword"

    # Not-required: ordinary question → always pass
    q_plain = Question(
        id="test_plain",
        text="What is the TWAP cost?",
        expected_tool_path=[],
        gt_values={},
    )
    result = layer2.check_caveat_presence(capture_fail, q_plain)
    assert result["passed"] is True
    assert result["required"] is False


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


# N7 — byte-identity check: prod system prompt must be exactly _SYSTEM_PROMPT
def test_eval_mode_off_system_prompt_unchanged() -> None:
    """With eval_mode=False the system prompt is byte-identical to _SYSTEM_PROMPT."""
    captured_prompts: list[str] = []

    def _spy_call(system_prompt, *args, **kwargs):
        captured_prompts.append(system_prompt)
        return _make_fake_response("42 bps.")

    with patch("agent.loop.llm.call", side_effect=_spy_call):
        from agent.loop import _SYSTEM_PROMPT, run
        run("What is the cost of AAPL?", eval_mode=False)

    assert len(captured_prompts) == 1
    assert captured_prompts[0] == _SYSTEM_PROMPT, (
        "eval_mode=False must pass _SYSTEM_PROMPT byte-for-byte; "
        "got a modified string — check loop.py prompt construction"
    )


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
