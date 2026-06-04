from api.market_impact import (
    TRADING_HOURS_PER_DAY,
    compute_cost_variance,
    schedule_ac_linear,
    schedule_twap,
)
from api.parameters import SYMBOL_PARAMS

from agent.tools import dispatch


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

    assert result["expected_cost_bps"] == round(cost, 4)
    assert result["variance_bps2"] == round(variance, 4)


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

    assert result["expected_cost_bps"] == round(cost, 4)
    assert result["variance_bps2"] == round(variance, 4)


def test_unknown_tool_returns_error_dict():
    """Unknown tool must return {'error': ...}, not raise."""
    result = dispatch("nonexistent_tool", {})
    assert isinstance(result, dict)
    assert "error" in result
    assert "nonexistent_tool" in result["error"]
