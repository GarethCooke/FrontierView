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
from agent.eval.scorer import DEFAULT_ATOL, DEFAULT_RTOL

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Matches comma-grouped numbers (e.g. "100,000") before falling back to plain floats.
_COMMA_NUMBER_RE = re.compile(r"-?\d{1,3}(?:,\d{3})+(?:\.\d+)?")
_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?")

# Caveat keywords: caveat-specific phrases only (no "approximate" — too broad).
# Covers synthetic-data and >ADV reliability caveats.
_CAVEAT_KEYWORDS = [
    "synthetic",
    "calibrated estimate",
    "recovery",
    "not from stored",
    "estimated parameter",
    # reliability / large-order caveats
    "unreliable",
    "exceeds adv",
    "outside the model",
    "less reliable",
    "treat these estimates",
    "indicative only",
    "may be less reliable",
]


def _extract_numbers(text: str) -> list[float]:
    """Return all numeric literals from a prose string.

    Handles comma-grouped numbers (e.g. "100,000" → 100000.0) before
    falling back to plain numeric literals.
    """
    # Strip thousands separators from grouped numbers first
    cleaned = _COMMA_NUMBER_RE.sub(lambda m: m.group().replace(",", ""), text)
    results = []
    for m in _NUMBER_RE.finditer(cleaned):
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


def _within_tol(
    n: float, pool: list[float], rtol: float = 0.02, atol: float = 0.01
) -> bool:
    """True if n is within tolerance of any number in the pool, or within tolerance
    of a pairwise sum or difference of pool numbers.

    Only direct match and pairwise ± are covered. Ratio and percentage candidates
    (a/b, b/a, 100·a/b, 100·b/a) are intentionally excluded: over a mixed-magnitude
    pool they scatter ~150 candidates across the number line, each with a 2% band,
    making it easy for a hallucinated mid-range value to match by coincidence.
    If a specific question's correct answer includes a ratio, include the derived
    value in that tool's summary instead of re-loosening this check globally.
    """
    # Direct match
    for p in pool:
        if p == 0.0:
            if abs(n) <= atol:
                return True
        else:
            if abs(n - p) / abs(p) <= rtol or abs(n - p) <= atol:
                return True

    # Pairwise derived: difference and sum only
    for i, a in enumerate(pool):
        for b in pool[i + 1 :]:
            for c in (a - b, b - a, a + b):
                if c == 0.0:
                    if abs(n) <= atol:
                        return True
                else:
                    if abs(n - c) / abs(c) <= rtol or abs(n - c) <= atol:
                        return True

    return False


# ---------------------------------------------------------------------------
# Check 1: tool-path efficiency
# ---------------------------------------------------------------------------


def check_tool_path(
    capture: list[dict],
    question: Question,
) -> dict:
    """Check that a valid tool path was used.

    Passes if the actual calls satisfy expected_tool_path OR any path listed
    in question.acceptable_tool_paths.  Extra calls beyond the matched path
    are flagged but do not cause a failure (N3: extras are an efficiency signal,
    not a correctness failure).
    """
    actual_names = [e["name"] for e in capture if e["type"] == "tool_call"]
    actual_set = set(actual_names)
    expected_set = set(question.expected_tool_path)

    primary_ok = expected_set.issubset(actual_set)
    alt_ok = any(
        set(alt).issubset(actual_set) for alt in (question.acceptable_tool_paths or [])
    )
    passed = primary_ok or alt_ok

    missing = sorted(expected_set - actual_set) if not passed else []

    # Extra calls: tools not in the expected (primary) path.
    # Non-failing — reported as an efficiency signal.
    extra = sorted(actual_set - expected_set)

    return {
        "passed": passed,
        "missing_tools": missing,
        "extra_tools": extra,
        "extra_count": len(extra),
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
# Check 3: caveat presence (generalised from synthetic-caveat check)
# ---------------------------------------------------------------------------


def check_caveat_presence(capture: list[dict], question: Question) -> dict:
    """If the question requires a caveat, the answer must contain one.

    Triggered by question.requires_caveat=True or question.synthetic=True
    (synthetic implies a caveat is needed).  The keyword set covers both
    synthetic-data caveats and >ADV reliability warnings.
    """
    required = question.requires_caveat or question.synthetic
    if not required:
        return {"passed": True, "required": False}

    answer_events = [e for e in capture if e["type"] == "answer"]
    if not answer_events:
        return {"passed": False, "required": True, "reason": "no answer captured"}

    answer_text = answer_events[-1]["text"].lower()
    found = any(kw in answer_text for kw in _CAVEAT_KEYWORDS)
    return {
        "passed": found,
        "required": True,
        "keywords_checked": _CAVEAT_KEYWORDS,
        # TODO: add synthetic=True eval questions when a recovery-demo tool is added
    }


# ---------------------------------------------------------------------------
# Check 4: numeric groundedness
# ---------------------------------------------------------------------------


def check_numeric_groundedness(capture: list[dict], question: Question) -> dict:
    """Every number in the final prose must trace to a model-visible tool summary
    or be a simple arithmetic derivation over tool result numbers.

    Model-visible = the summary half of the two-part result shape, NOT detail_id payloads.
    The eval_answer block is excluded from the prose before checking.

    A number is grounded if it is within tolerance of:
      - Any number in the tool-result pool (direct match), OR
      - A pairwise sum or difference of pool numbers.

    The pool is pre-seeded with the question's own numeric literals (order size, horizon,
    λ, sweep-range endpoints) so those constants are never flagged as ungrounded.
    """
    answer_events = [e for e in capture if e["type"] == "answer"]
    if not answer_events:
        return {"passed": True, "ungrounded": [], "reason": "no answer to check"}

    prose = _answer_without_eval_block(answer_events[-1]["text"])

    # Collect model-visible numbers (summaries of successful tool results only)
    summaries = [
        e["summary"]
        for e in capture
        if e["type"] == "tool_result" and not e.get("error") and e.get("summary")
    ]
    visible_numbers = _flatten_summary_numbers(summaries)

    # Seed pool with question's own numeric literals so echoed constants are grounded
    question_numbers = _extract_numbers(question.text)
    pool = visible_numbers + question_numbers

    # Numbers to check: all prose numbers (comma-grouped parsing handled in _extract_numbers).
    # We include all numbers here; question literals already in the pool will pass trivially.
    prose_numbers = _extract_numbers(prose)

    ungrounded = [n for n in prose_numbers if not _within_tol(n, pool)]

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
    caveat = check_caveat_presence(capture, question)
    groundedness = check_numeric_groundedness(capture, question)

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
        "caveat_presence": caveat,
        "numeric_groundedness": groundedness,
    }
