"""FrontierView FastAPI application — market impact analysis endpoints."""

from dataclasses import dataclass

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from api.rate_limit import limiter

from api.calibration import router as calibration_router
from api.models import (
    AnalyseRequest,
    AnalyseResponse,
    FrontierPoint,
    ImpactDecomp,
    ModelParams,
    RegimeFrontierPoint,
    RegimeFrontierResponse,
    ScheduleBin,
)
from api.market_impact import (
    SYMBOL_PARAMS,
    TRADING_HOURS_PER_DAY,
    SymbolParams,
    compute_cost_variance,
    generate_frontier,
    permanent_impact,
    schedule_ac_linear,
    schedule_back_loaded,
    schedule_front_loaded,
    schedule_twap,
    temporary_impact,
)

app = FastAPI(title="FrontierView", version="0.1.0")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/docs", StaticFiles(directory="docs"), name="docs")
app.include_router(calibration_router)


@app.get("/")
def root():
    return FileResponse("docs/index.html", media_type="text/html")


@app.get("/about")
def about():
    return FileResponse("docs/about.html", media_type="text/html")


@app.get("/calibration")
def calibration():
    return FileResponse("docs/calibration.html", media_type="text/html")


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
    horizon_hours: float,
) -> ImpactDecomp:
    """Compute cost components and shortfall variance for the schedule."""
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
    _, variance = compute_cost_variance(raw, ctx.order_size, side, params, horizon_hours)
    return ImpactDecomp(
        temporary_bps=round(temp_cost, 4),
        permanent_bps=round(perm_cost, 4),
        spread_bps=round(spread_cost, 4),
        variance_bps2=round(variance, 4),
    )


_REGIMES = {
    "calm":     {"sigma": 0.6,  "eta": 1.0, "gamma": 1.0},
    "normal":   {"sigma": 1.0,  "eta": 1.0, "gamma": 1.0},
    "stressed": {"sigma": 1.8,  "eta": 1.3, "gamma": 1.3},
}


@app.get("/health")
def health() -> dict:
    """Liveness check."""
    return {"status": "ok"}


@app.post("/api/regime-frontier", response_model=RegimeFrontierResponse)
def regime_frontier(request: AnalyseRequest) -> RegimeFrontierResponse:
    """Generate efficient frontiers under calm, normal, and stressed market regimes."""
    symbol = request.symbol.upper()
    if symbol not in SYMBOL_PARAMS:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown symbol '{symbol}'. Supported: {sorted(SYMBOL_PARAMS)}",
        )

    base = SYMBOL_PARAMS[symbol]
    n_bins = max(2, round(request.horizon_hours * 2))

    frontiers: dict[str, list[RegimeFrontierPoint]] = {}
    for regime, mults in _REGIMES.items():
        scaled = SymbolParams(
            adv=base.adv,
            sigma=base.sigma * mults["sigma"],
            half_spread=base.half_spread,
            eta=base.eta * mults["eta"],
            gamma=base.gamma * mults["gamma"],
        )
        pts = generate_frontier(request.order_size, request.side, request.horizon_hours, scaled, n_bins)
        frontiers[regime] = [
            RegimeFrontierPoint(expected_cost_bps=p["expected_cost_bps"], variance_bps2=p["variance_bps2"])
            for p in pts
        ]

    return RegimeFrontierResponse(**frontiers)


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
        impact_decomp=_decompose_impact(raw_schedule, ctx, request.side, params, request.horizon_hours),
        model_params=ModelParams(
            eta=params.eta,
            gamma=params.gamma,
            sigma=params.sigma,
            adv=params.adv,
            half_spread=params.half_spread,
        ),
    )
