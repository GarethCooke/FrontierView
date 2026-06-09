"""Unit tests for check_numeric_groundedness and _within_tol (layer2.py).

Six cases cover:
  1. Direct match grounded
  2. Derived delta grounded (a − b)
  3. Sum grounded (a + b)
  4. Comma parsing — "100,000" → 100000.0 (S1b regression)
  5. Echoed constant grounded — question literal not in any summary (S1c regression)
  6. Anti-leniency — hallucinated ratio flagged under tightened _within_tol
"""
from __future__ import annotations

from agent.eval import layer2
from agent.eval.questions.types import Question


def _q(text: str = "What is the cost?") -> Question:
    return Question(id="test_q", text=text, expected_tool_path=[], gt_values={})


def _cap(summary: dict, answer: str) -> list[dict]:
    return [
        {"type": "tool_result", "name": "cost_and_variance", "key": "k1",
         "summary": summary, "error": None},
        {"type": "answer", "text": answer},
    ]


# ---------------------------------------------------------------------------
# 1. Direct match grounded
# ---------------------------------------------------------------------------


def test_direct_match_grounded() -> None:
    result = layer2.check_numeric_groundedness(
        _cap({"expected_cost_bps": 12.5}, "The expected cost is 12.5 bps."),
        _q(),
    )
    assert result["passed"] is True
    assert result["ungrounded_numbers"] == []


# ---------------------------------------------------------------------------
# 2. Derived delta grounded (a − b)
# ---------------------------------------------------------------------------


def test_derived_delta_grounded() -> None:
    capture = [
        {"type": "tool_result", "name": "cost_and_variance", "key": "k1",
         "summary": {"expected_cost_bps": 15.0}, "error": None},
        {"type": "tool_result", "name": "cost_and_variance", "key": "k2",
         "summary": {"expected_cost_bps": 12.0}, "error": None},
        {"type": "answer",
         "text": "TWAP costs 15.0 bps, ac_linear costs 12.0 bps, a delta of 3.0 bps."},
    ]
    result = layer2.check_numeric_groundedness(capture, _q())
    assert result["passed"] is True
    assert result["ungrounded_numbers"] == []


# ---------------------------------------------------------------------------
# 3. Sum grounded (a + b)
# ---------------------------------------------------------------------------


def test_sum_grounded() -> None:
    capture = [
        {"type": "tool_result", "name": "cost_and_variance", "key": "k1",
         "summary": {"cost_component_a": 8.0}, "error": None},
        {"type": "tool_result", "name": "cost_and_variance", "key": "k2",
         "summary": {"cost_component_b": 5.0}, "error": None},
        {"type": "answer", "text": "Combined cost is 13.0 bps."},
    ]
    result = layer2.check_numeric_groundedness(capture, _q())
    assert result["passed"] is True
    assert result["ungrounded_numbers"] == []


# ---------------------------------------------------------------------------
# 4. Comma parsing — S1b regression
# ---------------------------------------------------------------------------


def test_comma_parsing_grounded() -> None:
    """'100,000' in prose is parsed as 100000.0 and matched against the summary."""
    result = layer2.check_numeric_groundedness(
        _cap({"order_size": 100000.0, "expected_cost_bps": 12.5},
             "The order of 100,000 shares costs 12.5 bps."),
        _q(),
    )
    assert result["passed"] is True
    assert result["ungrounded_numbers"] == []


# ---------------------------------------------------------------------------
# 5. Echoed constant grounded — S1c regression
# ---------------------------------------------------------------------------


def test_question_literal_not_flagged() -> None:
    """Numbers from question.text (order size, horizon) are never flagged,
    even when absent from every tool summary."""
    q = _q("What is the cost for an order of 100000 shares over 2 hours?")
    # Summary contains only bps/variance — NOT the order size or horizon.
    result = layer2.check_numeric_groundedness(
        _cap({"expected_cost_bps": 12.5, "variance_bps2": 0.30},
             "For 100000 shares over 2 hours, the cost is 12.5 bps with variance 0.30."),
        q,
    )
    assert result["passed"] is True
    assert result["ungrounded_numbers"] == []


# ---------------------------------------------------------------------------
# 6. Anti-leniency — hallucinated ratio must be flagged
# ---------------------------------------------------------------------------


def test_hallucinated_ratio_flagged() -> None:
    """50.0 = 15.0 / 0.30 is a ratio of pool members but NOT a direct value or
    sum/difference.  The tightened _within_tol must flag it as ungrounded.

    With the old _within_tol (which included a/b candidates), 15.0 / 0.30 = 50.0
    would be grounded, silently hiding the hallucination.  This test fails against
    that old logic and passes against the tightened version.
    """
    q = _q("What is the cost for an order of 100000 shares over 2 hours?")
    # Pool: 15.0, 0.30 (from summary) + 100000.0, 2.0 (from question text).
    # 50.0 is none of those, and not any pairwise sum or difference of them.
    result = layer2.check_numeric_groundedness(
        _cap({"expected_cost_bps": 15.0, "variance_bps2": 0.30},
             "The cost is 15.0 bps. The normalized figure is 50.0."),
        q,
    )
    assert result["passed"] is False
    assert 50.0 in result["ungrounded_numbers"]
