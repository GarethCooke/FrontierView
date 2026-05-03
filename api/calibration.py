"""Calibration endpoint — synthetic parameter recovery for Almgren-Chriss."""

import numpy as np
from scipy import stats
from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from api.parameters import ALMGREN_ETA, ALMGREN_GAMMA, SYMBOL_PARAMS
from api.rate_limit import limiter

# ─────────────────────────────────────────────────────────────────────────────
# Ground truth (Almgren 2005, Table 3) — imported from api.parameters
# ─────────────────────────────────────────────────────────────────────────────
ETA_TRUE   = ALMGREN_ETA    # 0.142
GAMMA_TRUE = ALMGREN_GAMMA  # 0.314

# ─────────────────────────────────────────────────────────────────────────────
# Symbol universe — imported from api.parameters (single source of truth)
# ─────────────────────────────────────────────────────────────────────────────
SYMBOLS      = list(SYMBOL_PARAMS.keys())
SYMBOL_PROBS = np.full(len(SYMBOLS), 1.0 / len(SYMBOLS))

# ─────────────────────────────────────────────────────────────────────────────
# Noise scaling constants
# ─────────────────────────────────────────────────────────────────────────────
# The volatility-driven noise model:
#   price-path noise std = σ · √(T/6.5)
# We scale by K_DRIFT and K_EXEC so that at N=5000 the WLS standard errors
# match the Almgren 2005 Table 3 benchmarks (γ: ~13%, η: ~4%).
#
# Derivation (see below):
#   SE(γ̂) = K_DRIFT / √(Σ f_i²·6.5/T_i)
#          ≈ K_DRIFT / √(N · E[f²] · 6.5 · E[1/T])
#   K_DRIFT s.t. SE(γ̂)/γ = 0.13  →  K_DRIFT ≈ 0.0884
#
#   SE(η̂)  = K_EXEC  / √(Σ x_η,i²/(σ_i²·T_i/6.5))
#          ≈ K_EXEC  / √(N · 6.5^(-0.2) · E[f^1.2] · E[1/T^2.2])
#   K_EXEC  s.t. SE(η̂)/η = 0.04   →  K_EXEC  ≈ 0.0164
#
# Physical interpretation: the full random walk σ·√(T/6.5) requires ~1.6 M
# observations to achieve the same SE; our 5 000-order demo uses a calibrated
# fraction (≈8.8% and ≈1.6% of the full walk respectively).
K_DRIFT = 0.0884   # fraction of σ·√(T/6.5) applied to I_obs noise
K_EXEC  = 0.0164   # fraction of σ·√(T/6.5) applied to execution noise


# ─────────────────────────────────────────────────────────────────────────────
# Core functions — copied verbatim from verify_calibration.py
# ─────────────────────────────────────────────────────────────────────────────

def generate_fills(seed: int, n_orders: int) -> dict:
    """
    Generate synthetic parent orders and their execution costs.

    Noise model (Almgren 2005 §4):
      - Post-trade drift measurement has noise driven by the price random walk.
      - ε_drift ~ N(0, (K_DRIFT · σ · √(T/6.5))²)
      - ε_exec  ~ N(0, (K_EXEC  · σ · √(T/6.5))²)

    Observed quantities:
      I_obs = I_true + ε_drift                          (post-trade drift, cost)
      J_obs = J_true + ε_exec + ε_drift/2              (implementation shortfall)
      T_fit = J_obs  − I_obs/2 = h_true + ε_exec       (temporary estimator — exact identity)

    The T_fit identity confirms the decomposition is methodologically sound:
    the temporary estimator is unbiased and its noise comes from execution only.
    """
    rng = np.random.default_rng(seed)

    # ── Symbol sampling ────────────────────────────────────────────────────
    sym_idx = rng.choice(len(SYMBOLS), size=n_orders, p=SYMBOL_PROBS)
    symbols  = np.array(SYMBOLS)[sym_idx]
    sigmas   = np.array([SYMBOL_PARAMS[s].sigma for s in symbols])
    advs     = np.array([SYMBOL_PARAMS[s].adv   for s in symbols])

    # ── Order parameters ───────────────────────────────────────────────────
    # participation = X/V ∈ [0.1%, 5%], log-uniform
    log_lo, log_hi = np.log(0.001), np.log(0.05)
    participation = np.exp(rng.uniform(log_lo, log_hi, size=n_orders))
    horizon       = rng.uniform(0.5, 6.0, size=n_orders)   # execution horizon, hours

    # ── Derived quantities ─────────────────────────────────────────────────
    X = participation * advs          # total shares
    v = X / horizon                   # execution rate, shares/hour

    # ── True impacts (fractional price units = cost to trader) ─────────────
    I_true = GAMMA_TRUE * sigmas * (X / advs)               # permanent component
    h_true = ETA_TRUE   * sigmas * (v / (6.5 * advs))**0.6 # temporary component
    J_true = h_true + I_true / 2                             # total IS (Almgren convention)

    # ── Volatility-driven noise ────────────────────────────────────────────
    sigma_path = sigmas * np.sqrt(horizon / 6.5)  # price-path diffusion std
    eps_drift  = rng.normal(0.0, K_DRIFT * sigma_path)
    eps_exec   = rng.normal(0.0, K_EXEC  * sigma_path)

    # ── Observed quantities ────────────────────────────────────────────────
    I_obs = I_true + eps_drift
    J_obs = J_true + eps_exec + eps_drift / 2

    # Almgren §4 decomposition — THIS IS THE METHODOLOGY BEING DEMONSTRATED
    T_fit = J_obs - I_obs / 2     # ≡ h_true + eps_exec  (exactly, by construction)

    return dict(
        symbols=symbols, sigmas=sigmas, advs=advs,
        participation=participation, horizon=horizon,
        X=X, v=v,
        I_true=I_true, h_true=h_true, J_true=J_true,
        eps_drift=eps_drift, eps_exec=eps_exec,
        I_obs=I_obs, J_obs=J_obs, T_fit=T_fit,
    )


def fit_parameters(fills: dict) -> dict:
    """
    Recover η and γ via heteroskedastic WLS (Almgren 2005 §4).

    Weight choice: w_i ∝ 1 / Var(noise_i) = 6.5 / (σ_i² · T_i)
    This downweights long-horizon, high-volatility orders whose drift
    measurement noise is largest — exactly the heteroskedasticity structure
    described in the paper.

    Standard errors are computed from empirical weighted residuals, not
    assumed noise structure, for robustness.
    """
    σ  = fills["sigmas"]
    T  = fills["horizon"]
    X  = fills["X"]
    V  = fills["advs"]
    v  = fills["v"]

    # Common WLS weight (drives out heteroskedasticity from price-path noise)
    w = 6.5 / (σ**2 * T)

    # ── Permanent impact regression ────────────────────────────────────────
    # Model: I_obs = γ · (σ · X/V) + noise
    x_perm = σ * (X / V)
    y_perm = fills["I_obs"]

    gamma_hat    = np.sum(w * x_perm * y_perm) / np.sum(w * x_perm**2)
    resid_perm   = y_perm - gamma_hat * x_perm
    n            = len(y_perm)
    # Empirical weighted residual variance (normalise to 1 if weights are exact)
    sigma_sq_perm = np.sum(w * resid_perm**2) / (n - 1)
    gamma_se      = np.sqrt(sigma_sq_perm / np.sum(w * x_perm**2))

    # Weighted R²
    y_perm_wbar = np.average(y_perm, weights=w)
    r2_perm = 1 - np.sum(w * resid_perm**2) / np.sum(w * (y_perm - y_perm_wbar)**2)

    # Standardised residuals for QQ
    std_resid_perm = resid_perm / np.sqrt(sigma_sq_perm / w)

    # ── Temporary impact regression ────────────────────────────────────────
    # Model: T_fit = η · σ·(v/(6.5V))^0.6 + noise
    x_temp = σ * (v / (6.5 * V))**0.6
    y_temp = fills["T_fit"]

    eta_hat      = np.sum(w * x_temp * y_temp) / np.sum(w * x_temp**2)
    resid_temp   = y_temp - eta_hat * x_temp
    sigma_sq_temp = np.sum(w * resid_temp**2) / (n - 1)
    eta_se        = np.sqrt(sigma_sq_temp / np.sum(w * x_temp**2))

    y_temp_wbar = np.average(y_temp, weights=w)
    r2_temp = 1 - np.sum(w * resid_temp**2) / np.sum(w * (y_temp - y_temp_wbar)**2)

    std_resid_temp = resid_temp / np.sqrt(sigma_sq_temp / w)

    return dict(
        gamma_hat=gamma_hat, gamma_se=gamma_se, r2_perm=r2_perm,
        std_resid_perm=std_resid_perm, x_perm=x_perm, y_perm=y_perm,
        eta_hat=eta_hat, eta_se=eta_se, r2_temp=r2_temp,
        std_resid_temp=std_resid_temp, x_temp=x_temp, y_temp=y_temp,
    )


# ─────────────────────────────────────────────────────────────────────────────
# FastAPI router
# ─────────────────────────────────────────────────────────────────────────────

router = APIRouter()


class CalibrationRequest(BaseModel):
    seed: int = 42
    n_orders: int = Field(5000, ge=100, le=20000)


@router.post("/api/calibration/run")
@limiter.limit("10/minute")
def run_calibration(request: Request, payload: CalibrationRequest) -> dict:
    fills = generate_fills(payload.seed, payload.n_orders)
    fit = fit_parameters(fills)

    symbols = fills["symbols"]
    x_perm = fit["x_perm"]
    y_perm = fit["y_perm"]
    x_temp = fit["x_temp"]
    y_temp = fit["y_temp"]

    # Scatter: 100 points per symbol — take the first 100 indices for each
    # symbol (these appear in random order in the array due to the fill RNG).
    scatter_temp: list[dict] = []
    scatter_perm: list[dict] = []
    for sym in SYMBOLS:
        idx = np.where(symbols == sym)[0]
        chosen = idx[:min(100, len(idx))]
        for i in chosen:
            scatter_temp.append({"x": float(x_temp[i]), "y": float(y_temp[i]), "symbol": sym})
            scatter_perm.append({"x": float(x_perm[i]), "y": float(y_perm[i]), "symbol": sym})

    # Fitted curves: 100 points across x-range of scatter
    x_t_range = np.linspace(float(x_temp.min()), float(x_temp.max()), 100)
    x_p_range = np.linspace(float(x_perm.min()), float(x_perm.max()), 100)
    eta_hat   = float(fit["eta_hat"])
    gamma_hat = float(fit["gamma_hat"])
    fitted_temp = [{"x": float(x), "y": eta_hat * float(x)}   for x in x_t_range]
    fitted_perm = [{"x": float(x), "y": gamma_hat * float(x)} for x in x_p_range]

    # QQ plots via scipy.stats.probplot on standardised residuals
    (qq_t_osm, qq_t_osr), _ = stats.probplot(fit["std_resid_temp"], dist="norm")
    (qq_p_osm, qq_p_osr), _ = stats.probplot(fit["std_resid_perm"], dist="norm")

    eta_se   = float(fit["eta_se"])
    gamma_se = float(fit["gamma_se"])

    return {
        "seed": payload.seed,
        "n_orders": payload.n_orders,
        "true_params": {"eta": ETA_TRUE, "gamma": GAMMA_TRUE},
        "fitted_params": {
            "eta_hat": eta_hat,
            "eta_se": eta_se,
            "eta_rel_se_pct": eta_se / ETA_TRUE * 100.0,
            "gamma_hat": gamma_hat,
            "gamma_se": gamma_se,
            "gamma_rel_se_pct": gamma_se / GAMMA_TRUE * 100.0,
        },
        "fit_stats": {
            "r2_temporary": float(fit["r2_temp"]),
            "r2_permanent": float(fit["r2_perm"]),
            "n_obs": payload.n_orders,
        },
        "plot_data": {
            "scatter": {
                "temporary": scatter_temp,
                "permanent": scatter_perm,
            },
            "fitted_curves": {
                "temporary": fitted_temp,
                "permanent": fitted_perm,
            },
            "qq": {
                "temporary": {
                    "theoretical": [float(v) for v in qq_t_osm],
                    "sample":      [float(v) for v in qq_t_osr],
                },
                "permanent": {
                    "theoretical": [float(v) for v in qq_p_osm],
                    "sample":      [float(v) for v in qq_p_osr],
                },
            },
        },
    }
