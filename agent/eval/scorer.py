"""
Layer 1 scorer: tolerance-based numeric answer correctness.

Default tolerances are set to absorb display rounding to ~3 significant figures
while remaining tight enough to catch genuine errors.
"""
from __future__ import annotations

import json
import re

# Default relative tolerance: 0.5% absorbs 3-sig-fig rounding comfortably.
DEFAULT_RTOL = 5e-3
# Absolute floor: prevents spurious relative-error blow-up near zero.
DEFAULT_ATOL = 1e-4

_EVAL_BLOCK_RE = re.compile(r"<eval_answer>(.*?)</eval_answer>", re.DOTALL)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def parse_eval_answer(text: str) -> dict | None:
    """Extract and JSON-parse the <eval_answer> block from the agent's response.

    Returns the parsed dict (expected shape: {"values": {...}, "synthetic": bool})
    or None if the block is absent or malformed.
    """
    m = _EVAL_BLOCK_RE.search(text)
    if not m:
        return None
    try:
        data = json.loads(m.group(1))
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def extract_values(parsed: dict | None) -> dict[str, float]:
    """Return the numeric values dict from a parsed eval_answer, or {}."""
    if parsed is None:
        return {}
    values = parsed.get("values", {})
    if not isinstance(values, dict):
        return {}
    # bool is a subclass of int — exclude it so a stray True/False is not scored as 1/0.
    return {
        k: float(v)
        for k, v in values.items()
        if isinstance(v, (int, float)) and not isinstance(v, bool)
    }


def is_synthetic(parsed: dict | None) -> bool:
    """Return True if the eval_answer block marks the result as synthetic."""
    if parsed is None:
        return False
    return bool(parsed.get("synthetic", False))


# ---------------------------------------------------------------------------
# Tolerance check
# ---------------------------------------------------------------------------


def _within_tolerance(actual: float, expected: float, rtol: float, atol: float) -> bool:
    if expected == 0.0:
        return abs(actual) <= atol
    rel_err = abs(actual - expected) / abs(expected)
    abs_err = abs(actual - expected)
    return rel_err <= rtol or abs_err <= atol


def score_values(
    actual: dict[str, float],
    expected: dict[str, float],
    overrides: dict[str, dict] | None = None,
) -> dict[str, bool]:
    """Score each key in expected against actual with tolerance.

    Returns {key: passed_bool} for every key in expected.
    Keys absent from actual are scored False.
    """
    overrides = overrides or {}
    results: dict[str, bool] = {}
    for key, exp_val in expected.items():
        if key not in actual:
            results[key] = False
            continue
        tol = overrides.get(key, {})
        rtol = float(tol.get("rtol", DEFAULT_RTOL))
        atol = float(tol.get("atol", DEFAULT_ATOL))
        results[key] = _within_tolerance(actual[key], exp_val, rtol, atol)
    return results


def all_passed(scores: dict[str, bool]) -> bool:
    return bool(scores) and all(scores.values())
