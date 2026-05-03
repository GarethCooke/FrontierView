from typing import Literal
from pydantic import BaseModel, Field

ValidSymbol = Literal["AAPL", "MSFT", "GOOGL", "JPM", "SPY"]


class AnalyseRequest(BaseModel):
    symbol: ValidSymbol
    order_size: float = Field(gt=0, le=1_000_000_000)
    horizon_hours: float = Field(gt=0, le=24)
    schedule_type: Literal["twap", "front_loaded", "back_loaded", "ac_linear"]


class FrontierPoint(BaseModel):
    lambda_val: float
    expected_cost_bps: float
    variance_bps2: float


class ScheduleBin(BaseModel):
    time_bin: int
    participation_rate: float
    cumulative_filled: float


class ImpactDecomp(BaseModel):
    temporary_bps: float
    permanent_bps: float
    spread_bps: float
    variance_bps2: float


class ModelParams(BaseModel):
    eta: float
    gamma: float
    sigma: float
    adv: float
    half_spread: float


class AnalyseResponse(BaseModel):
    frontier: list[FrontierPoint]
    schedule: list[ScheduleBin]
    impact_decomp: ImpactDecomp
    model_params: ModelParams


class RegimeFrontierPoint(BaseModel):
    expected_cost_bps: float
    variance_bps2: float


class RegimeFrontierResponse(BaseModel):
    calm: list[RegimeFrontierPoint]
    normal: list[RegimeFrontierPoint]
    stressed: list[RegimeFrontierPoint]
