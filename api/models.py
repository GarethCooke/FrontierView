from typing import Literal
from pydantic import BaseModel


class AnalyseRequest(BaseModel):
    symbol: str
    order_size: float
    side: Literal["buy", "sell"]
    horizon_hours: float
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
