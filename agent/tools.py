from __future__ import annotations

from api.market_impact import (
    TRADING_HOURS_PER_DAY,
    compute_cost_variance,
    schedule_ac_linear,
    schedule_back_loaded,
    schedule_front_loaded,
    schedule_twap,
)
from api.parameters import SYMBOL_PARAMS

_ALLOWED_SYMBOLS = frozenset(SYMBOL_PARAMS)
_SYMBOLS_SORTED = sorted(_ALLOWED_SYMBOLS)
_SYMBOL_LIST = ", ".join(_SYMBOLS_SORTED)

TOOLS = [
    {
        "name": "cost_and_variance",
        "description": (
            "Compute expected execution cost (bps) and variance (bps²) "
            "for a given trade execution schedule."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": f"Ticker symbol. One of: {_SYMBOL_LIST}.",
                    "enum": _SYMBOLS_SORTED,
                },
                "order_size": {
                    "type": "number",
                    "description": "Total order size in shares.",
                },
                "horizon_hours": {
                    "type": "number",
                    "description": "Execution horizon in trading hours.",
                },
                "schedule_type": {
                    "type": "string",
                    "description": (
                        "Execution schedule type: twap (flat), "
                        "front_loaded (aggressive early), "
                        "back_loaded (aggressive late), "
                        "or ac_linear (Almgren-Chriss optimal)."
                    ),
                    "enum": ["twap", "front_loaded", "back_loaded", "ac_linear"],
                },
                "lambda_risk": {
                    "type": "number",
                    "description": (
                        "Risk-aversion parameter for ac_linear schedule. "
                        "Higher = more front-loading. Default: 1e-6."
                    ),
                },
                "n_bins": {
                    "type": "integer",
                    "description": "Number of time bins; each bin's length = horizon_hours / n_bins. Default: 13.",
                },
            },
            "required": ["symbol", "order_size", "horizon_hours", "schedule_type"],
        },
    },
    {
        "name": "optimal_schedule",
        "description": (
            "Compute the Almgren-Chriss optimal execution schedule for a given "
            "risk-aversion level, returning the schedule bins and their expected "
            "cost and variance."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": f"Ticker symbol. One of: {_SYMBOL_LIST}.",
                    "enum": _SYMBOLS_SORTED,
                },
                "order_size": {
                    "type": "number",
                    "description": "Total order size in shares.",
                },
                "horizon_hours": {
                    "type": "number",
                    "description": "Execution horizon in trading hours.",
                },
                "lambda_risk": {
                    "type": "number",
                    "description": (
                        "Risk-aversion parameter λ. Higher λ = more front-loading "
                        "to reduce variance. Typical range: 1e-9 (cost-minimising) "
                        "to 1e-1 (variance-minimising). Moderate risk aversion: 1e-6."
                    ),
                },
                "n_bins": {
                    "type": "integer",
                    "description": "Number of time bins; each bin's length = horizon_hours / n_bins. Default: 13.",
                },
            },
            "required": ["symbol", "order_size", "horizon_hours", "lambda_risk"],
        },
    },
]

_ALLOWED = frozenset(t["name"] for t in TOOLS)


def dispatch(name: str, args: dict) -> dict:
    if name not in _ALLOWED:
        return {"error": f"Unknown tool '{name}'. Allowed tools: {sorted(_ALLOWED)}"}
    try:
        if name == "cost_and_variance":
            return _cost_and_variance(**args)
        return _optimal_schedule(**args)
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


def _validate_symbol(symbol: str) -> dict | None:
    if symbol not in _ALLOWED_SYMBOLS:
        return {"error": f"Unknown symbol '{symbol}'. Allowed: {_SYMBOL_LIST}"}
    return None


def _run_ac(
    symbol: str, order_size: float, horizon_hours: float, lambda_risk: float, n_bins: int
) -> tuple:
    params = SYMBOL_PARAMS[symbol]
    schedule = schedule_ac_linear(n_bins, order_size, horizon_hours, params, lambda_risk)
    cost, variance = compute_cost_variance(schedule, order_size, params, horizon_hours)
    return schedule, cost, variance


def _cost_and_variance(
    symbol: str,
    order_size: float,
    horizon_hours: float,
    schedule_type: str,
    lambda_risk: float = 1e-6,
    n_bins: int = 13,
) -> dict:
    if err := _validate_symbol(symbol):
        return err

    if schedule_type == "ac_linear":
        _, cost, variance = _run_ac(symbol, order_size, horizon_hours, lambda_risk, n_bins)
    else:
        params = SYMBOL_PARAMS[symbol]
        v_hourly = params.adv / TRADING_HOURS_PER_DAY
        dt = horizon_hours / n_bins
        if schedule_type == "twap":
            schedule = schedule_twap(n_bins, order_size, v_hourly, dt)
        elif schedule_type == "front_loaded":
            schedule = schedule_front_loaded(n_bins, order_size, v_hourly, dt)
        elif schedule_type == "back_loaded":
            schedule = schedule_back_loaded(n_bins, order_size, v_hourly, dt)
        else:
            raise ValueError(f"Unknown schedule_type: {schedule_type!r}")
        cost, variance = compute_cost_variance(schedule, order_size, params, horizon_hours)

    return {
        "expected_cost_bps": round(cost, 4),
        "variance_bps2": round(variance, 4),
        "symbol": symbol,
        "order_size": order_size,
        "horizon_hours": horizon_hours,
        "schedule_type": schedule_type,
    }


def _optimal_schedule(
    symbol: str,
    order_size: float,
    horizon_hours: float,
    lambda_risk: float,
    n_bins: int = 13,
) -> dict:
    if err := _validate_symbol(symbol):
        return err

    schedule, cost, variance = _run_ac(symbol, order_size, horizon_hours, lambda_risk, n_bins)
    bins = [{"bin": b, "participation_rate": round(r, 6)} for b, r in schedule]
    return {
        "expected_cost_bps": round(cost, 4),
        "variance_bps2": round(variance, 4),
        "lambda_risk": lambda_risk,
        "symbol": symbol,
        "order_size": order_size,
        "horizon_hours": horizon_hours,
        "schedule_bins": bins,
    }
