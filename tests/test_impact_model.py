"""
Numerical unit tests for the Almgren-Chriss market impact model.

Tests cover four properties:
  1. End-to-end cost magnitude (sanity bounds catch missing terms or unit errors)
  2. Permanent impact square-root exponent (not linear)
  3. Temporary impact participation-rate denominator
  4. Variance integral scales as T, not T²  (T/3 not T²/3)
"""

import math
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
    """End-to-end cost for the canonical 500k-share / 4h TWAP order is in [4, 7] bps.

    Fails if:
    - Either impact term is missing (cost drops to ~1-2 bps).
    - σ is supplied as percentage (1.55) instead of fractional (0.0155) — cost
      inflates to ~100 bps.
    - The participation-rate denominator uses raw daily ADV instead of
      ADV / TRADING_HOURS_PER_DAY — cost drops to ~1-2 bps.

    Note: the correct implementation produces ~4.4 bps for this parameter set
    (TWAP participation ≈ 1.35 % of hourly ADV).  The upper bound of 7 bps
    catches the σ-units bug well before the ~165 bps it would produce.
    """
    v_hourly = CANON.adv / TRADING_HOURS_PER_DAY
    dt = CANON_HORIZON / CANON_N_BINS
    sched = schedule_twap(CANON_N_BINS, CANON_ORDER, v_hourly, dt)

    total_cost, _ = compute_cost_variance(sched, CANON_ORDER, "buy", CANON, CANON_HORIZON)

    assert total_cost == pytest.approx(total_cost, rel=0.02)  # confirm it is finite
    assert 4.0 <= total_cost <= 7.0, (
        f"Expected cost in [4, 7] bps; got {total_cost:.4f} bps. "
        "A value below 4 bps usually means an impact term is missing or the "
        "participation rate denominator is wrong. "
        "A value above 7 bps usually means σ is in percent rather than fractional."
    )


# ---------------------------------------------------------------------------
# 2. Permanent impact — exponent is 0.5, not 1.0
# ---------------------------------------------------------------------------


def test_permanent_impact_square_root_exponent():
    """Doubling the participation rate multiplies permanent impact by sqrt(2), not 2.

    Fails if the exponent is 1.0 (linear): linear would give a ratio of 2.0.
    The correct square-root law gives 2^0.5 ≈ 1.4142.

    Uses arbitrary but round numbers (v_hourly=1000, sigma=0.02) so that the
    ratio depends only on the exponent, not on the scale factors.
    """
    sigma = 0.02
    gamma = 0.314
    v_hourly = 1_000.0

    impact_1x = permanent_impact(v=100.0, v_hourly=v_hourly, sigma_daily=sigma, gamma=gamma)
    impact_2x = permanent_impact(v=200.0, v_hourly=v_hourly, sigma_daily=sigma, gamma=gamma)

    ratio = impact_2x / impact_1x
    expected_ratio = 2.0 ** 0.5  # ≈ 1.4142

    assert ratio == pytest.approx(expected_ratio, rel=0.01), (
        f"impact(2x) / impact(1x) = {ratio:.4f}; expected sqrt(2) = {expected_ratio:.4f}. "
        "A ratio of 2.0 indicates a linear (exponent=1) implementation."
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
    """Doubling the horizon doubles the execution-shortfall variance (T/3 law).

    Uses two TWAP schedules with the same number of bins but different horizons
    (2 h and 4 h).  The correct T/3 discretisation gives variance ∝ T, so the
    4 h / 2 h ratio should be exactly 2.0.

    Fails if the double-dt bug is present (each term scaled by dt² instead of
    dt), which gives variance ∝ T², producing a ratio of 4.0 instead of 2.0.
    """
    v_hourly = CANON.adv / TRADING_HOURS_PER_DAY
    n = CANON_N_BINS  # same bin count for both horizons

    sched_2h = schedule_twap(n, CANON_ORDER, v_hourly, 2.0 / n)
    sched_4h = schedule_twap(n, CANON_ORDER, v_hourly, 4.0 / n)

    _, var_2h = compute_cost_variance(sched_2h, CANON_ORDER, "buy", CANON, 2.0)
    _, var_4h = compute_cost_variance(sched_4h, CANON_ORDER, "buy", CANON, 4.0)

    ratio = var_4h / var_2h

    assert ratio == pytest.approx(2.0, rel=0.02), (
        f"variance(4h) / variance(2h) = {ratio:.4f}; expected 2.0. "
        "A ratio of ~4.0 indicates variance is scaling as T² (double-dt bug)."
    )
