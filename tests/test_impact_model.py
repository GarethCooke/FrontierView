"""
Numerical unit tests for the Almgren-Chriss market impact model.

Tests cover four properties:
  1. End-to-end cost magnitude (sanity bounds catch missing terms or unit errors)
  2. Permanent impact square-root exponent (not linear)
  3. Temporary impact participation-rate denominator
  4. Variance integral scales as T, not T²  (T/3 not T²/3)
"""

import pytest

from api.market_impact import (
    TRADING_HOURS_PER_DAY,
    compute_cost_variance,
    permanent_impact,
    schedule_twap,
    temporary_impact,
)
from api.parameters import SymbolParams


# ---------------------------------------------------------------------------
# Shared fixtures / constants
# ---------------------------------------------------------------------------

# Canonical AAPL-like parameters used throughout
CANON = SymbolParams(
    adv=60_000_000,
    sigma=0.0155,
    half_spread=0.30,
    eta=0.142,
    gamma=0.314,
)
CANON_ORDER = 500_000        # shares
CANON_HORIZON = 4.0          # hours
CANON_N_BINS = 8             # 30-minute slots over 4 h


# ---------------------------------------------------------------------------
# 1. Cost magnitude — end-to-end sanity check
# ---------------------------------------------------------------------------


def test_cost_magnitude_canonical():
    """End-to-end cost for the canonical 500k-share / 4h TWAP order is in [2.2, 4.5] bps.

    Fails if:
    - Either impact term is missing (cost drops to ~1 bps or below).
    - σ is supplied as percentage (1.55) instead of fractional (0.0155) — cost
      inflates to ~100 bps.
    - The participation-rate denominator uses raw daily ADV instead of
      ADV / TRADING_HOURS_PER_DAY — cost drops to ~1 bps or below.

    Note: the correct implementation produces ~3.1 bps for this parameter set
    (TWAP participation ≈ 1.35 % of hourly ADV, linear permanent impact,
    forward-accumulator shortfall).  The upper bound of 4.5 bps catches the
    σ-units bug well before the ~165 bps it would produce.
    """
    v_hourly = CANON.adv / TRADING_HOURS_PER_DAY
    dt = CANON_HORIZON / CANON_N_BINS
    sched = schedule_twap(CANON_N_BINS, CANON_ORDER, v_hourly, dt)

    total_cost, _ = compute_cost_variance(sched, CANON_ORDER, CANON, CANON_HORIZON)

    assert 2.2 <= total_cost <= 4.5, (
        f"Expected cost in [2.2, 4.5] bps; got {total_cost:.4f} bps. "
        "A value below 2.2 bps usually means an impact term is missing or the "
        "participation rate denominator is wrong. "
        "A value above 4.5 bps usually means σ is in percent rather than fractional."
    )


# ---------------------------------------------------------------------------
# 2. Permanent impact — exponent is 1.0, linear
# ---------------------------------------------------------------------------


def test_permanent_impact_linear_exponent():
    """Doubling the participation rate multiplies permanent impact by 2.0.

    Permanent impact is linear in participation rate, so doubling v doubles impact.
    The correct linear law gives 2^1 = 2.0.

    Uses arbitrary but round numbers (v_hourly=1000, sigma=0.02) so that the
    ratio depends only on the exponent, not on the scale factors.
    """
    sigma = 0.02
    gamma = 0.314
    v_hourly = 1_000.0

    impact_1x = permanent_impact(v=100.0, v_hourly=v_hourly, sigma_daily=sigma, gamma=gamma)
    impact_2x = permanent_impact(v=200.0, v_hourly=v_hourly, sigma_daily=sigma, gamma=gamma)

    ratio = impact_2x / impact_1x
    expected_ratio = 2.0 ** 1.0  # = 2.0

    assert ratio == pytest.approx(expected_ratio, rel=0.01), (
        f"impact(2x) / impact(1x) = {ratio:.4f}; expected 2.0 = {expected_ratio:.4f}. "
        "A ratio other than 2.0 indicates a non-linear implementation."
    )


# ---------------------------------------------------------------------------
# 3. Temporary impact — participation rate denominator
# ---------------------------------------------------------------------------


def test_temporary_impact_full_participation():
    """At 100 % participation (v == v_hourly) temp impact equals η·σ·1e4 exactly.

    Fails if the denominator includes an extra TRADING_HOURS_PER_DAY factor
    (the old bug `v / (6.5 * v_hourly)`), which would make participation appear
    6.5× smaller than it truly is and reduce the result by 6.5^0.6 ≈ 3.7×.
    """
    v_hourly = 10_000.0
    sigma = 0.02
    eta = 0.142

    result = temporary_impact(v=v_hourly, v_hourly=v_hourly, sigma_daily=sigma, eta=eta)
    expected = eta * sigma * (1.0 ** 0.6) * 1e4  # participation = 1.0

    assert result == pytest.approx(expected, rel=1e-9), (
        f"temp_impact at 100 % participation = {result:.6f}; "
        f"expected η·σ·1e4 = {expected:.6f}. "
        "A lower value suggests the denominator includes an extra 6.5 factor."
    )


def test_temporary_impact_half_participation():
    """At 50 % participation (v = 0.5·v_hourly) temp impact equals η·σ·0.5^0.6·1e4.

    Fails if the TRADING_HOURS_PER_DAY×v_hourly denominator bug is present
    (participation appears 6.5× smaller, changing the power-law result).
    """
    v_hourly = 10_000.0
    sigma = 0.02
    eta = 0.142

    result = temporary_impact(v=0.5 * v_hourly, v_hourly=v_hourly, sigma_daily=sigma, eta=eta)
    expected = eta * sigma * (0.5 ** 0.6) * 1e4

    assert result == pytest.approx(expected, rel=1e-9), (
        f"temp_impact at 50 % participation = {result:.6f}; "
        f"expected η·σ·0.5^0.6·1e4 = {expected:.6f}."
    )


# ---------------------------------------------------------------------------
# 4. Variance integral — scales as T (T/3 law), not T²
# ---------------------------------------------------------------------------


def test_variance_scales_linearly_with_horizon():
    """Doubling the horizon (with fixed bin count) doubles execution-shortfall variance.

    With fixed n and TWAP weights, variance reduces to n·σ²·(dt/6.5)·constant,
    so variance ∝ dt ∝ T. The ratio of variances at T=4h vs T=2h should be 2.0.

    Fails (ratio ≈ 4.0) if each variance term is scaled by dt² instead of dt
    — the double-dt bug, where variance accumulates as T² rather than T.
    """
    v_hourly = CANON.adv / TRADING_HOURS_PER_DAY
    n = CANON_N_BINS  # same bin count for both horizons

    sched_2h = schedule_twap(n, CANON_ORDER, v_hourly, 2.0 / n)
    sched_4h = schedule_twap(n, CANON_ORDER, v_hourly, 4.0 / n)

    _, var_2h = compute_cost_variance(sched_2h, CANON_ORDER, CANON, 2.0)
    _, var_4h = compute_cost_variance(sched_4h, CANON_ORDER, CANON, 4.0)

    ratio = var_4h / var_2h

    assert ratio == pytest.approx(2.0, rel=0.02), (
        f"variance(4h) / variance(2h) = {ratio:.4f}; expected 2.0. "
        "A ratio of ~4.0 indicates variance is scaling as T² (double-dt bug)."
    )
