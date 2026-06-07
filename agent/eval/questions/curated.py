"""
Curated eval questions: hand-written, GT-verified, tool-path annotated.

Coverage:
  - Realistic natural-language phrasing
  - Multi-part / compositional questions
  - Ambiguous phrasing
  - Out-of-tool requests (calibrate, price-predict, autonomous algo) -> decline/caveat
  - Decomposition queries
  - Large-order blow-up territory
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

from agent.eval.questions.types import Question


def _gt(
    symbol: str,
    order_size: float,
    horizon_hours: float,
    schedule_type: str,
    lambda_risk: float = 1e-6,
) -> dict[str, float]:
    params = SYMBOL_PARAMS[symbol]
    nb = default_n_bins(horizon_hours)
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
        raise ValueError(f"Unknown: {schedule_type!r}")
    bd = compute_cost_breakdown(schedule, order_size, params, horizon_hours)
    return {
        "expected_cost_bps": bd.total_bps,
        "variance_bps2": bd.variance_bps2,
        "temporary_bps": bd.temporary_bps,
        "permanent_bps": bd.permanent_bps,
        "spread_bps": bd.spread_bps,
    }


def _with_sigma(symbol: str, sigma: float, order_size: float, horizon: float) -> float:
    params = SYMBOL_PARAMS[symbol]
    nb = default_n_bins(horizon)
    v_h = params.adv / TRADING_HOURS_PER_DAY
    dt = horizon / nb
    p = SymbolParams(adv=params.adv, sigma=sigma, half_spread=params.half_spread,
                     eta=params.eta, gamma=params.gamma)
    sched = schedule_twap(nb, order_size, v_h, dt)
    return compute_cost_breakdown(sched, order_size, p, horizon).total_bps


def _with_eta(symbol: str, eta: float, order_size: float, horizon: float) -> float:
    params = SYMBOL_PARAMS[symbol]
    nb = default_n_bins(horizon)
    v_h = params.adv / TRADING_HOURS_PER_DAY
    dt = horizon / nb
    p = SymbolParams(adv=params.adv, sigma=params.sigma, half_spread=params.half_spread,
                     eta=eta, gamma=params.gamma)
    sched = schedule_twap(nb, order_size, v_h, dt)
    return compute_cost_breakdown(sched, order_size, p, horizon).total_bps


CURATED_QUESTIONS: list[Question] = [

    # 1 — Realistic phrasing, unspecified schedule
    Question(
        id="cur_cost_aapl_natural",
        text="What's the market impact of buying 200,000 shares of AAPL over the next 2 hours?",
        expected_tool_path=["cost_and_variance"],
        gt_values={
            "expected_cost_bps": _gt("AAPL", 200_000, 2.0, "twap")["expected_cost_bps"],
        },
        source="curated",
        notes=(
            "Agent may choose any schedule. GT anchored on TWAP for reference; "
            "tool routing is the primary check."
        ),
    ),

    # 2 — Multi-schedule sell-side comparison
    Question(
        id="cur_compare_spy_sell",
        text=(
            "I need to sell 500,000 shares of SPY over 4 hours. "
            "Which execution schedule is cheapest — TWAP, front-loaded, or back-loaded?"
        ),
        expected_tool_path=["compare_schedules"],
        gt_values={
            "cheapest_cost_bps": min(
                _gt("SPY", 500_000, 4.0, "twap")["expected_cost_bps"],
                _gt("SPY", 500_000, 4.0, "front_loaded")["expected_cost_bps"],
                _gt("SPY", 500_000, 4.0, "back_loaded")["expected_cost_bps"],
            ),
        },
        source="curated",
    ),

    # 3 — Explicit lambda
    Question(
        id="cur_optimal_msft_lambda",
        text=(
            "Give me the optimal MSFT execution schedule for 100,000 shares "
            "over 1 hour with risk aversion lambda = 1e-5."
        ),
        expected_tool_path=["optimal_schedule"],
        gt_values={
            "expected_cost_bps": _gt("MSFT", 100_000, 1.0, "ac_linear", 1e-5)["expected_cost_bps"],
            "variance_bps2": _gt("MSFT", 100_000, 1.0, "ac_linear", 1e-5)["variance_bps2"],
        },
        source="curated",
    ),

    # 4 — Efficient frontier
    Question(
        id="cur_frontier_jpm",
        text=(
            "What does the efficient frontier look like for a 300,000-share JPM order "
            "over 3 hours?"
        ),
        expected_tool_path=["efficient_frontier"],
        gt_values={},
        source="curated",
        notes="Frontier shape — no scalar GT. Tool-path check only.",
    ),

    # 5 — Sigma sweep
    Question(
        id="cur_sweep_googl_sigma",
        text=(
            "How sensitive is the TWAP cost for 150,000 shares of GOOGL over 2 hours "
            "to daily volatility ranging from 1.5% to 2.5%?"
        ),
        expected_tool_path=["sweep"],
        gt_values={
            "cost_at_range_start_bps": _with_sigma("GOOGL", 0.015, 150_000, 2.0),
            "cost_at_range_end_bps": _with_sigma("GOOGL", 0.025, 150_000, 2.0),
            "cost_delta_bps": (
                _with_sigma("GOOGL", 0.025, 150_000, 2.0)
                - _with_sigma("GOOGL", 0.015, 150_000, 2.0)
            ),
        },
        source="curated",
    ),

    # 6 — Symbol list
    Question(
        id="cur_list_symbols",
        text="What symbols are available in FrontierView?",
        expected_tool_path=["list_symbols"],
        gt_values={},
        source="curated",
        notes="Qualitative. Tool-path check only.",
    ),

    # 7 — Symbol reference
    Question(
        id="cur_symbol_ref_aapl",
        text="What are AAPL's market parameters — ADV, volatility, and bid-ask spread?",
        expected_tool_path=["get_symbol_reference"],
        gt_values={},
        source="curated",
        notes="Reference lookup. Tool-path check only.",
    ),

    # 8 — Model description
    Question(
        id="cur_describe_model",
        text=(
            "Can you explain the FrontierView market impact model "
            "and where the parameters come from?"
        ),
        expected_tool_path=["describe_model"],
        gt_values={},
        source="curated",
        notes="Qualitative. Tool-path check only.",
    ),

    # 9 — Short horizon (30 min)
    Question(
        id="cur_cost_aapl_30min",
        text=(
            "I need to buy 100,000 AAPL shares in 30 minutes. "
            "What's the estimated execution cost in bps?"
        ),
        expected_tool_path=["cost_and_variance"],
        gt_values={
            "expected_cost_bps": _gt("AAPL", 100_000, 0.5, "twap")["expected_cost_bps"],
        },
        source="curated",
        notes="0.5h horizon. GT anchored on TWAP.",
    ),

    # 10 — Out-of-tool: calibration request
    Question(
        id="cur_out_calibrate",
        text=(
            "I have my own trade data. "
            "Can you calibrate the model to my recent fills?"
        ),
        expected_tool_path=[],
        gt_values={},
        out_of_tool=True,
        source="curated",
        notes=(
            "No calibration tool exposed. Correct: decline or caveat. "
            "Fail: fabricated calibrated values."
        ),
    ),

    # 11 — Out-of-tool: price prediction
    Question(
        id="cur_out_price_predict",
        text="What will AAPL's stock price be next week?",
        expected_tool_path=[],
        gt_values={},
        out_of_tool=True,
        source="curated",
        notes="FrontierView does not predict prices. Agent must decline.",
    ),

    # 12 — Out-of-tool: autonomous trading algorithm
    Question(
        id="cur_out_algo_request",
        text=(
            "Write me a trading algorithm I can run autonomously "
            "to minimise my market impact."
        ),
        expected_tool_path=[],
        gt_values={},
        out_of_tool=True,
        source="curated",
        notes="Out of scope. Agent must decline or caveat.",
    ),

    # 13 — All-4-schedules comparison
    Question(
        id="cur_compare_all4_spy",
        text=(
            "What's cheapest: TWAP, front-loaded, back-loaded, or AC-optimal "
            "for 100,000 shares of SPY over 2 hours?"
        ),
        expected_tool_path=["compare_schedules"],
        gt_values={
            "cheapest_cost_bps": min(
                _gt("SPY", 100_000, 2.0, "twap")["expected_cost_bps"],
                _gt("SPY", 100_000, 2.0, "front_loaded")["expected_cost_bps"],
                _gt("SPY", 100_000, 2.0, "back_loaded")["expected_cost_bps"],
                _gt("SPY", 100_000, 2.0, "ac_linear")["expected_cost_bps"],
            ),
        },
        source="curated",
    ),

    # 14 — Variance comparison (front vs back)
    Question(
        id="cur_compare_var_msft",
        text=(
            "Compare the execution shortfall variance of front-loaded versus "
            "back-loaded schedules for 200,000 shares of MSFT over 2 hours."
        ),
        expected_tool_path=["compare_schedules"],
        gt_values={
            "cheapest_cost_bps": min(
                _gt("MSFT", 200_000, 2.0, "front_loaded")["expected_cost_bps"],
                _gt("MSFT", 200_000, 2.0, "back_loaded")["expected_cost_bps"],
            ),
        },
        source="curated",
    ),

    # 15 — Large order (1x ADV)
    Question(
        id="cur_large_order_jpm",
        text=(
            "I need to trade 12,000,000 shares of JPM over a full trading day "
            "(6.5 hours). Is that feasible?"
        ),
        expected_tool_path=["cost_and_variance"],
        gt_values={
            "expected_cost_bps": _gt("JPM", 12_000_000, 6.5, "twap")["expected_cost_bps"],
        },
        source="curated",
        notes=(
            "12M shares = 1x JPM ADV. Tool returns unreliability warning. "
            "Answer must include caveat."
        ),
    ),

    # 16 — Compositional: optimal schedule + frontier
    Question(
        id="cur_multi_optimal_and_frontier",
        text=(
            "For a 100,000-share AAPL order over 2 hours at lambda=1e-6, "
            "what is the optimal schedule cost and variance? Also show the efficient frontier."
        ),
        expected_tool_path=["optimal_schedule", "efficient_frontier"],
        gt_values={
            "expected_cost_bps": _gt("AAPL", 100_000, 2.0, "ac_linear", 1e-6)["expected_cost_bps"],
            "variance_bps2": _gt("AAPL", 100_000, 2.0, "ac_linear", 1e-6)["variance_bps2"],
        },
        source="curated",
        notes="Multi-part; both tools expected.",
    ),

    # 17 — Decomposition (temporary, permanent, spread)
    Question(
        id="cur_decomposition_aapl_twap",
        text=(
            "Break down the execution cost for a 100,000-share AAPL TWAP order over 2 hours: "
            "how much is temporary impact, permanent impact, and spread cost?"
        ),
        expected_tool_path=["cost_and_variance"],
        gt_values={
            "expected_cost_bps": _gt("AAPL", 100_000, 2.0, "twap")["expected_cost_bps"],
            "temporary_bps": _gt("AAPL", 100_000, 2.0, "twap")["temporary_bps"],
            "permanent_bps": _gt("AAPL", 100_000, 2.0, "twap")["permanent_bps"],
            "spread_bps": _gt("AAPL", 100_000, 2.0, "twap")["spread_bps"],
        },
        tolerance_overrides={
            "temporary_bps": {"rtol": 0.01, "atol": 1e-4},
            "permanent_bps": {"rtol": 0.01, "atol": 1e-4},
            "spread_bps": {"rtol": 0.01, "atol": 1e-4},
        },
        source="curated",
    ),

    # 18 — Eta sweep
    Question(
        id="cur_sweep_eta_spy",
        text=(
            "How does the SPY TWAP cost for 200,000 shares over 2 hours change "
            "as the temporary impact coefficient eta varies from 0.1 to 0.2?"
        ),
        expected_tool_path=["sweep"],
        gt_values={
            "cost_at_range_start_bps": _with_eta("SPY", 0.1, 200_000, 2.0),
            "cost_at_range_end_bps": _with_eta("SPY", 0.2, 200_000, 2.0),
            "cost_delta_bps": (
                _with_eta("SPY", 0.2, 200_000, 2.0)
                - _with_eta("SPY", 0.1, 200_000, 2.0)
            ),
        },
        source="curated",
    ),

    # 19 — Variance focus
    Question(
        id="cur_variance_spy_optimal",
        text=(
            "What is the expected shortfall variance for an Almgren-Chriss optimal SPY trade "
            "of 500,000 shares over 4 hours at lambda=1e-6?"
        ),
        expected_tool_path=["optimal_schedule"],
        gt_values={
            "variance_bps2": _gt("SPY", 500_000, 4.0, "ac_linear", 1e-6)["variance_bps2"],
        },
        source="curated",
    ),

    # 20 — Symbol reference lookup (ADV / volatility)
    Question(
        id="cur_symbol_ref_googl",
        text="What is GOOGL's average daily volume and daily volatility?",
        expected_tool_path=["get_symbol_reference"],
        gt_values={},
        source="curated",
        notes="Reference lookup. Tool-path check only.",
    ),
]
