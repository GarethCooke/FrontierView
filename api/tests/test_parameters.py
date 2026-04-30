"""
Regression tests for model parameters and core impact functions.

Guards against accidental modification of Almgren 2005 literature constants
and per-symbol market defaults. Run with: pytest api/tests/
"""

import pytest

from api.parameters import ALMGREN_ETA, ALMGREN_GAMMA, SYMBOL_PARAMS
from api.market_impact import (
    TRADING_HOURS_PER_DAY,
    compute_cost_variance,
    schedule_twap,
    temporary_impact,
)


# ---------------------------------------------------------------------------
# Almgren 2005 literature constants
# ---------------------------------------------------------------------------


def test_almgren_eta():
    assert ALMGREN_ETA == 0.142


def test_almgren_gamma():
    assert ALMGREN_GAMMA == 0.314


# ---------------------------------------------------------------------------
# AAPL symbol defaults
# ---------------------------------------------------------------------------


def test_aapl_adv():
    assert SYMBOL_PARAMS["AAPL"].adv == 60_000_000


def test_aapl_sigma():
    assert SYMBOL_PARAMS["AAPL"].sigma == 0.0155


def test_aapl_half_spread():
    assert SYMBOL_PARAMS["AAPL"].half_spread == 0.30


# ---------------------------------------------------------------------------
# Impact function behaviour
# ---------------------------------------------------------------------------


def test_temporary_impact_zero_participation():
    """v=0 ⟹ participation=0 ⟹ power-law term 0^0.6 = 0, no impact."""
    result = temporary_impact(v=0.0, v_hourly=1_000.0, sigma_daily=0.02, eta=ALMGREN_ETA)
    assert result == 0.0


# ---------------------------------------------------------------------------
# compute_cost_variance: half-spread floor
# ---------------------------------------------------------------------------

_AAPL = SYMBOL_PARAMS["AAPL"]
_HORIZON = 2.0
_N_BINS = 4
_ORDER = 100_000
_V_HOURLY = _AAPL.adv / TRADING_HOURS_PER_DAY
_TWAP = schedule_twap(_N_BINS, _ORDER, _V_HOURLY, _HORIZON / _N_BINS)


def test_spread_floor():
    """Half-spread contributes positively regardless of impact magnitude.

    Weights sum to 1.0, so the spread contribution equals params.half_spread exactly.
    Total cost must be at least that floor.
    """
    cost, _ = compute_cost_variance(_TWAP, _ORDER, _AAPL, _HORIZON)
    assert cost >= _AAPL.half_spread
