"""FrontierView FastAPI application — market impact analysis endpoints."""

from dataclasses import dataclass

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from api.models import (
    AnalyseRequest,
    AnalyseResponse,
    FrontierPoint,
    ImpactDecomp,
    ModelParams,
    ScheduleBin,
)
from api.market_impact import (
    SYMBOL_PARAMS,
    TRADING_HOURS_PER_DAY,
    SymbolParams,
    generate_frontier,
    permanent_impact,
    schedule_ac_linear,
    schedule_back_loaded,
    schedule_front_loaded,
    schedule_twap,
    temporary_impact,
)

app = FastAPI(title="FrontierView", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_SCHEDULE_FNS = {
    "twap": schedule_twap,
    "front_loaded": schedule_front_loaded,
    "back_loaded": schedule_back_loaded,
}


@dataclass(frozen=True)
class _BinCtx:
    """Derived per-request constants shared by schedule and decomposition helpers."""

    v_hourly: float
    dt: float
    order_size: float


def _build_schedule(raw: list[tuple[int, float]], ctx: _BinCtx) -> list[ScheduleBin]:
    """Annotate raw (bin, participation) pairs with cumulative fill fraction."""
    out: list[ScheduleBin] = []
    cumulative = 0.0
    for time_bin, participation_rate in raw:
        cumulative += participation_rate * ctx.v_hourly * ctx.dt
        out.append(ScheduleBin(
            time_bin=time_bin,
            participation_rate=round(participation_rate, 6),
            cumulative_filled=round(min(cumulative / ctx.order_size, 1.0), 6),
        ))
    return out


def _decompose_impact(
    raw: list[tuple[int, float]],
    ctx: _BinCtx,
    side: str,
    params: SymbolParams,
) -> ImpactDecomp:
    """Compute temporary, spread, and permanent cost components independently."""
    temp_cost = spread_cost = perm_cost = 0.0
    remaining = ctx.order_size
    for _, p in raw:
        v = p * ctx.v_hourly
        weight = v * ctx.dt / ctx.order_size
        temp_cost += temporary_impact(v, ctx.v_hourly, params.sigma, params.eta) * weight
        spread_cost += params.half_spread * weight
        perm_cost += (
            permanent_impact(v, ctx.v_hourly, params.sigma, params.gamma)
            * (remaining / ctx.order_size) * weight
        )
        remaining -= v * ctx.dt
    return ImpactDecomp(
        temporary_bps=round(temp_cost, 4),
        permanent_bps=round(perm_cost, 4),
        spread_bps=round(spread_cost, 4),
    )


@app.get("/health")
def health() -> dict:
    """Liveness check."""
    return {"status": "ok"}


@app.post("/analyse", response_model=AnalyseResponse)
def analyse(request: AnalyseRequest) -> AnalyseResponse:
    """Run market impact analysis and return schedule, decomposition, and frontier."""
    symbol = request.symbol.upper()
    if symbol not in SYMBOL_PARAMS:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown symbol '{symbol}'. Supported: {sorted(SYMBOL_PARAMS)}",
        )

    params = SYMBOL_PARAMS[symbol]
    n_bins = max(2, round(request.horizon_hours * 2))
    ctx = _BinCtx(
        v_hourly=params.adv / TRADING_HOURS_PER_DAY,
        dt=request.horizon_hours / n_bins,
        order_size=request.order_size,
    )

    if request.schedule_type == "ac_linear":
        raw_schedule = schedule_ac_linear(
            n_bins, request.order_size, request.horizon_hours, params,
        )
    else:
        raw_schedule = _SCHEDULE_FNS[request.schedule_type](
            n_bins, request.order_size, ctx.v_hourly, ctx.dt,
        )

    frontier_out = [
        FrontierPoint(
            lambda_val=pt["lambda_val"],
            expected_cost_bps=pt["expected_cost_bps"],
            variance_bps2=pt["variance_bps2"],
        )
        for pt in generate_frontier(
            request.order_size, request.side, request.horizon_hours, params, n_bins,
        )
    ]

    return AnalyseResponse(
        frontier=frontier_out,
        schedule=_build_schedule(raw_schedule, ctx),
        impact_decomp=_decompose_impact(raw_schedule, ctx, request.side, params),
        model_params=ModelParams(
            eta=params.eta,
            gamma=params.gamma,
            sigma=params.sigma,
            adv=params.adv,
            half_spread=params.half_spread,
        ),
    )
