# Model Assumptions

This document records every modelling decision made in FrontierView's market
impact engine. Update it whenever a parameter, formula, or convention changes.

---

## 1. Market structure

| Assumption         | Value / rationale                                            |
| ------------------ | ------------------------------------------------------------ |
| Trading day length | 6.5 hours (09:30–16:00 ET)                                   |
| Time bins          | Half-hour slots; `n_bins = round(horizon_hours × 2)`, min 2  |
| Volume unit        | Shares per hour throughout — never cumulative                |
| ADV basis          | Calendar-day ADV (shares), divided by 6.5 to get shares/hour |

---

## 2. Temporary impact

```text
power_law = η·σ_daily·(|v| / (6.5·V_hourly))^0.6
```

- `temporary_impact()` returns the **power-law component only** (in bps).
  The Almgren-Chriss formula includes a spread term ε·sgn(v), but in this
  implementation spread is handled as a separate component (see § 4).
- The 0.6 exponent is the empirical "square-root-ish" power from Almgren et al.
  (2005) and subsequent literature. It is not 0.5 because impact grows
  sublinearly but slightly faster than a pure square root in practice.
- **σ is daily** throughout the formula. The participation rate `|v|/(6.5·V)`
  is dimensionless so no explicit time scaling is needed inside the power law.
- Result is always non-negative: `power_law × 10 000` bps.

---

## 3. Permanent impact

```text
g = γ·σ_daily·(|v| / V_hourly)
```

- Linear in participation rate (per-hour), unsigned (always positive).
- **Sign convention**: permanent impact raises the average execution price for
  a buyer (bad: they pay more) and lowers it for a seller (also bad: they
  receive less). In both cases, the cost to execution quality is positive.
  No sign multiplier is needed; `permanent_impact()` returns an unsigned cost.
- The permanent component accumulates as the standard Almgren-Chriss path integral, discretised by the midpoint rule for second-order accuracy. Each bin pays the cumulative price drift from all prior bins plus half its own drift, weighted by its share fraction. With linear g, this reduces to γ · σ · X / (2 · V_hourly) independent of schedule shape — a property verified by `test_permanent_cost_schedule_invariant`.

---

## 4. Spread cost

- Each share crossed pays the half-spread `ε` (bps).
- Spread is computed separately from `temporary_impact()`: the power-law
  component is returned by `temporary_impact(v, v_hourly, sigma, eta)`, and
  spread is added independently in `compute_cost_variance()`.
- This structural separation produces identical total costs as the
  Almgren-Chriss formula `h = ε·sgn(v) + power_law` and avoids conceptual
  confusion about whether `temporary_impact` includes spread.
- Spread is reported separately in `impact_decomp.spread_bps` for transparency.

---

## 5. Execution shortfall variance

```text
Var[shortfall] = σ²_bin × Σ (x_i / X)²
```

where `σ_bin = σ_daily × sqrt(dt / 6.5)` and `x_i` is remaining inventory
(shares) at bin `i`, `X` is total order size.

- This is the **variance of execution shortfall** (drift of mark-to-market
  while the order is worked), not P&L variance.
- Expressed in bps² to match expected cost units.
- Does not include model uncertainty or impact uncertainty.

---

## 6. Almgren-Chriss linear schedule (`ac_linear`)

```text
x(t) = X · sinh(κ(T−t)) / sinh(κT)
κ² = λ·σ²_bin / η̃
```

- η̃ is the **linearised** temporary impact slope, evaluated at the actual TWAP participation rate of the specific order: `η̃ = η·σ_daily·0.6·p₀^{−0.4} / V_hourly` where `p₀ = (order_size / horizon_hours) / V_hourly`. Linearising at the order's own participation rate rather than a fixed 10% ADV point ensures the AC schedule is calibrated to the regime where it will actually operate.
- λ (lambda_risk) is the risk-aversion coefficient in units of bps per bps².
  The frontier is swept over `λ ∈ [10⁻⁹, 10⁻¹]` on a log-½-decade grid
  (17 points).
- At very low λ the schedule approaches VWAP (flat); at very high λ it
  front-loads aggressively.

---

## 7. Symbol parameters

η and γ are **global** literature values from Almgren et al. (2005), Table 3:

- η = 0.142 (temporary impact coefficient, β = 3/5)
- γ = 0.314 (permanent impact coefficient, α = 1)

These are not calibrated per-symbol. Real production TCA fits η and γ from
historical fills with stock-level fixed effects; this implementation deliberately
uses the published central estimates and exposes parameter uncertainty via the
regime sensitivity panel rather than per-symbol fits. See Limitations.

Per-symbol observable parameters:

| Symbol | ADV (shares) | σ_daily | Half-spread (bps) |
| ------ | ------------ | ------- | ----------------- |
| AAPL   | 60 000 000   | 1.55%   | 0.30              |
| MSFT   | 25 000 000   | 1.60%   | 0.35              |
| GOOGL  | 22 000 000   | 1.75%   | 0.40              |
| JPM    | 12 000 000   | 1.85%   | 0.50              |
| SPY    | 80 000 000   | 0.90%   | 0.15              |

Sanity-check expectation (not a calibration target): a 1% ADV order in a
large-cap name with these parameters should produce total cost in the
2–5 bps range, consistent with published broker TCA for similar orders.
Absolute levels are uncalibrated; relative comparisons across schedules
on the frontier are the model's primary output.

---

## 8. Known limitations / open questions

- [ ] η and γ are global literature values; no per-symbol or live calibration.
- [ ] ADV is constant (no intraday U-shape volume weighting).
- [ ] σ is constant; no vol-of-vol or regime switching.
- [ ] Permanent impact is linear; real markets show concave impact at large size.
- [ ] No market-hours boundary enforcement (horizon can exceed 6.5 h).
- [ ] Correlation between bins is ignored in the variance calculation.
- [ ] Schedule-invariance of permanent cost relies on linear g; switching to a
      non-linear permanent impact would break this property and require revisiting
      the cost decomposition.
