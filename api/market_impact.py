"""
Almgren-Chriss style market impact model.

Key conventions
---------------
- v           : participation rate (shares / hour), NOT cumulative shares
- v_hourly    : ADV in shares / hour  (= daily ADV / 6.5)
- sigma       : daily volatility (fractional); scaled to intraday via sqrt(dt/6.5)
- horizon     : trading horizon in hours; binned into integer half-hour slots
- All costs   : expressed in basis points (bps) of notional
"""

import math
from dataclasses import dataclass

from api.parameters import SYMBOL_PARAMS as SYMBOL_PARAMS, SymbolParams as SymbolParams  # re-exported for downstream callers

TRADING_HOURS_PER_DAY = 6.5


def default_n_bins(horizon_hours: float) -> int:
    """Canonical bin count for a horizon; shared by the API and the agent."""
    return max(2, round(horizon_hours * 2))


# ---------------------------------------------------------------------------
# Impact functions
# ---------------------------------------------------------------------------


def temporary_impact(
    v: float, v_hourly: float, sigma_daily: float, eta: float,
    temp_exponent: float = 0.6,
) -> float:
    """Power-law temporary market impact in bps, spread excluded.

    power_law = η · σ_daily · (|v| / (6.5 · v_hourly))^temp_exponent
    """
    participation = abs(v) / v_hourly  # both shares/hour — correct
    return eta * sigma_daily * (participation**temp_exponent) * 1e4


def permanent_impact(
    v: float, v_hourly: float, sigma_daily: float, gamma: float
) -> float:
    """Linear permanent market impact in bps, unsigned.

    g = γ · σ_daily · (|v| / v_hourly)
    """
    participation = abs(v) / v_hourly
    return gamma * sigma_daily * participation * 1e4


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


def _linearised_eta(
    eta: float, sigma_daily: float, v_hourly: float,
    order_size: float, horizon_hours: float
) -> float:
    """Linearise at actual TWAP participation rate of this specific order."""
    twap_rate = order_size / horizon_hours          # shares/hour at TWAP
    p0 = twap_rate / v_hourly                       # dimensionless participation
    p0 = max(p0, 1e-4)                              # floor to avoid blow-up
    return max(
        eta * sigma_daily * 0.6 * (p0 ** -0.4) / v_hourly,
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
    eta_tilde = _linearised_eta(params.eta, params.sigma, v_hourly, order_size, horizon_hours)
    kappa = _ac_kappa(lambda_risk, sigma_bin, eta_tilde)
    inventory = _ac_inventory(order_size, kappa, horizon_hours, n_bins, dt)
    rates = [(inventory[i] - inventory[i + 1]) / dt for i in range(n_bins)]
    return list(enumerate(r / v_hourly for r in rates))


# ---------------------------------------------------------------------------
# Cost and variance
# ---------------------------------------------------------------------------


@dataclass
class CostBreakdown:
    """Per-component execution cost breakdown plus shortfall variance."""
    temporary_bps: float    # power-law temp impact, spread excluded
    permanent_bps: float    # permanent (price-drift) impact
    spread_bps: float       # half-spread cost
    total_bps: float        # sum of the three above
    variance_bps2: float    # execution shortfall variance (NOT P&L variance)


def compute_cost_breakdown(
    schedule: list[tuple[int, float]],
    order_size: float,
    params: SymbolParams,
    horizon_hours: float,
    temp_exponent: float = 0.6,
) -> CostBreakdown:
    """Return a full per-component cost breakdown for the given schedule.

    Uses the midpoint-rule permanent-cost accumulation, which is
    schedule-invariant by construction (integrates to γσX/(2·V_hourly) in bps).
    """
    dt = horizon_hours / len(schedule)
    v_hourly = params.adv / TRADING_HOURS_PER_DAY
    sigma_bin = params.sigma * math.sqrt(dt / TRADING_HOURS_PER_DAY)

    temp_cost = perm_cost = spread_cost = shortfall_variance = 0.0
    remaining = order_size
    cumulative_drift_bps = 0.0
    for _, participation in schedule:
        v = participation * v_hourly
        weight = v * dt / order_size
        temp_cost += temporary_impact(v, v_hourly, params.sigma, params.eta, temp_exponent) * weight
        spread_cost += params.half_spread * weight
        # Midpoint rule: each bin pays cumulative drift from prior bins plus
        # half its own, giving schedule-invariant permanent cost.
        own = permanent_impact(v, v_hourly, params.sigma, params.gamma) * dt
        perm_cost += (cumulative_drift_bps + own / 2) * weight
        cumulative_drift_bps += own
        shortfall_variance += (sigma_bin * 1e4) ** 2 * (remaining / order_size) ** 2
        remaining -= v * dt

    return CostBreakdown(
        temporary_bps=temp_cost,
        permanent_bps=perm_cost,
        spread_bps=spread_cost,
        total_bps=temp_cost + perm_cost + spread_cost,
        variance_bps2=shortfall_variance,
    )


def compute_cost_variance(
    schedule: list[tuple[int, float]],
    order_size: float,
    params: SymbolParams,
    horizon_hours: float,
    temp_exponent: float = 0.6,
) -> tuple[float, float]:
    """Return (expected_cost_bps, variance_bps2) for the given schedule."""
    bd = compute_cost_breakdown(schedule, order_size, params, horizon_hours, temp_exponent)
    return bd.total_bps, bd.variance_bps2


def generate_frontier(
    order_size: float,
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
            sched, order_size, params, horizon_hours
        )
        frontier.append(
            {
                "lambda_val": lam,
                "expected_cost_bps": round(cost, 4),
                "variance_bps2": round(var, 4),
            }
        )
    return frontier
