"""
Generated eval questions: symbol × tool parameterisations, GT via in-process FV calls.

Computed at module load time.  Regenerate by re-importing after changing SYMBOL_PARAMS
or the schedule generators.
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
from api.parameters import SYMBOL_PARAMS

from agent.eval.questions.types import Question

# ---------------------------------------------------------------------------
# GT helpers — same FV callables the tools wrap (DRY contract)
# ---------------------------------------------------------------------------


def _gt_cost(
    symbol: str,
    order_size: float,
    horizon_hours: float,
    schedule_type: str,
    lambda_risk: float = 1e-6,
    n_bins: int | None = None,
) -> dict[str, float]:
    """Return full GT cost breakdown via direct FV call."""
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


# ---------------------------------------------------------------------------
# Parameterisation grid
# ---------------------------------------------------------------------------

_ORDER_SIZE = 100_000.0
_HORIZON = 2.0
_LAMBDA = 1e-6
_SYMBOLS = ["AAPL", "MSFT", "GOOGL", "JPM", "SPY"]

_questions: list[Question] = []

# --- cost_and_variance: all symbols × TWAP ---
for _sym in _SYMBOLS:
    _gt = _gt_cost(_sym, _ORDER_SIZE, _HORIZON, "twap", _LAMBDA)
    _questions.append(Question(
        id=f"gen_cost_twap_{_sym}",
        text=(
            f"What will it cost in bps to execute a {int(_ORDER_SIZE):,}-share {_sym} order "
            f"over {int(_HORIZON)} hours using a TWAP schedule?"
        ),
        expected_tool_path=["cost_and_variance"],
        gt_values={
            "expected_cost_bps": _gt["expected_cost_bps"],
            "variance_bps2": _gt["variance_bps2"],
        },
        source="generated",
    ))

# --- cost_and_variance: all symbols × ac_linear ---
for _sym in _SYMBOLS:
    _gt = _gt_cost(_sym, _ORDER_SIZE, _HORIZON, "ac_linear", _LAMBDA)
    _questions.append(Question(
        id=f"gen_cost_ac_{_sym}",
        text=(
            f"What is the expected execution cost for an AC-optimal liquidation of "
            f"{int(_ORDER_SIZE):,} shares of {_sym} over {int(_HORIZON)} hours at λ=1e-6?"
        ),
        # model may use cost_and_variance or optimal_schedule — either is correct
        expected_tool_path=["cost_and_variance"],
        gt_values={
            "expected_cost_bps": _gt["expected_cost_bps"],
            "variance_bps2": _gt["variance_bps2"],
        },
        source="generated",
        notes="Agent may legitimately use optimal_schedule instead; tool-path check is lenient.",
    ))

# --- optimal_schedule: 3 symbols ---
for _sym in ["AAPL", "SPY", "JPM"]:
    _gt = _gt_cost(_sym, _ORDER_SIZE, _HORIZON, "ac_linear", _LAMBDA)
    _questions.append(Question(
        id=f"gen_optimal_{_sym}",
        text=(
            f"Give me the Almgren-Chriss optimal execution schedule for "
            f"{int(_ORDER_SIZE):,} shares of {_sym} over {int(_HORIZON)} hours "
            f"at risk aversion λ=1e-6."
        ),
        expected_tool_path=["optimal_schedule"],
        gt_values={
            "expected_cost_bps": _gt["expected_cost_bps"],
            "variance_bps2": _gt["variance_bps2"],
        },
        source="generated",
    ))

# --- compare_schedules: 3 symbols (twap vs ac_linear) ---
for _sym in ["AAPL", "MSFT", "SPY"]:
    _gt_twap = _gt_cost(_sym, _ORDER_SIZE, _HORIZON, "twap")
    _gt_ac = _gt_cost(_sym, _ORDER_SIZE, _HORIZON, "ac_linear")
    # cheapest_cost_bps is the headline summary field from compare_schedules
    _cheapest = min(_gt_twap["expected_cost_bps"], _gt_ac["expected_cost_bps"])
    _questions.append(Question(
        id=f"gen_compare_{_sym}",
        text=(
            f"Compare the execution cost of TWAP versus AC-linear for "
            f"{int(_ORDER_SIZE):,} shares of {_sym} over {int(_HORIZON)} hours."
        ),
        expected_tool_path=["compare_schedules"],
        gt_values={"cheapest_cost_bps": _cheapest},
        source="generated",
    ))

# --- efficient_frontier: AAPL (shape check; no single-number GT) ---
_questions.append(Question(
    id="gen_frontier_AAPL",
    text=(
        f"Show me the efficient cost-variance frontier for "
        f"{int(_ORDER_SIZE):,} shares of AAPL over {int(_HORIZON)} hours."
    ),
    expected_tool_path=["efficient_frontier"],
    gt_values={},   # frontier shape — no single GT value; Layer 2 checks tool path only
    source="generated",
    notes="No numeric GT: frontier is a curve, not a scalar. Tool-path check only.",
))

# --- sweep: sigma sensitivity for AAPL ---
# GT: cost at range start and end for a sigma sweep, TWAP schedule
_sym = "AAPL"
_params_base = SYMBOL_PARAMS[_sym]
_sigma_lo, _sigma_hi = 0.010, 0.025
_n_bins_sweep = default_n_bins(_HORIZON)
_v_hourly = _params_base.adv / TRADING_HOURS_PER_DAY
_dt = _HORIZON / _n_bins_sweep

from api.parameters import SymbolParams as _SP  # noqa: E402

def _gt_sweep_sigma(sym: str, order_size: float, horizon: float, sigma: float) -> float:
    params = SYMBOL_PARAMS[sym]
    nb = default_n_bins(horizon)
    v_h = params.adv / TRADING_HOURS_PER_DAY
    dt_ = horizon / nb
    p = _SP(adv=params.adv, sigma=sigma, half_spread=params.half_spread, eta=params.eta, gamma=params.gamma)
    sched = schedule_twap(nb, order_size, v_h, dt_)
    bd = compute_cost_breakdown(sched, order_size, p, horizon)
    return bd.total_bps

_cost_lo = _gt_sweep_sigma(_sym, _ORDER_SIZE, _HORIZON, _sigma_lo)
_cost_hi = _gt_sweep_sigma(_sym, _ORDER_SIZE, _HORIZON, _sigma_hi)
_questions.append(Question(
    id="gen_sweep_sigma_AAPL",
    text=(
        f"How sensitive is the TWAP cost for {int(_ORDER_SIZE):,} shares of AAPL "
        f"over {int(_HORIZON)} hours to daily volatility varying from "
        f"{_sigma_lo:.3f} to {_sigma_hi:.3f}?"
    ),
    expected_tool_path=["sweep"],
    gt_values={
        "cost_at_range_start_bps": _cost_lo,
        "cost_at_range_end_bps": _cost_hi,
        "cost_delta_bps": _cost_hi - _cost_lo,
    },
    source="generated",
))

GENERATED_QUESTIONS: list[Question] = _questions
