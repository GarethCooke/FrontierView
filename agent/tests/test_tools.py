"""
Tool unit tests.

DRY equality tests: every compute tool must produce numbers identical to a
direct FV core call.  Read-only test: sweep must not mutate global state.
"""
import copy
import math

from api.market_impact import (
    TRADING_HOURS_PER_DAY,
    compute_cost_variance,
    schedule_ac_linear,
    schedule_twap,
)
from api.parameters import SYMBOL_PARAMS

from agent.tools import dispatch


# ---------------------------------------------------------------------------
# DRY equality tests — Phase 1 tools (migrated to two-part shape)
# ---------------------------------------------------------------------------


def test_cost_and_variance_twap_equals_core():
    """DRY: tool must produce the same numbers as a direct core call."""
    params = SYMBOL_PARAMS["AAPL"]
    v_hourly = params.adv / TRADING_HOURS_PER_DAY
    dt = 2.0 / 13
    schedule = schedule_twap(13, 100_000, v_hourly, dt)
    cost, variance = compute_cost_variance(schedule, 100_000, params, 2.0)

    result = dispatch(
        "cost_and_variance",
        {
            "symbol": "AAPL",
            "order_size": 100_000,
            "horizon_hours": 2.0,
            "schedule_type": "twap",
            "n_bins": 13,
        },
    )

    assert "summary" in result, f"Expected two-part shape, got: {result}"
    assert "detail_id" in result
    s = result["summary"]
    assert s["expected_cost_bps"] == round(cost, 4)
    assert s["variance_bps2"] == round(variance, 4)


def test_optimal_schedule_cost_equals_core():
    """DRY: optimal_schedule cost/variance must equal direct core call."""
    params = SYMBOL_PARAMS["AAPL"]
    schedule = schedule_ac_linear(13, 100_000, 2.0, params, 1e-6)
    cost, variance = compute_cost_variance(schedule, 100_000, params, 2.0)

    result = dispatch(
        "optimal_schedule",
        {
            "symbol": "AAPL",
            "order_size": 100_000,
            "horizon_hours": 2.0,
            "lambda_risk": 1e-6,
            "n_bins": 13,
        },
    )

    assert "summary" in result
    assert "detail_id" in result
    s = result["summary"]
    assert s["expected_cost_bps"] == round(cost, 4)
    assert s["variance_bps2"] == round(variance, 4)


# ---------------------------------------------------------------------------
# DRY equality tests — new compute tools
# ---------------------------------------------------------------------------


def test_compare_schedules_twap_cost_equals_core():
    """compare_schedules TWAP cost must equal a direct cost_and_variance call."""
    direct = dispatch(
        "cost_and_variance",
        {
            "symbol": "AAPL",
            "order_size": 100_000,
            "horizon_hours": 2.0,
            "schedule_type": "twap",
            "n_bins": 13,
        },
    )
    compared = dispatch(
        "compare_schedules",
        {
            "symbol": "AAPL",
            "side": "sell",
            "order_size": 100_000,
            "horizon_hours": 2.0,
            "schedules": ["twap", "ac_linear"],
            "n_bins": 13,
        },
    )

    assert "summary" in compared
    twap_row = next(
        r for r in compared["summary"]["schedules"] if r["schedule"] == "twap"
    )
    assert twap_row["expected_cost_bps"] == direct["summary"]["expected_cost_bps"]
    assert twap_row["variance_bps2"] == direct["summary"]["variance_bps2"]


def test_efficient_frontier_endpoints_match_cost_and_variance():
    """efficient_frontier low-λ endpoint cost must match a direct ac_linear cost_and_variance."""
    ef = dispatch(
        "efficient_frontier",
        {
            "symbol": "AAPL",
            "side": "sell",
            "order_size": 100_000,
            "horizon_hours": 2.0,
            "lambda_range": [1e-9, 1e-1],
            "n_points": 5,
            "n_bins": 13,
        },
    )
    assert "summary" in ef
    low_end = ef["summary"]["low_lambda_end"]

    direct = dispatch(
        "cost_and_variance",
        {
            "symbol": "AAPL",
            "order_size": 100_000,
            "horizon_hours": 2.0,
            "schedule_type": "ac_linear",
            "lambda_risk": 1e-9,
            "n_bins": 13,
        },
    )
    assert low_end["expected_cost_bps"] == direct["summary"]["expected_cost_bps"]


def test_sweep_calibrated_eta_base_matches_cost_and_variance():
    """sweep at the base η value must match a direct ac_linear cost call."""
    from api.parameters import ALMGREN_ETA

    direct = dispatch(
        "cost_and_variance",
        {
            "symbol": "AAPL",
            "order_size": 100_000,
            "horizon_hours": 2.0,
            "schedule_type": "ac_linear",
            "lambda_risk": 1e-6,
            "n_bins": 13,
        },
    )
    # Sweep eta with a range that includes the base value as one of the endpoints
    swp = dispatch(
        "sweep",
        {
            "symbol": "AAPL",
            "side": "sell",
            "order_size": 100_000,
            "horizon_hours": 2.0,
            "schedule": "ac_linear",
            "param": "calibrated.eta",
            "param_range": [ALMGREN_ETA, ALMGREN_ETA * 2],
            "n_points": 2,
            "lambda_risk": 1e-6,
            "n_bins": 13,
        },
    )
    assert "summary" in swp
    # First point is at the base eta value
    assert swp["summary"]["cost_at_range_start_bps"] == direct["summary"]["expected_cost_bps"]


# ---------------------------------------------------------------------------
# Two-part result shape
# ---------------------------------------------------------------------------


def test_all_tools_return_two_part_shape():
    """Every tool must return {summary: {...}, detail_id: str}."""
    calls = [
        ("cost_and_variance", {"symbol": "AAPL", "order_size": 10_000, "horizon_hours": 2.0, "schedule_type": "twap"}),
        ("optimal_schedule", {"symbol": "AAPL", "order_size": 10_000, "horizon_hours": 2.0, "lambda_risk": 1e-6}),
        ("compare_schedules", {"symbol": "AAPL", "side": "sell", "order_size": 10_000, "horizon_hours": 2.0, "schedules": ["twap", "ac_linear"]}),
        ("efficient_frontier", {"symbol": "AAPL", "side": "sell", "order_size": 10_000, "horizon_hours": 2.0}),
        ("sweep", {"symbol": "AAPL", "side": "sell", "order_size": 10_000, "horizon_hours": 2.0, "schedule": "twap", "param": "calibrated.eta", "param_range": [0.1, 0.2]}),
        ("list_symbols", {}),
        ("get_symbol_reference", {"symbol": "AAPL"}),
        ("describe_model", {}),
    ]
    for name, args in calls:
        result = dispatch(name, args)
        assert "summary" in result, f"Tool '{name}' missing 'summary' key: {result}"
        assert "detail_id" in result, f"Tool '{name}' missing 'detail_id' key: {result}"
        assert isinstance(result["detail_id"], str), f"Tool '{name}' detail_id is not a string"


# ---------------------------------------------------------------------------
# Error and validation behaviour
# ---------------------------------------------------------------------------


def test_unknown_tool_returns_structured_error():
    """Unknown tool must return {error: 'UnknownTool', allowed: [...]}, not raise."""
    result = dispatch("nonexistent_tool", {})
    assert isinstance(result, dict)
    assert result.get("error") == "UnknownTool"
    assert "allowed" in result
    assert "cost_and_variance" in result["allowed"]


def test_unknown_symbol_returns_structured_error():
    """Out-of-enum symbol must return a structured InvalidArgument error."""
    for tool in ("cost_and_variance", "optimal_schedule"):
        args = {"symbol": "BOGUS", "order_size": 100_000, "horizon_hours": 2.0,
                "schedule_type": "twap", "lambda_risk": 1e-6}
        result = dispatch(tool, args)
        assert result.get("error") == "InvalidArgument", (
            f"Expected InvalidArgument for {tool}, got: {result}"
        )
        assert "BOGUS" in result.get("detail", "")


def test_all_schedule_types_finite_and_distinct():
    """Fix 1 lock-in: all four schedule types yield finite, not-all-identical results."""
    schedule_types = ["twap", "front_loaded", "back_loaded", "ac_linear"]
    results = []
    for stype in schedule_types:
        result = dispatch(
            "cost_and_variance",
            {
                "symbol": "AAPL",
                "order_size": 100_000,
                "horizon_hours": 6.5,
                "schedule_type": stype,
                "n_bins": 13,
            },
        )
        assert "error" not in result, f"Unexpected error for {stype}: {result}"
        s = result["summary"]
        cost = s["expected_cost_bps"]
        var = s["variance_bps2"]
        assert math.isfinite(cost), f"Non-finite cost for {stype}: {cost}"
        assert math.isfinite(var), f"Non-finite variance for {stype}: {var}"
        results.append((cost, var))

    assert len(set(results)) > 1, "All schedule types returned identical results — format collapse?"


def test_negative_order_size_returns_error():
    result = dispatch(
        "cost_and_variance",
        {"symbol": "AAPL", "order_size": -100, "horizon_hours": 2.0, "schedule_type": "twap"},
    )
    assert result.get("error") == "InvalidArgument"


def test_lambda_zero_returns_error():
    result = dispatch(
        "optimal_schedule",
        {"symbol": "AAPL", "order_size": 100_000, "horizon_hours": 2.0, "lambda_risk": 0},
    )
    assert result.get("error") == "InvalidArgument"


def test_compare_schedules_weights_sum_check():
    result = dispatch(
        "compare_schedules",
        {
            "symbol": "AAPL",
            "side": "sell",
            "order_size": 100_000,
            "horizon_hours": 2.0,
            "schedules": ["twap", [0.5, 0.3, 0.1]],  # sums to 0.9, not 1.0
        },
    )
    assert result.get("error") == "InvalidArgument"


def test_sweep_invalid_param_returns_error():
    result = dispatch(
        "sweep",
        {
            "symbol": "AAPL",
            "side": "sell",
            "order_size": 100_000,
            "horizon_hours": 2.0,
            "schedule": "twap",
            "param": "unknown.bogus",
            "param_range": [0.1, 0.5],
        },
    )
    assert result.get("error") == "InvalidArgument"


# ---------------------------------------------------------------------------
# Read-only test — sweep must not mutate global state
# ---------------------------------------------------------------------------


def test_sweep_does_not_mutate_global_params():
    """After any sweep call, SYMBOL_PARAMS must be byte-identical to its pre-call state."""
    # Take a deep snapshot before
    before = {
        sym: copy.copy(params) for sym, params in SYMBOL_PARAMS.items()
    }

    for param_name in ("calibrated.eta", "calibrated.gamma", "market.sigma",
                       "market.half_spread", "market.adv", "structural.temp_exponent"):
        dispatch(
            "sweep",
            {
                "symbol": "AAPL",
                "side": "sell",
                "order_size": 50_000,
                "horizon_hours": 2.0,
                "schedule": "twap",
                "param": param_name,
                "param_range": [0.05, 0.5],
                "n_points": 3,
            },
        )

    for sym, pre in before.items():
        post = SYMBOL_PARAMS[sym]
        assert post == pre, (
            f"SYMBOL_PARAMS['{sym}'] was mutated during sweep!\n"
            f"  Before: {pre}\n  After:  {post}"
        )


def test_no_tool_writes_fv_state():
    """Running every tool must leave SYMBOL_PARAMS unchanged."""
    before = {sym: copy.copy(p) for sym, p in SYMBOL_PARAMS.items()}

    calls = [
        ("cost_and_variance", {"symbol": "AAPL", "order_size": 10_000, "horizon_hours": 1.0, "schedule_type": "twap"}),
        ("optimal_schedule", {"symbol": "AAPL", "order_size": 10_000, "horizon_hours": 1.0, "lambda_risk": 1e-6}),
        ("compare_schedules", {"symbol": "AAPL", "side": "sell", "order_size": 10_000, "horizon_hours": 1.0, "schedules": ["twap", "front_loaded"]}),
        ("efficient_frontier", {"symbol": "AAPL", "side": "sell", "order_size": 10_000, "horizon_hours": 1.0}),
        ("list_symbols", {}),
        ("get_symbol_reference", {"symbol": "AAPL"}),
        ("describe_model", {}),
    ]
    for name, args in calls:
        dispatch(name, args)

    for sym, pre in before.items():
        assert SYMBOL_PARAMS[sym] == pre, (
            f"SYMBOL_PARAMS['{sym}'] was mutated by a tool call!"
        )


# ---------------------------------------------------------------------------
# ADV warning
# ---------------------------------------------------------------------------


def test_large_order_carries_warning():
    """Order >> ADV must include a warning in the summary, not crash."""
    result = dispatch(
        "cost_and_variance",
        {
            "symbol": "JPM",
            "order_size": 50_000_000,  # ~4× ADV
            "horizon_hours": 6.5,
            "schedule_type": "twap",
        },
    )
    assert "summary" in result
    assert "warning" in result["summary"]


# ---------------------------------------------------------------------------
# compare_schedules label uniqueness
# ---------------------------------------------------------------------------


def test_compare_schedules_custom_vector_labels_are_distinct():
    """Two same-length custom weight vectors must get distinct labels."""
    result = dispatch(
        "compare_schedules",
        {
            "symbol": "AAPL",
            "side": "sell",
            "order_size": 10_000,
            "horizon_hours": 2.0,
            "schedules": [[0.6, 0.4], [0.3, 0.7]],  # same length, different weights
        },
    )
    assert "summary" in result
    labels = [r["schedule"] for r in result["summary"]["schedules"]]
    assert len(labels) == len(set(labels)), f"Duplicate schedule labels: {labels}"
