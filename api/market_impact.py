"""
Almgren-Chriss style market impact model.

Key conventions
---------------
- v           : participation rate (shares / hour), NOT cumulative shares
- v_hourly    : ADV in shares / hour  (= daily ADV / 6.5)
- sigma       : daily volatility (fractional); scaled to intraday via sqrt(dt/6.5)
- horizon     : trading horizon in hours; binned into integer half-hour slots
- side        : "buy" or "sell" — affects sign of permanent impact on remaining inventory
- All costs   : expressed in basis points (bps) of notional
"""

import math

from api.parameters import SYMBOL_PARAMS as SYMBOL_PARAMS, SymbolParams as SymbolParams

TRADING_HOURS_PER_DAY = 6.5


def _sign(side: str) -> float:
    return 1.0 if side == "buy" else -1.0


# ---------------------------------------------------------------------------
# Impact functions
# ---------------------------------------------------------------------------


def temporary_impact(
    v: float, v_hourly: float, sigma_daily: float, eta: float
) -> float:
    """Power-law temporary market impact in bps, spread excluded.

    power_law = η · σ_daily · (|v| / (6.5 · v_hourly))^0.6
    """
    participation = abs(v) / (TRADING_HOURS_PER_DAY * v_hourly)
    return eta * sigma_daily * (participation**0.6) * 1e4


def permanent_impact(
    v: float, v_hourly: float, sigma_daily: float, gamma: float
) -> float:
    """Linear permanent market impact in bps, unsigned.

    g = γ · σ_daily · (|v| / v_hourly)
    """
    return gamma * sigma_daily * abs(v) / v_hourly * 1e4


# ---------------------------------------------------------------------------
# Schedule generators
# Each returns list[(time_bin, participation_rate)] where
# participation_rate = v_i / v_hourly  (fraction of hourly ADV).
# ---------------------------------------------------------------------------


def _normalise(weights: list[float], order_size: float, dt_hours: float) -> list[float]:
    """Scale weights so Σ v_i · dt == order_size; return v_i (shares/hour)."""
    total = sum(weights)
    return [w / total * order_size / dt_hours for w in weights]


def schedule_twap(
    n_bins: int, order_size: float, v_hourly: float, dt_hours: float
) -> list[tuple[int, float]]:
    """Flat (time-weighted average price) participation schedule."""
    rates = _normalise([1.0] * n_bins, order_size, dt_hours)
    return [(i, r / v_hourly) for i, r in enumerate(rates)]


def schedule_front_loaded(
    n_bins: int, order_size: float, v_hourly: float, dt_hours: float
) -> list[tuple[int, float]]:
    """Exponentially decaying participation schedule."""
    weights = [math.exp(-3.0 * i / max(n_bins - 1, 1)) for i in range(n_bins)]
    rates = _normalise(weights, order_size, dt_hours)
    return [(i, r / v_hourly) for i, r in enumerate(rates)]


def schedule_back_loaded(
    n_bins: int, order_size: float, v_hourly: float, dt_hours: float
) -> list[tuple[int, float]]:
    """Exponentially growing participation schedule."""
    weights = [math.exp(3.0 * i / max(n_bins - 1, 1)) for i in range(n_bins)]
    rates = _normalise(weights, order_size, dt_hours)
    return [(i, r / v_hourly) for i, r in enumerate(rates)]


# ---------------------------------------------------------------------------
# AC linear schedule helpers
# ---------------------------------------------------------------------------


def _linearised_eta(eta: float, sigma_daily: float, v_hourly: float) -> float:
    """Temporary impact slope linearised at 10% ADV participation."""
    return max(
        eta * sigma_daily * 0.6 * (0.10**-0.4) / (TRADING_HOURS_PER_DAY * v_hourly),
        1e-12,
    )


def _ac_kappa(lambda_risk: float, sigma_bin: float, eta_tilde: float) -> float:
    """Almgren-Chriss decay rate κ = sqrt(λ·σ²_bin / η̃)."""
    return math.sqrt(max(lambda_risk * sigma_bin**2 / eta_tilde, 1e-12))


def _ac_inventory(
    order_size: float, kappa: float, horizon: float, n_bins: int, dt: float
) -> list[float]:
    """Inventory trajectory x(t_i) = X · sinh(κ(T−t_i)) / sinh(κT).

    When κT > 500 the sinh ratio overflows; use the large-κT asymptotic
    exp(−κt_i), which correctly collapses to near-instant execution.
    """
    kT = kappa * horizon
    if kT > 500:
        return [order_size * math.exp(-kappa * i * dt) for i in range(n_bins + 1)]
    sinh_kt = math.sinh(kT)
    return [
        order_size * math.sinh(kappa * (horizon - i * dt)) / sinh_kt
        for i in range(n_bins + 1)
    ]


def schedule_ac_linear(
    n_bins: int,
    order_size: float,
    horizon_hours: float,
    params: SymbolParams,
    lambda_risk: float = 1e-6,
) -> list[tuple[int, float]]:
    """Almgren-Chriss closed-form optimal liquidation schedule.

    Minimises E[cost] + λ · Var[shortfall] on a linearised impact model.
    At low λ the schedule approaches TWAP; at high λ it front-loads aggressively.
    """
    v_hourly = params.adv / TRADING_HOURS_PER_DAY
    dt = horizon_hours / n_bins
    sigma_bin = params.sigma * math.sqrt(dt / TRADING_HOURS_PER_DAY)
    eta_tilde = _linearised_eta(params.eta, params.sigma, v_hourly)
    kappa = _ac_kappa(lambda_risk, sigma_bin, eta_tilde)
    inventory = _ac_inventory(order_size, kappa, horizon_hours, n_bins, dt)
    rates = [(inventory[i] - inventory[i + 1]) / dt for i in range(n_bins)]
    return list(enumerate(r / v_hourly for r in rates))


# ---------------------------------------------------------------------------
# Cost and variance
# ---------------------------------------------------------------------------


def compute_cost_variance(
    schedule: list[tuple[int, float]],
    order_size: float,
    side: str,
    params: SymbolParams,
    horizon_hours: float,
) -> tuple[float, float]:
    """Return (expected_cost_bps, variance_bps2) for the given schedule.

    expected_cost_bps — temporary + spread + permanent impact (AC shortfall).
    variance_bps2     — variance of execution shortfall due to price diffusion
                        while the order is worked (NOT P&L variance).
    """
    dt = horizon_hours / len(schedule)
    v_hourly = params.adv / TRADING_HOURS_PER_DAY
    sigma_bin = params.sigma * math.sqrt(dt / TRADING_HOURS_PER_DAY)

    temp_cost = perm_cost = shortfall_variance = 0.0
    remaining = order_size
    for _, participation in schedule:
        v = participation * v_hourly
        weight = v * dt / order_size
        temp_cost += (
            temporary_impact(v, v_hourly, params.sigma, params.eta) + params.half_spread
        ) * weight
        # Permanent impact is always a cost: buyer pays more for remaining shares,
        # seller receives less.  No sign flip needed — permanent_impact() is positive.
        perm_cost += (
            permanent_impact(v, v_hourly, params.sigma, params.gamma)
            * (remaining / order_size)
            * weight
        )
        # Var[shortfall] = σ²_bin × Σ (x_i / X)² × dt; integral discretised per bin
        shortfall_variance += (
            (sigma_bin * 1e4) ** 2 * (remaining / order_size) ** 2 * dt
        )
        remaining -= v * dt

    return temp_cost + perm_cost, shortfall_variance


def generate_frontier(
    order_size: float,
    side: str,
    horizon_hours: float,
    params: SymbolParams,
    n_bins: int = 13,
) -> list[dict]:
    """Sweep λ over a log-spaced grid and return the cost-variance frontier."""
    lambdas = [10 ** (i * 0.5 - 9) for i in range(17)]  # 1e-9 … 1e-1
    frontier = []
    for lam in lambdas:
        sched = schedule_ac_linear(n_bins, order_size, horizon_hours, params, lam)
        cost, var = compute_cost_variance(
            sched, order_size, side, params, horizon_hours
        )
        frontier.append(
            {
                "lambda_val": lam,
                "expected_cost_bps": round(cost, 4),
                "variance_bps2": round(var, 4),
            }
        )
    return frontier
