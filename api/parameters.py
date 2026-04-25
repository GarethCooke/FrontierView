"""
Single source of truth for all model parameters used in FrontierView.

These values are Almgren 2005 literature central estimates and must not be changed
without explicit instruction and a citation update in model_assumptions.md.
η = 0.142, γ = 0.314.
"""

from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Almgren et al. (2005) literature constants
# Source: Table 3, "Direct estimation of equity market impact", Risk 18(7).
# ---------------------------------------------------------------------------

ALMGREN_ETA: float = 0.142    # temporary impact coefficient
ALMGREN_GAMMA: float = 0.314  # permanent impact coefficient


# ---------------------------------------------------------------------------
# Per-symbol market defaults
# Representative values for liquid US equities (~2023 conditions).
# ADV: shares/day.  sigma: daily return volatility (fractional).
# half_spread: half bid-ask spread in bps.
# ---------------------------------------------------------------------------

@dataclass
class SymbolParams:
    """Market parameters for a single symbol."""

    adv: float         # average daily volume (shares/day)
    sigma: float       # daily return volatility (fractional, e.g. 0.02 = 2%)
    half_spread: float # half bid-ask spread in bps
    eta: float         # temporary impact coefficient
    gamma: float       # permanent impact coefficient


SYMBOL_PARAMS: dict[str, SymbolParams] = {
    "AAPL": SymbolParams(adv=60_000_000, sigma=0.0155, half_spread=0.30, eta=ALMGREN_ETA, gamma=ALMGREN_GAMMA),
    "MSFT": SymbolParams(adv=25_000_000, sigma=0.0160, half_spread=0.35, eta=ALMGREN_ETA, gamma=ALMGREN_GAMMA),
    "GOOGL": SymbolParams(adv=22_000_000, sigma=0.0175, half_spread=0.40, eta=ALMGREN_ETA, gamma=ALMGREN_GAMMA),
    "JPM":  SymbolParams(adv=12_000_000, sigma=0.0185, half_spread=0.50, eta=ALMGREN_ETA, gamma=ALMGREN_GAMMA),
    "SPY":  SymbolParams(adv=80_000_000, sigma=0.0090, half_spread=0.15, eta=ALMGREN_ETA, gamma=ALMGREN_GAMMA),
}
