"""
Property-based tests (Hypothesis) for the Almgren-Chriss market impact model.

Each test verifies a mathematical invariant that must hold by construction.
A failing test means the bug is in the model, not in the test.

Tests:
  1. Permanent cost is schedule-invariant
  2. AC Linear → TWAP as λ → 0
  3. AC Linear → instant execution as λ → ∞
  4. Temporary cost monotonic in peak participation
  5. Square-order-size scaling
  6. Zero-order limit
  7. Regime ordering (stressed > normal > calm)
  8. Frontier monotonicity
  9. Schedule normalisation
 10. Vendored κ relates to the discrete argmin by a known rescaling
"""

import math

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from api.market_impact import (
    TRADING_HOURS_PER_DAY,
    _ac_kappa,
    _linearised_eta,
    compute_cost_variance,
    generate_frontier,
    permanent_impact,
    schedule_ac_linear,
    schedule_back_loaded,
    schedule_front_loaded,
    schedule_twap,
    temporary_impact,
)
from api.parameters import SYMBOL_PARAMS, SymbolParams

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

@st.composite
def market_params(draw):
    """Random but physically plausible symbol parameters."""
    return SymbolParams(
        adv=draw(st.floats(min_value=1e5, max_value=1e9)),
        sigma=draw(st.floats(min_value=0.005, max_value=0.10)),
        half_spread=draw(st.floats(min_value=0.05, max_value=5.0)),
        eta=draw(st.floats(min_value=0.05, max_value=0.5)),
        gamma=draw(st.floats(min_value=0.05, max_value=1.0)),
    )


order_size_st = st.floats(min_value=1e3, max_value=5e6)
horizon_st = st.floats(min_value=0.5, max_value=6.5)
n_bins_st = st.integers(min_value=4, max_value=26)

# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _perm_cost_midpoint(
    schedule: list[tuple[int, float]],
    order_size: float,
    params: SymbolParams,
    horizon_hours: float,
) -> float:
    """Permanent cost using the midpoint convention — exactly schedule-invariant.

    Closed form: γ·σ·X / (2·V_hourly) for linear g.
    """
    dt = horizon_hours / len(schedule)
    v_hourly = params.adv / TRADING_HOURS_PER_DAY
    perm = drift = 0.0
    for _, participation in schedule:
        v = participation * v_hourly
        weight = v * dt / order_size
        own = permanent_impact(v, v_hourly, params.sigma, params.gamma) * dt
        perm += (drift + own / 2) * weight
        drift += own
    return perm


def _temp_cost_only(
    schedule: list[tuple[int, float]],
    order_size: float,
    params: SymbolParams,
    horizon_hours: float,
) -> float:
    """Temporary impact component only (spread excluded)."""
    dt = horizon_hours / len(schedule)
    v_hourly = params.adv / TRADING_HOURS_PER_DAY
    temp = 0.0
    for _, participation in schedule:
        v = participation * v_hourly
        weight = v * dt / order_size
        temp += temporary_impact(v, v_hourly, params.sigma, params.eta) * weight
    return temp


def _build_schedules(params, order_size, horizon_hours, n_bins):
    v_hourly = params.adv / TRADING_HOURS_PER_DAY
    dt = horizon_hours / n_bins
    return {
        "twap": schedule_twap(n_bins, order_size, v_hourly, dt),
        "front": schedule_front_loaded(n_bins, order_size, v_hourly, dt),
        "back": schedule_back_loaded(n_bins, order_size, v_hourly, dt),
        "ac": schedule_ac_linear(n_bins, order_size, horizon_hours, params, lambda_risk=1e-6),
    }


# ---------------------------------------------------------------------------
# Test 1: PERMANENT COST IS SCHEDULE-INVARIANT
# ---------------------------------------------------------------------------


@given(
    params=market_params(),
    order_size=order_size_st,
    horizon_hours=horizon_st,
    n_bins=n_bins_st,
)
@settings(max_examples=50)
def test_permanent_cost_schedule_invariant(params, order_size, horizon_hours, n_bins):
    """Verifies that midpoint-rule permanent cost accumulation is schedule-invariant to within discretisation error (1% at low bin counts)."""
    scheds = _build_schedules(params, order_size, horizon_hours, n_bins)

    perm = {
        name: _perm_cost_midpoint(sched, order_size, params, horizon_hours)
        for name, sched in scheds.items()
    }

    assert perm["twap"] == pytest.approx(perm["front"], rel=0.01), (
        f"TWAP vs front-loaded perm cost: {perm['twap']:.6f} vs {perm['front']:.6f}"
    )
    assert perm["twap"] == pytest.approx(perm["back"], rel=0.01), (
        f"TWAP vs back-loaded perm cost: {perm['twap']:.6f} vs {perm['back']:.6f}"
    )
    assert perm["twap"] == pytest.approx(perm["ac"], rel=0.01), (
        f"TWAP vs AC linear perm cost: {perm['twap']:.6f} vs {perm['ac']:.6f}"
    )


# ---------------------------------------------------------------------------
# Test 2: AC LINEAR → TWAP AS λ → 0
# ---------------------------------------------------------------------------


@given(
    params=market_params(),
    order_size=order_size_st,
    horizon_hours=horizon_st,
    n_bins=n_bins_st,
)
@settings(max_examples=60)
def test_ac_linear_converges_to_twap_at_low_lambda(
    params, order_size, horizon_hours, n_bins
):
    """At λ = 1e-12, the AC schedule is flat to within 1% of the mean rate.

    With λ → 0, κ → 0, sinh(κ(T−t))/sinh(κT) → (T−t)/T, which is the
    linear inventory drawdown (TWAP).  A regression in the κT → 0 branch
    would show up as a non-flat schedule.
    """
    v_hourly = params.adv / TRADING_HOURS_PER_DAY
    dt = horizon_hours / n_bins

    sched_twap = schedule_twap(n_bins, order_size, v_hourly, dt)
    sched_ac = schedule_ac_linear(
        n_bins, order_size, horizon_hours, params, lambda_risk=1e-12
    )

    rates_twap = [p for _, p in sched_twap]
    rates_ac = [p for _, p in sched_ac]

    twap_rate = rates_twap[0]  # all identical for TWAP
    assume(twap_rate > 0)

    max_dev = max(abs(a - t) for a, t in zip(rates_ac, rates_twap))
    assert max_dev < 0.01 * twap_rate, (
        f"AC at λ=1e-12 deviates {max_dev:.2e} from TWAP rate {twap_rate:.2e} "
        f"(>{max_dev / twap_rate * 100:.1f}%); κT → 0 regression?"
    )


# ---------------------------------------------------------------------------
# Test 3: AC LINEAR → INSTANT EXECUTION AS λ → ∞
# ---------------------------------------------------------------------------


@given(
    params=market_params(),
    order_size=order_size_st,
    horizon_hours=horizon_st,
    n_bins=n_bins_st,
)
@settings(max_examples=60)
def test_ac_linear_front_loads_at_high_lambda(
    params, order_size, horizon_hours, n_bins
):
    """At λ = 1e-1, the AC schedule concentrates >90% of execution in the first bin.

    In the large-κT limit the optimal inventory follows X·exp(−κt), collapsing
    almost all execution to bin 0.  Only tested when κT > 10 (i.e. the model
    is firmly in the aggressive regime).
    """
    v_hourly = params.adv / TRADING_HOURS_PER_DAY
    sigma_bin = params.sigma * math.sqrt((horizon_hours / n_bins) / TRADING_HOURS_PER_DAY)
    eta_tilde = _linearised_eta(
        params.eta, params.sigma, v_hourly, order_size, horizon_hours
    )
    kappa = _ac_kappa(1e-1, sigma_bin, eta_tilde)
    # First-bin fraction ≈ 1 − exp(−κT/n_bins); need κT/n > ln(10) ≈ 2.3
    # Use 3.5× for safety margin across all n_bins values.
    assume(kappa * horizon_hours > 3.5 * n_bins)

    sched = schedule_ac_linear(
        n_bins, order_size, horizon_hours, params, lambda_risk=1e-1
    )

    rates = [p for _, p in sched]
    total = sum(rates)
    assume(total > 0)

    first_bin_fraction = rates[0] / total
    assert first_bin_fraction > 0.90, (
        f"First bin fraction {first_bin_fraction:.3f} < 0.90 at λ=1e-1, "
        f"κT={kappa * horizon_hours:.1f}; large-κT asymptotic branch broken?"
    )


# ---------------------------------------------------------------------------
# Test 4: TEMPORARY COST MONOTONIC IN PEAK PARTICIPATION
# ---------------------------------------------------------------------------


@given(
    params=market_params(),
    order_size=order_size_st,
    horizon_hours=horizon_st,
    n_bins=n_bins_st,
)
@settings(max_examples=60)
def test_temporary_cost_ordering(params, order_size, horizon_hours, n_bins):
    """Temporary cost satisfies front > TWAP and back > TWAP.

    The execution-weighted cost is Σ η·σ·(v_k/V)^0.6 · (v_k·dt/X) ∝ Σ v_k^1.6 · dt.
    Because f(v) = v^1.6 is strictly convex (exponent > 1), Jensen's inequality
    gives Σ v_k^1.6 > n·(X/nT)^1.6 for any non-uniform schedule.  Both
    front-loaded and back-loaded are non-uniform, so both exceed TWAP.
    (Front and back are mirror images and have the same Σ v^1.6 by symmetry.)
    """
    v_hourly = params.adv / TRADING_HOURS_PER_DAY
    dt = horizon_hours / n_bins

    sched_twap = schedule_twap(n_bins, order_size, v_hourly, dt)
    sched_front = schedule_front_loaded(n_bins, order_size, v_hourly, dt)
    sched_back = schedule_back_loaded(n_bins, order_size, v_hourly, dt)

    temp_twap = _temp_cost_only(sched_twap, order_size, params, horizon_hours)
    temp_front = _temp_cost_only(sched_front, order_size, params, horizon_hours)
    temp_back = _temp_cost_only(sched_back, order_size, params, horizon_hours)

    # Convexity: both concentrated schedules must exceed uniform TWAP
    assert temp_front > temp_twap * 0.999, (
        f"front-loaded temp {temp_front:.6f} not > TWAP {temp_twap:.6f}; "
        "convexity (v^1.6) / Jensen violation"
    )
    assert temp_back > temp_twap * 0.999, (
        f"back-loaded temp {temp_back:.6f} not > TWAP {temp_twap:.6f}; "
        "convexity (v^1.6) / Jensen violation"
    )


# ---------------------------------------------------------------------------
# Test 5: SQUARE-ORDER-SIZE SCALING
# ---------------------------------------------------------------------------


@given(
    params=market_params(),
    order_size=st.floats(min_value=1e3, max_value=2e6),  # room to double
    horizon_hours=horizon_st,
    n_bins=n_bins_st,
)
@settings(max_examples=60)
def test_order_size_scaling(params, order_size, horizon_hours, n_bins):
    """Doubling X scales temporary cost by 2^0.6 ≈ 1.516 and perm cost by 2x.

    For a TWAP schedule, temporary impact integrates to η·σ·(X/T)^0.6/V^0.6,
    which scales as X^0.6.  Permanent cost (midpoint convention) equals
    γ·σ·X/(2·V), which scales as X^1.  The γX²/2 in AC refers to the
    absolute-dollar cost; dividing by notional X yields the bps figure above.

    Tolerance: 5%.
    """
    v_hourly = params.adv / TRADING_HOURS_PER_DAY
    dt = horizon_hours / n_bins

    sched_1x = schedule_twap(n_bins, order_size, v_hourly, dt)
    sched_2x = schedule_twap(n_bins, 2 * order_size, v_hourly, dt)

    temp_1x = _temp_cost_only(sched_1x, order_size, params, horizon_hours)
    temp_2x = _temp_cost_only(sched_2x, 2 * order_size, params, horizon_hours)
    perm_1x = _perm_cost_midpoint(sched_1x, order_size, params, horizon_hours)
    perm_2x = _perm_cost_midpoint(sched_2x, 2 * order_size, params, horizon_hours)

    assume(temp_1x > 0 and perm_1x > 0)

    expected_temp_ratio = 2 ** 0.6  # ≈ 1.516
    expected_perm_ratio = 2.0       # linear in X in bps

    assert temp_2x / temp_1x == pytest.approx(expected_temp_ratio, rel=0.05), (
        f"Temp cost ratio {temp_2x / temp_1x:.4f} ≠ 2^0.6 = {expected_temp_ratio:.4f} "
        "(±5%); power-law exponent or normalisation error?"
    )
    assert perm_2x / perm_1x == pytest.approx(expected_perm_ratio, rel=0.05), (
        f"Perm cost ratio (midpoint) {perm_2x / perm_1x:.4f} ≠ 2.0 "
        "(±5%); γ·σ·X/(2·V) not linear in X?"
    )


# ---------------------------------------------------------------------------
# Test 6: ZERO-ORDER LIMIT
# ---------------------------------------------------------------------------


@given(params=market_params())
@settings(max_examples=60)
def test_zero_order_limit(params):
    """Single-bin order: cost equals half-spread plus temporary impact plus half the permanent drift.

    Midpoint discretisation charges each bin half its own permanent drift, so
    even the first (and only) bin pays own/2 for permanent impact.  With n_bins=1
    the closed form is:
        cost = half_spread + η·σ·(v/V)^0.6 + γ·σ·(v/V)·dt / 2
    Verifies the midpoint accounting in the n=1 limit and catches numerical issues
    at vanishing participation (missing terms, wrong weight, divide-by-zero at small v).
    """
    order_size = 1.0
    horizon_hours = 1.0
    n_bins = 1
    v_hourly = params.adv / TRADING_HOURS_PER_DAY
    dt = horizon_hours / n_bins

    v = order_size / horizon_hours
    own = permanent_impact(v, v_hourly, params.sigma, params.gamma) * dt
    expected_cost = params.half_spread + temporary_impact(v, v_hourly, params.sigma, params.eta) + own / 2

    sched = schedule_twap(n_bins, order_size, v_hourly, dt)
    total_cost, _ = compute_cost_variance(sched, order_size, params, horizon_hours)

    assert total_cost == pytest.approx(expected_cost, rel=1e-9), (
        f"Single-bin cost {total_cost:.9f} ≠ half_spread + temp_impact "
        f"{expected_cost:.9f}; numerical issue in cost accumulation loop?"
    )


# ---------------------------------------------------------------------------
# Test 7: REGIME ORDERING
# ---------------------------------------------------------------------------


@given(
    params=market_params(),
    order_size=order_size_st,
    horizon_hours=horizon_st,
    n_bins=n_bins_st,
)
@settings(max_examples=50)
def test_regime_ordering(params, order_size, horizon_hours, n_bins):
    """Cost and variance are strictly ordered: stressed > normal > calm.

    Calm: σ scaled to 0.6×; stressed: σ × 1.8, η × 1.3, γ × 1.3.
    The ordering must hold for both expected_cost and shortfall_variance,
    catching sign errors and parameter mis-wiring in the regime panel.
    """
    calm = SymbolParams(
        adv=params.adv,
        sigma=params.sigma * 0.6,
        half_spread=params.half_spread,
        eta=params.eta,
        gamma=params.gamma,
    )
    stressed = SymbolParams(
        adv=params.adv,
        sigma=params.sigma * 1.8,
        half_spread=params.half_spread,
        eta=params.eta * 1.3,
        gamma=params.gamma * 1.3,
    )

    def _cv(p):
        sched = schedule_ac_linear(n_bins, order_size, horizon_hours, p, lambda_risk=1e-6)
        return compute_cost_variance(sched, order_size, p, horizon_hours)

    calm_cost, calm_var = _cv(calm)
    normal_cost, normal_var = _cv(params)
    stressed_cost, stressed_var = _cv(stressed)

    assert calm_cost < normal_cost < stressed_cost, (
        f"Cost ordering violated: calm={calm_cost:.4f}, "
        f"normal={normal_cost:.4f}, stressed={stressed_cost:.4f}"
    )
    assert calm_var < normal_var < stressed_var, (
        f"Variance ordering violated: calm={calm_var:.4f}, "
        f"normal={normal_var:.4f}, stressed={stressed_var:.4f}"
    )


# ---------------------------------------------------------------------------
# Test 8: FRONTIER MONOTONICITY
# ---------------------------------------------------------------------------


@given(
    params=market_params(),
    order_size=order_size_st,
    horizon_hours=horizon_st,
)
@settings(max_examples=40)
def test_frontier_monotonicity(params, order_size, horizon_hours):
    """The efficient frontier is monotone: higher λ → higher cost, lower variance.

    As λ increases the schedule front-loads more aggressively, raising
    execution cost while reducing shortfall variance.  Any non-monotonicity
    indicates a non-convex frontier bug.

    Previously failing due to a forward-convention permanent-cost bug in
    compute_cost_variance (perm_cost fell as schedules front-loaded, causing
    a dip in the total-cost frontier at certain λ values).  Fixed by the
    midpoint-rule accumulation in commit 80dcd4e.
    """
    frontier = generate_frontier(order_size, horizon_hours, params)
    frontier.sort(key=lambda x: x["lambda_val"])

    for i in range(1, len(frontier)):
        prev, curr = frontier[i - 1], frontier[i]
        assert curr["expected_cost_bps"] >= prev["expected_cost_bps"] - 1e-6, (
            f"Cost not non-decreasing: λ={prev['lambda_val']:.1e} → "
            f"{curr['lambda_val']:.1e}, cost {prev['expected_cost_bps']} → "
            f"{curr['expected_cost_bps']}"
        )
        assert curr["variance_bps2"] <= prev["variance_bps2"] + 1e-6, (
            f"Variance not non-increasing: λ={prev['lambda_val']:.1e} → "
            f"{curr['lambda_val']:.1e}, var {prev['variance_bps2']} → "
            f"{curr['variance_bps2']}"
        )


# ---------------------------------------------------------------------------
# Test 9: SCHEDULE NORMALISATION
# ---------------------------------------------------------------------------


@given(
    params=market_params(),
    order_size=order_size_st,
    horizon_hours=horizon_st,
    n_bins=n_bins_st,
)
@settings(max_examples=60)
def test_schedule_normalisation(params, order_size, horizon_hours, n_bins):
    """Every schedule generator sums to exactly order_size (within 1e-6 relative).

    Σ participation_i × v_hourly × dt == order_size for all four generators.
    Guards against weight-normalisation bugs: a schedule that under- or
    over-trades by even 0.01% would silently mis-price execution cost.
    """
    v_hourly = params.adv / TRADING_HOURS_PER_DAY
    dt = horizon_hours / n_bins

    scheds = _build_schedules(params, order_size, horizon_hours, n_bins)

    for name, sched in scheds.items():
        total = sum(p * v_hourly * dt for _, p in sched)
        assert total == pytest.approx(order_size, rel=1e-6), (
            f"{name} total executed {total:.6f} ≠ order_size {order_size:.6f} "
            f"(rel error {abs(total - order_size) / order_size:.2e})"
        )


# ---------------------------------------------------------------------------
# Test 10: VENDORED κ vs DISCRETE ARGMIN — KNOWN RESCALING
# ---------------------------------------------------------------------------


def test_vendored_kappa_relates_to_discrete_argmin_by_known_rescaling():
    """kappa_ac^2 / kappa_opt^2 -> X*dt/1e4 as mu -> 0; 2.24x at the documented config.

    Mirrors Temper's test from the opposite side: a casual "fix" of the vendored
    convention outside a golden re-vendor must fail loudly in both repos.
    """
    params = SYMBOL_PARAMS["AAPL"]
    X, horizon, n_bins = 1e5, 6.5, 13
    dt = horizon / n_bins
    v_hourly = params.adv / TRADING_HOURS_PER_DAY
    sigma_bin = params.sigma * math.sqrt(dt / TRADING_HOURS_PER_DAY)
    eta_tilde = _linearised_eta(params.eta, params.sigma, v_hourly, X, horizon)

    def kappa_opt(lam):
        mu = lam * sigma_bin**2 * 1e4 * dt / (X * eta_tilde)
        return math.acosh(1.0 + mu / 2.0) / dt

    # small-mu limit: pure units factor
    lam = 1e-9
    ratio2 = _ac_kappa(lam, sigma_bin, eta_tilde) ** 2 / kappa_opt(lam) ** 2
    assert ratio2 == pytest.approx(X * dt / 1e4, rel=1e-6)

    # documented config: the headline 2.24x, and the rates are genuinely distinct
    lam = 1e-4
    k_ac, k_op = _ac_kappa(lam, sigma_bin, eta_tilde), kappa_opt(lam)
    assert k_ac / k_op == pytest.approx(2.2407, rel=1e-3)
    assert not math.isclose(k_ac, k_op, rel_tol=1e-3)
