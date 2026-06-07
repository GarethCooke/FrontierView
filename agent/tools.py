"""
FrontierView agent tool registry.

Single definition rule:  each tool's input_schema is generated from its Pydantic
input model (_INPUT_MODELS).  The same model validates incoming args at dispatch
time, so the schema advertised to the model == the schema enforced at dispatch.
"""
from __future__ import annotations

import math
from typing import Any, Literal, TypedDict, Union, cast

from pydantic import BaseModel, Field, field_validator, model_validator

from api.market_impact import (
    TRADING_HOURS_PER_DAY,
    compute_cost_breakdown,
    compute_cost_variance,
    schedule_ac_linear,
    schedule_back_loaded,
    schedule_front_loaded,
    schedule_twap,
)
from api.parameters import (
    ALMGREN_ETA,
    ALMGREN_GAMMA,
    SYMBOL_PARAMS,
    SymbolParams,
)
from agent import detail_store

class ToolResult(TypedDict, total=False):
    summary: dict[str, Any]
    detail_id: str
    error: str
    allowed: list[str] | None
    detail: str
    field: str
    got: Any
    unreliable: bool


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_ALLOWED_SYMBOLS: list[str] = sorted(SYMBOL_PARAMS)
_CANONICAL_SCHEDULES = ("twap", "front_loaded", "back_loaded", "ac_linear")
_VALID_SWEEP_PARAMS = (
    "structural.temp_exponent",
    "calibrated.eta",
    "calibrated.gamma",
    "market.sigma",
    "market.half_spread",
    "market.adv",
)


def _check_symbol(v: str) -> str:
    if v not in SYMBOL_PARAMS:
        raise ValueError(
            f"Unknown symbol '{v}'. Allowed: {', '.join(_ALLOWED_SYMBOLS)}"
        )
    return v


# ---------------------------------------------------------------------------
# Pydantic input models
# ---------------------------------------------------------------------------


class CostAndVarianceInput(BaseModel):
    symbol: str
    order_size: float = Field(gt=0)
    horizon_hours: float = Field(gt=0)
    schedule_type: Literal["twap", "front_loaded", "back_loaded", "ac_linear"]
    lambda_risk: float = Field(default=1e-6, gt=0)
    n_bins: int = Field(default=13, ge=1)

    @field_validator("symbol")
    @classmethod
    def _valid_symbol(cls, v: str) -> str:
        return _check_symbol(v)


class OptimalScheduleInput(BaseModel):
    symbol: str
    order_size: float = Field(gt=0)
    horizon_hours: float = Field(gt=0)
    lambda_risk: float = Field(gt=0)
    n_bins: int = Field(default=13, ge=1)

    @field_validator("symbol")
    @classmethod
    def _valid_symbol(cls, v: str) -> str:
        return _check_symbol(v)


class CompareSchedulesInput(BaseModel):
    symbol: str
    side: Literal["buy", "sell"]
    order_size: float = Field(gt=0)
    horizon_hours: float = Field(gt=0)
    schedules: list[Union[str, list[float]]] = Field(min_length=2)
    lambda_risk: float = Field(default=1e-6, gt=0, description="λ for ac_linear entries")
    n_bins: int = Field(default=13, ge=1)

    @field_validator("symbol")
    @classmethod
    def _valid_symbol(cls, v: str) -> str:
        return _check_symbol(v)

    @field_validator("schedules")
    @classmethod
    def _valid_schedules(cls, v: list) -> list:
        for item in v:
            if isinstance(item, str):
                if item not in _CANONICAL_SCHEDULES:
                    raise ValueError(
                        f"Unknown schedule '{item}'. Allowed: {_CANONICAL_SCHEDULES}"
                    )
            elif isinstance(item, list):
                if not item:
                    raise ValueError("Weight vector must not be empty")
                if not all(isinstance(x, (int, float)) for x in item):
                    raise ValueError("Weight vector entries must be numbers")
                total = sum(item)
                if abs(total - 1.0) > 1e-4:
                    raise ValueError(
                        f"Schedule weights must sum to 1.0 (got {total:.6f})"
                    )
            else:
                raise ValueError(
                    "Each schedule must be a canonical name string or a weight-vector list"
                )
        return v


class EfficientFrontierInput(BaseModel):
    symbol: str
    side: Literal["buy", "sell"]
    order_size: float = Field(gt=0)
    horizon_hours: float = Field(gt=0)
    lambda_range: list[float] = Field(
        default=[1e-9, 1e-1],
        min_length=2,
        max_length=2,
        description="[lambda_min, lambda_max] — log-spaced grid sampled between these bounds.",
    )
    n_points: int = Field(default=17, ge=3, le=50)
    n_bins: int = Field(default=13, ge=1)

    @field_validator("symbol")
    @classmethod
    def _valid_symbol(cls, v: str) -> str:
        return _check_symbol(v)

    @model_validator(mode="after")
    def _valid_lambda_range(self) -> "EfficientFrontierInput":
        lo, hi = self.lambda_range
        if lo <= 0 or hi <= 0:
            raise ValueError("lambda_range values must be positive")
        if lo >= hi:
            raise ValueError("lambda_range[0] must be strictly less than lambda_range[1]")
        return self


class SweepInput(BaseModel):
    symbol: str
    side: Literal["buy", "sell"]
    order_size: float = Field(gt=0)
    horizon_hours: float = Field(gt=0)
    schedule: Literal["twap", "front_loaded", "back_loaded", "ac_linear"]
    param: str = Field(description=f"Namespaced parameter to sweep. One of: {', '.join(_VALID_SWEEP_PARAMS)}.")
    param_range: list[float] = Field(
        min_length=2,
        max_length=2,
        description="[start, end] — linear grid between these bounds.",
    )
    n_points: int = Field(default=10, ge=2, le=50)
    lambda_risk: float = Field(default=1e-6, gt=0, description="λ for ac_linear schedule")
    n_bins: int = Field(default=13, ge=1)

    @field_validator("symbol")
    @classmethod
    def _valid_symbol(cls, v: str) -> str:
        return _check_symbol(v)

    @field_validator("param")
    @classmethod
    def _valid_param(cls, v: str) -> str:
        if v not in _VALID_SWEEP_PARAMS:
            raise ValueError(
                f"Unknown sweep param '{v}'. Allowed: {', '.join(_VALID_SWEEP_PARAMS)}"
            )
        return v

    @model_validator(mode="after")
    def _valid_range(self) -> "SweepInput":
        lo, hi = self.param_range
        if lo == hi:
            raise ValueError("param_range start and end must differ")
        return self


class ListSymbolsInput(BaseModel):
    pass


class GetSymbolReferenceInput(BaseModel):
    symbol: str

    @field_validator("symbol")
    @classmethod
    def _valid_symbol(cls, v: str) -> str:
        return _check_symbol(v)


class DescribeModelInput(BaseModel):
    pass


# ---------------------------------------------------------------------------
# Schema helpers
# ---------------------------------------------------------------------------

_SYMBOL_ENUM_OVERRIDE = {"enum": _ALLOWED_SYMBOLS}


def _build_schema(model: type[BaseModel], symbol_field: str = "symbol") -> dict:
    """Generate JSON schema from Pydantic model, adding enum for symbol field."""
    schema = model.model_json_schema()
    schema.pop("title", None)
    props = schema.get("properties", {})
    if symbol_field in props:
        props[symbol_field] = {**props[symbol_field], **_SYMBOL_ENUM_OVERRIDE}
    return schema


# Mapping: tool name → (Pydantic model, schema_dict)
_INPUT_MODELS: dict[str, type[BaseModel]] = {
    "cost_and_variance": CostAndVarianceInput,
    "optimal_schedule": OptimalScheduleInput,
    "compare_schedules": CompareSchedulesInput,
    "efficient_frontier": EfficientFrontierInput,
    "sweep": SweepInput,
    "list_symbols": ListSymbolsInput,
    "get_symbol_reference": GetSymbolReferenceInput,
    "describe_model": DescribeModelInput,
}

# Pre-computed schemas (one per tool, generated from models)
_SCHEMAS: dict[str, dict] = {
    name: _build_schema(model) for name, model in _INPUT_MODELS.items()
}

# ---------------------------------------------------------------------------
# TOOLS list advertised to the model
# ---------------------------------------------------------------------------

TOOLS: list[dict] = [
    {
        "name": "cost_and_variance",
        "description": (
            "Compute expected execution cost (bps) and variance (bps²) for a given trade "
            "execution schedule. Returns a summary with headline numbers and a detail_id "
            "for full bin-level data."
        ),
        "input_schema": _SCHEMAS["cost_and_variance"],
    },
    {
        "name": "optimal_schedule",
        "description": (
            "Compute the Almgren-Chriss optimal execution schedule for a given risk-aversion "
            "level λ, returning schedule bins and their cost/variance."
        ),
        "input_schema": _SCHEMAS["optimal_schedule"],
    },
    {
        "name": "compare_schedules",
        "description": (
            "Cost multiple execution schedules side-by-side for the same order. "
            "Accepts canonical schedule names (twap, front_loaded, back_loaded, ac_linear) "
            "and/or explicit weight vectors. Returns each schedule's cost (bps), variance, "
            "decomposition, and pairwise deltas vs the cheapest. Use this instead of "
            "repeated cost_and_variance calls when comparing schedules."
        ),
        "input_schema": _SCHEMAS["compare_schedules"],
    },
    {
        "name": "efficient_frontier",
        "description": (
            "Sweep risk-aversion λ over a log-spaced grid and return the cost/variance "
            "efficient frontier. Summary contains grid endpoints, knee, and point count; "
            "full per-λ data is in the detail payload."
        ),
        "input_schema": _SCHEMAS["efficient_frontier"],
    },
    {
        "name": "sweep",
        "description": (
            "Sensitivity analysis: re-run cost for a schedule while varying one model "
            "parameter over a range. Parameters are namespaced by class: "
            "structural (temp_exponent — changes model identity), "
            "calibrated (eta, gamma — Almgren 2005 estimates), "
            "market (sigma, half_spread, adv — observable inputs). "
            "Uses a per-call copy of params; global model state is never modified."
        ),
        "input_schema": _SCHEMAS["sweep"],
    },
    {
        "name": "list_symbols",
        "description": (
            "List available symbols. NOTE: these are stored reference values, not a live feed."
        ),
        "input_schema": _SCHEMAS["list_symbols"],
    },
    {
        "name": "get_symbol_reference",
        "description": (
            "Return stored reference values (ADV, σ, price, spread) for a symbol. "
            "NOTE: stored reference values only — not a live feed."
        ),
        "input_schema": _SCHEMAS["get_symbol_reference"],
    },
    {
        "name": "describe_model",
        "description": (
            "Return current model parameters and their provenance (Almgren 2005 Table 3) "
            "so the agent can explain model assumptions from ground truth."
        ),
        "input_schema": _SCHEMAS["describe_model"],
    },
]

# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


def dispatch(name: str, args: dict[str, Any]) -> ToolResult:
    """Validate args against the tool's Pydantic model, then call the implementation.

    Tool errors are returned as structured dicts the model can recover from.
    Unknown-tool errors carry the valid tool list.
    """
    if name not in _INPUT_MODELS:
        return {
            "error": "UnknownTool",
            "detail": f"Unknown tool '{name}'.",
            "allowed": sorted(_INPUT_MODELS),
        }

    model_cls = _INPUT_MODELS[name]
    try:
        validated = model_cls(**args)
    except Exception as exc:
        # Pydantic ValidationError or any unexpected error during construction
        errors = getattr(exc, "errors", None)
        if callable(errors):
            first = cast(Any, errors)()[0]
            loc = first.get("loc", ())
            field = ".".join(str(x) for x in loc) if loc else "unknown"
            detail = first.get("msg", str(exc))
            ctx = first.get("ctx", {})
            allowed = ctx.get("expected") or ctx.get("allowed") or None
        else:
            field = "unknown"
            detail = str(exc)
            allowed = None
        return {
            "error": "InvalidArgument",
            "field": field,
            "detail": detail,
            "got": args.get(field) if field != "unknown" else None,
            "allowed": allowed,
        }

    impl_map = {
        "cost_and_variance": _cost_and_variance,
        "optimal_schedule": _optimal_schedule,
        "compare_schedules": _compare_schedules,
        "efficient_frontier": _efficient_frontier,
        "sweep": _sweep,
        "list_symbols": _list_symbols,
        "get_symbol_reference": _get_symbol_reference,
        "describe_model": _describe_model,
    }
    try:
        return impl_map[name](validated)
    except Exception as exc:
        return {
            "error": type(exc).__name__,
            "detail": str(exc),
            "unreliable": True,
        }


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _adv_warning(order_size: float, params: SymbolParams) -> str | None:
    ratio = order_size / params.adv
    if ratio >= 3.0:
        return (
            f"Order ({order_size:,.0f} shares) is {ratio:.1f}× ADV; "
            "cost estimate is unreliable at this participation level."
        )
    if ratio >= 1.0:
        return (
            f"Order ({order_size:,.0f} shares) is {ratio:.1f}× ADV; "
            "estimate may be less reliable."
        )
    return None


def _run_named_schedule(
    name: str,
    n_bins: int,
    order_size: float,
    horizon_hours: float,
    params: SymbolParams,
    lambda_risk: float,
) -> list[tuple[int, float]]:
    v_hourly = params.adv / TRADING_HOURS_PER_DAY
    dt = horizon_hours / n_bins
    if name == "twap":
        return schedule_twap(n_bins, order_size, v_hourly, dt)
    if name == "front_loaded":
        return schedule_front_loaded(n_bins, order_size, v_hourly, dt)
    if name == "back_loaded":
        return schedule_back_loaded(n_bins, order_size, v_hourly, dt)
    if name == "ac_linear":
        return schedule_ac_linear(n_bins, order_size, horizon_hours, params, lambda_risk)
    raise ValueError(f"Unknown schedule: {name!r}")


def _weights_to_schedule(
    weights: list[float],
    n_bins: int,
    order_size: float,
    horizon_hours: float,
    v_hourly: float,
) -> list[tuple[int, float]]:
    """Convert explicit weight vector to participation schedule."""
    dt = horizon_hours / n_bins
    total = sum(weights)
    rates = [w / total * order_size / dt / v_hourly for w in weights]
    return list(enumerate(rates))


def _breakdown_to_dict(bd) -> dict:
    return {
        "temporary_bps": round(bd.temporary_bps, 4),
        "permanent_bps": round(bd.permanent_bps, 4),
        "spread_bps": round(bd.spread_bps, 4),
        "total_bps": round(bd.total_bps, 4),
        "variance_bps2": round(bd.variance_bps2, 4),
    }


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------


def _cost_and_variance(inp: CostAndVarianceInput) -> ToolResult:
    params = SYMBOL_PARAMS[inp.symbol]
    warning = _adv_warning(inp.order_size, params)

    schedule = _run_named_schedule(
        inp.schedule_type, inp.n_bins, inp.order_size, inp.horizon_hours, params, inp.lambda_risk
    )
    bd = compute_cost_breakdown(schedule, inp.order_size, params, inp.horizon_hours)
    bins = [{"bin": b, "participation_rate": round(r, 6)} for b, r in schedule]
    detail_id = detail_store.put({"schedule_bins": bins})

    summary: dict[str, Any] = {
        "expected_cost_bps": round(bd.total_bps, 4),
        "variance_bps2": round(bd.variance_bps2, 4),
        "decomposition": _breakdown_to_dict(bd),
        "symbol": inp.symbol,
        "order_size": inp.order_size,
        "horizon_hours": inp.horizon_hours,
        "schedule_type": inp.schedule_type,
    }
    if warning:
        summary["warning"] = warning
    return {"summary": summary, "detail_id": detail_id}


def _optimal_schedule(inp: OptimalScheduleInput) -> ToolResult:
    params = SYMBOL_PARAMS[inp.symbol]
    warning = _adv_warning(inp.order_size, params)

    schedule = schedule_ac_linear(
        inp.n_bins, inp.order_size, inp.horizon_hours, params, inp.lambda_risk
    )
    bd = compute_cost_breakdown(schedule, inp.order_size, params, inp.horizon_hours)
    bins = [{"bin": b, "participation_rate": round(r, 6)} for b, r in schedule]
    detail_id = detail_store.put({"schedule_bins": bins})

    summary: dict[str, Any] = {
        "expected_cost_bps": round(bd.total_bps, 4),
        "variance_bps2": round(bd.variance_bps2, 4),
        "decomposition": _breakdown_to_dict(bd),
        "lambda_risk": inp.lambda_risk,
        "symbol": inp.symbol,
        "order_size": inp.order_size,
        "horizon_hours": inp.horizon_hours,
        "n_bins_used": len(bins),
    }
    if warning:
        summary["warning"] = warning
    return {"summary": summary, "detail_id": detail_id}


def _compare_schedules(inp: CompareSchedulesInput) -> ToolResult:
    params = SYMBOL_PARAMS[inp.symbol]
    warning = _adv_warning(inp.order_size, params)
    v_hourly = params.adv / TRADING_HOURS_PER_DAY

    results = []
    detail_schedules = {}
    for idx, spec in enumerate(inp.schedules):
        if isinstance(spec, str):
            label = spec
            schedule = _run_named_schedule(
                spec, inp.n_bins, inp.order_size, inp.horizon_hours, params, inp.lambda_risk
            )
        else:  # explicit weight vector
            label = f"custom_{idx}"
            schedule = _weights_to_schedule(
                spec, inp.n_bins, inp.order_size, inp.horizon_hours, v_hourly
            )

        bd = compute_cost_breakdown(schedule, inp.order_size, params, inp.horizon_hours)
        results.append({
            "schedule": label,
            "expected_cost_bps": round(bd.total_bps, 4),
            "variance_bps2": round(bd.variance_bps2, 4),
            "decomposition": _breakdown_to_dict(bd),
        })
        detail_schedules[label] = [
            {"bin": b, "participation_rate": round(r, 6)} for b, r in schedule
        ]

    # Rank by cost ascending
    ranked = sorted(results, key=lambda x: x["expected_cost_bps"])
    cheapest = ranked[0]

    # Pairwise deltas vs cheapest
    for r in results:
        r["delta_vs_cheapest_bps"] = round(
            r["expected_cost_bps"] - cheapest["expected_cost_bps"], 4
        )

    detail_id = detail_store.put({
        "schedule_bins": detail_schedules,
        "ranked": ranked,
    })

    summary: dict[str, Any] = {
        "schedules": results,
        "cheapest": cheapest["schedule"],
        "cheapest_cost_bps": cheapest["expected_cost_bps"],
        "symbol": inp.symbol,
        "side": inp.side,
        "order_size": inp.order_size,
        "horizon_hours": inp.horizon_hours,
    }
    if warning:
        summary["warning"] = warning
    return {"summary": summary, "detail_id": detail_id}


def _efficient_frontier(inp: EfficientFrontierInput) -> ToolResult:
    params = SYMBOL_PARAMS[inp.symbol]
    warning = _adv_warning(inp.order_size, params)

    lo, hi = inp.lambda_range
    log_lo = math.log10(lo)
    log_hi = math.log10(hi)
    lambdas = [
        10 ** (log_lo + i * (log_hi - log_lo) / (inp.n_points - 1))
        for i in range(inp.n_points)
    ]

    points = []
    for lam in lambdas:
        schedule = schedule_ac_linear(
            inp.n_bins, inp.order_size, inp.horizon_hours, params, lam
        )
        cost, var = compute_cost_variance(schedule, inp.order_size, params, inp.horizon_hours)
        points.append({
            "lambda": lam,
            "expected_cost_bps": round(cost, 4),
            "variance_bps2": round(var, 4),
        })

    # Knee: maximum curvature heuristic — largest cost drop per variance unit
    knee = points[0]
    if len(points) > 2:
        best_ratio = -1.0
        for i in range(1, len(points) - 1):
            cost_drop = points[i - 1]["expected_cost_bps"] - points[i]["expected_cost_bps"]
            var_gain = points[i]["variance_bps2"] - points[i - 1]["variance_bps2"]
            if var_gain > 0:
                ratio = cost_drop / var_gain
                if ratio > best_ratio:
                    best_ratio = ratio
                    knee = points[i]

    detail_id = detail_store.put({"frontier_points": points})

    summary: dict[str, Any] = {
        "low_lambda_end": points[0],
        "high_lambda_end": points[-1],
        "knee": knee,
        "n_points": len(points),
        "symbol": inp.symbol,
        "side": inp.side,
        "order_size": inp.order_size,
        "horizon_hours": inp.horizon_hours,
    }
    if warning:
        summary["warning"] = warning
    return {"summary": summary, "detail_id": detail_id}


def _sweep(inp: SweepInput) -> ToolResult:
    base_params = SYMBOL_PARAMS[inp.symbol]
    warning = _adv_warning(inp.order_size, base_params)

    lo, hi = inp.param_range
    values = [lo + i * (hi - lo) / (inp.n_points - 1) for i in range(inp.n_points)]

    param_class, param_name = inp.param.split(".", 1)
    structural_note = None
    if param_class == "structural":
        structural_note = (
            "Sweeping structural parameters changes model identity. "
            "The 0.6 temporary-impact exponent is a literature calibration "
            "(Almgren 2005); sensitivity here should not be over-read."
        )
    elif param_class == "calibrated":
        structural_note = (
            f"Sweeping calibrated parameter '{param_name}' (Almgren 2005 Table 3 central estimate). "
            "Interpret as model uncertainty, not market uncertainty."
        )

    series = []
    for val in values:
        # Per-call copy — global SYMBOL_PARAMS is never mutated
        if param_class == "structural":
            # temp_exponent: pass directly to compute_cost_variance
            temp_exp = val
            p_copy = base_params
        else:
            temp_exp = 0.6
            # Build a modified copy of SymbolParams
            kw = {
                "adv": base_params.adv,
                "sigma": base_params.sigma,
                "half_spread": base_params.half_spread,
                "eta": base_params.eta,
                "gamma": base_params.gamma,
            }
            if param_name in kw:
                kw[param_name] = val
            else:
                raise ValueError(f"Cannot sweep unknown param '{param_name}'")
            p_copy = SymbolParams(**kw)

        schedule = _run_named_schedule(
            inp.schedule,
            inp.n_bins,
            inp.order_size,
            inp.horizon_hours,
            p_copy,
            inp.lambda_risk,
        )
        cost, var = compute_cost_variance(
            schedule, inp.order_size, p_copy, inp.horizon_hours, temp_exp
        )
        series.append({
            "param_value": val,
            "expected_cost_bps": round(cost, 4),
            "variance_bps2": round(var, 4),
        })

    costs = [pt["expected_cost_bps"] for pt in series]
    detail_id = detail_store.put({"sweep_series": series})

    summary: dict[str, Any] = {
        "param": inp.param,
        "param_class": param_class,
        "schedule": inp.schedule,
        "param_range": inp.param_range,
        "cost_at_range_start_bps": costs[0],
        "cost_at_range_end_bps": costs[-1],
        "cost_delta_bps": round(costs[-1] - costs[0], 4),
        "n_points": len(series),
        "symbol": inp.symbol,
        "side": inp.side,
        "order_size": inp.order_size,
        "horizon_hours": inp.horizon_hours,
    }
    if structural_note:
        summary["structural_caveat"] = structural_note
    if warning:
        summary["warning"] = warning
    return {"summary": summary, "detail_id": detail_id}


def _list_symbols(_inp: ListSymbolsInput) -> ToolResult:
    detail_id = detail_store.put({"symbols": _ALLOWED_SYMBOLS})
    return {
        "summary": {
            "symbols": _ALLOWED_SYMBOLS,
            "caveat": "Stored reference values — not a live feed.",
        },
        "detail_id": detail_id,
    }


def _get_symbol_reference(inp: GetSymbolReferenceInput) -> ToolResult:
    params = SYMBOL_PARAMS[inp.symbol]
    ref = {
        "adv_shares_per_day": params.adv,
        "sigma_daily": params.sigma,
        "half_spread_bps": params.half_spread,
        "eta": params.eta,
        "gamma": params.gamma,
    }
    detail_id = detail_store.put(ref)
    return {
        "summary": {
            "symbol": inp.symbol,
            **ref,
            "caveat": "Stored reference values — not a live feed.",
        },
        "detail_id": detail_id,
    }


def _describe_model(_inp: DescribeModelInput) -> ToolResult:
    payload = {
        "model": "Almgren-Chriss (2005) market impact model",
        "provenance": "Almgren et al., 'Direct estimation of equity market impact', Risk 18(7), 2005.",
        "parameters": {
            "eta": {
                "value": ALMGREN_ETA,
                "description": "Temporary impact coefficient (Table 3 central estimate)",
                "source": "Almgren 2005 Table 3",
            },
            "gamma": {
                "value": ALMGREN_GAMMA,
                "description": "Permanent impact coefficient (Table 3 central estimate)",
                "source": "Almgren 2005 Table 3",
            },
            "temp_exponent": {
                "value": 0.6,
                "description": "Power-law exponent for temporary impact; structural assumption",
                "source": "Almgren 2005 empirical calibration",
            },
        },
        "cost_components": [
            "Temporary impact: η·σ·(participation)^0.6",
            "Permanent impact: γ·σ·participation (linear by no-arbitrage)",
            "Half-spread cost: half_spread per unit weight",
        ],
        "notes": [
            "Per-symbol ADV, σ, and half-spread are stored reference values, not a live feed.",
            "The AC linear schedule minimises E[cost] + λ·Var[shortfall] on a linearised impact model.",
        ],
    }
    detail_id = detail_store.put(payload)
    return {
        "summary": {
            "model": payload["model"],
            "eta": ALMGREN_ETA,
            "gamma": ALMGREN_GAMMA,
            "temp_exponent": 0.6,
            "provenance": payload["provenance"],
        },
        "detail_id": detail_id,
    }
