"""
Shared ground-truth helpers for eval questions.

Both generated.py and curated.py import from here (DRY contract).
Every function directly invokes the same FV callables used by tool dispatch,
ensuring GT values are always consistent with live tool results.
"""
from __future__ import annotations

from api.market_impact import (
    TRADING_HOURS_PER_DAY,
    compute_cost_breakdown,
    default_n_bins,
    schedule_ac_linear,
    schedule_back_loaded,
    schedule_front_loaded,
    schedule_twap,
)
from api.parameters import SYMBOL_PARAMS, SymbolParams


def gt_cost(
    symbol: str,
    order_size: float,
    horizon_hours: float,
    schedule_type: str,
    lambda_risk: float = 1e-6,
    n_bins: int | None = None,
) -> dict[str, float]:
    """Return full GT cost breakdown via direct FV call.

    Equivalent to calling dispatch('cost_and_variance', ...) or
    dispatch('optimal_schedule', ...) and reading the summary fields.
    """
    params = SYMBOL_PARAMS[symbol]
    nb = n_bins if n_bins is not None else default_n_bins(horizon_hours)
    v_hourly = params.adv / TRADING_HOURS_PER_DAY
    dt = horizon_hours / nb

    if schedule_type == "twap":
        schedule = schedule_twap(nb, order_size, v_hourly, dt)
    elif schedule_type == "front_loaded":
        schedule = schedule_front_loaded(nb, order_size, v_hourly, dt)
    elif schedule_type == "back_loaded":
        schedule = schedule_back_loaded(nb, order_size, v_hourly, dt)
    elif schedule_type == "ac_linear":
        schedule = schedule_ac_linear(nb, order_size, horizon_hours, params, lambda_risk)
    else:
        raise ValueError(f"Unknown schedule: {schedule_type!r}")

    bd = compute_cost_breakdown(schedule, order_size, params, horizon_hours)
    return {
        "expected_cost_bps": bd.total_bps,
        "variance_bps2": bd.variance_bps2,
        "temporary_bps": bd.temporary_bps,
        "permanent_bps": bd.permanent_bps,
        "spread_bps": bd.spread_bps,
    }


def gt_with_param_override(
    symbol: str,
    order_size: float,
    horizon_hours: float,
    *,
    sigma: float | None = None,
    eta: float | None = None,
    gamma: float | None = None,
    schedule_type: str = "twap",
) -> float:
    """Return expected_cost_bps with one market/calibrated parameter overridden.

    Used for sweep endpoint GT: supply exactly one of sigma, eta, gamma.
    Schedule is always TWAP (or overridden via schedule_type).
    """
    params = SYMBOL_PARAMS[symbol]
    nb = default_n_bins(horizon_hours)
    v_h = params.adv / TRADING_HOURS_PER_DAY
    dt = horizon_hours / nb

    p = SymbolParams(
        adv=params.adv,
        sigma=sigma if sigma is not None else params.sigma,
        half_spread=params.half_spread,
        eta=eta if eta is not None else params.eta,
        gamma=gamma if gamma is not None else params.gamma,
    )

    if schedule_type == "twap":
        sched = schedule_twap(nb, order_size, v_h, dt)
    elif schedule_type == "front_loaded":
        sched = schedule_front_loaded(nb, order_size, v_h, dt)
    elif schedule_type == "back_loaded":
        sched = schedule_back_loaded(nb, order_size, v_h, dt)
    elif schedule_type == "ac_linear":
        sched = schedule_ac_linear(nb, order_size, horizon_hours, p, 1e-6)
    else:
        raise ValueError(f"Unknown schedule: {schedule_type!r}")

    return compute_cost_breakdown(sched, order_size, p, horizon_hours).total_bps
