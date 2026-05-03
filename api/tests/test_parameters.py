"""
Regression tests for model parameters and core impact functions.

Guards against accidental modification of Almgren 2005 literature constants
and per-symbol market defaults. Run with: pytest api/tests/
"""

import pytest
from fastapi.testclient import TestClient

from api.main import app
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


# ---------------------------------------------------------------------------
# AnalyseRequest field validation — /analyse must return 422 for bad inputs
# ---------------------------------------------------------------------------

_client = TestClient(app)
_VALID_PAYLOAD = {
    "symbol": "AAPL",
    "order_size": 100_000,
    "horizon_hours": 2.0,
    "schedule_type": "twap",
}


@pytest.mark.parametrize("bad_payload", [
    {**_VALID_PAYLOAD, "order_size": -1},
    {**_VALID_PAYLOAD, "order_size": 0},
    {**_VALID_PAYLOAD, "order_size": 1_000_000_001},
    {**_VALID_PAYLOAD, "horizon_hours": 0},
    {**_VALID_PAYLOAD, "horizon_hours": -0.5},
    {**_VALID_PAYLOAD, "horizon_hours": 25},
    {**_VALID_PAYLOAD, "symbol": "TSLA"},
    {**_VALID_PAYLOAD, "symbol": ""},
])
def test_analyse_invalid_request_returns_422(bad_payload):
    response = _client.post("/analyse", json=bad_payload)
    assert response.status_code == 422
