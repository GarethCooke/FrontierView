"""
Layer 2: deterministic trace-based behavioural checks.

Each check receives the capture list produced by loop.run(..., _eval_capture=[])
and the Question being evaluated.  All checks are deterministic — they do not
call the model.

Capture event shapes (from loop.py):
  {"type": "tool_call",   "name": str, "args": dict, "key": str}
  {"type": "tool_result", "name": str, "key": str,
                          "summary": dict, "error": str | None}
  {"type": "answer",      "text": str}
"""
from __future__ import annotations

import re
from collections import Counter

from agent.eval.questions.types import Question
from agent.eval.scorer import DEFAULT_RTOL, DEFAULT_ATOL

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?")

_SYNTHETIC_KEYWORDS = [
    "synthetic", "calibrated estimate", "recovery", "not from stored",
    "estimated parameter", "approximate",
]


def _extract_numbers(text: str) -> list[float]:
    """Return all numeric literals from a prose string."""
    results = []
    for m in _NUMBER_RE.finditer(text):
        try:
            results.append(float(m.group()))
        except ValueError:
            pass
    return results


def _flatten_summary_numbers(summaries: list[dict]) -> list[float]:
    """Recursively collect all floats/ints from a list of summary dicts."""
    numbers: list[float] = []

    def _walk(obj: object) -> None:
        if isinstance(obj, (int, float)):
            numbers.append(float(obj))
        elif isinstance(obj, dict):
            for v in obj.values():
                _walk(v)
        elif isinstance(obj, list):
            for item in obj:
                _walk(item)

    for s in summaries:
        _walk(s)
    return numbers


def _answer_without_eval_block(text: str) -> str:
    """Strip the <eval_answer> block before groundedness checking."""
    return re.sub(r"<eval_answer>.*?</eval_answer>", "", text, flags=re.DOTALL)


def _within_tol(n: float, pool: list[float], rtol: float = 0.02, atol: float = 0.01) -> bool:
    """True if n is within tolerance of any number in pool."""
    for p in pool:
        if p == 0.0:
            if abs(n) <= atol:
                return True
        else:
            if abs(n - p) / abs(p) <= rtol or abs(n - p) <= atol:
                return True
    return False


# ---------------------------------------------------------------------------
# Check 1: tool-path efficiency
# ---------------------------------------------------------------------------


def check_tool_path(
    capture: list[dict],
    question: Question,
) -> dict:
    """Check that all expected minimal tools were called; flag extras."""
    actual_names = [e["name"] for e in capture if e["type"] == "tool_call"]
    actual_set = set(actual_names)
    expected_set = set(question.expected_tool_path)

    missing = sorted(expected_set - actual_set)
    extra = sorted(actual_set - expected_set)
    passed = len(missing) == 0

    return {
        "passed": passed,
        "missing_tools": missing,
        "extra_tools": extra,
        "actual_call_sequence": actual_names,
    }


# ---------------------------------------------------------------------------
# Check 2: no excess duplicate calls
# ---------------------------------------------------------------------------


def check_no_excess_duplicates(capture: list[dict]) -> dict:
    """Verify no tool key was called more than twice (guard allows one nudge)."""
    key_counts: Counter[str] = Counter()
    for e in capture:
        if e["type"] == "tool_call":
            key_counts[e["key"]] += 1

    excess = {k: c for k, c in key_counts.items() if c > 2}
    return {
        "passed": len(excess) == 0,
        "excess_calls": excess,  # {tool_key: count}
    }


# ---------------------------------------------------------------------------
# Check 3: synthetic caveat presence
# ---------------------------------------------------------------------------


def check_synthetic_caveat(capture: list[dict], question: Question) -> dict:
    """If question.synthetic is True, the answer must contain a synthetic caveat."""
    if not question.synthetic:
        return {"passed": True, "required": False}

    answer_events = [e for e in capture if e["type"] == "answer"]
    if not answer_events:
        return {"passed": False, "required": True, "reason": "no answer captured"}

    answer_text = answer_events[-1]["text"].lower()
    found = any(kw in answer_text for kw in _SYNTHETIC_KEYWORDS)
    return {
        "passed": found,
        "required": True,
        "keywords_checked": _SYNTHETIC_KEYWORDS,
    }


# ---------------------------------------------------------------------------
# Check 4: numeric groundedness
# ---------------------------------------------------------------------------


def check_numeric_groundedness(capture: list[dict]) -> dict:
    """Every number in the final prose must trace to a model-visible tool summary.

    Model-visible = the summary half of the two-part result shape, NOT detail_id payloads.
    The eval_answer block is excluded from the prose before checking.

    A number is considered grounded if it is within 2% (relative) or 0.01 (absolute) of
    any number found in any successful tool result summary.  This catches hallucinated
    values while tolerating display rounding.
    """
    answer_events = [e for e in capture if e["type"] == "answer"]
    if not answer_events:
        return {"passed": True, "ungrounded": [], "reason": "no answer to check"}

    prose = _answer_without_eval_block(answer_events[-1]["text"])

    # Collect model-visible numbers (summaries of successful tool results only)
    summaries = [
        e["summary"] for e in capture
        if e["type"] == "tool_result" and not e.get("error") and e.get("summary")
    ]
    visible_numbers = _flatten_summary_numbers(summaries)

    # Numbers to check: prose floats with an absolute value > 0.1
    # (ignore plain integers like "5 symbols" or "2 hours")
    prose_numbers = [n for n in _extract_numbers(prose) if abs(n) > 0.1]

    ungrounded = [n for n in prose_numbers if not _within_tol(n, visible_numbers)]

    return {
        "passed": len(ungrounded) == 0,
        "ungrounded_numbers": ungrounded,
        "visible_pool_size": len(visible_numbers),
        "prose_numbers_checked": len(prose_numbers),
    }


# ---------------------------------------------------------------------------
# Run all Layer 2 checks
# ---------------------------------------------------------------------------


def run_all(capture: list[dict], question: Question) -> dict:
    """Run all Layer 2 checks and return a combined result dict."""
    tool_path = check_tool_path(capture, question)
    duplicates = check_no_excess_duplicates(capture)
    caveat = check_synthetic_caveat(capture, question)
    groundedness = check_numeric_groundedness(capture)

    all_passed = (
        tool_path["passed"]
        and duplicates["passed"]
        and caveat["passed"]
        and groundedness["passed"]
    )

    return {
        "passed": all_passed,
        "tool_path": tool_path,
        "no_excess_duplicates": duplicates,
        "synthetic_caveat": caveat,
        "numeric_groundedness": groundedness,
    }
