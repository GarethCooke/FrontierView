# FrontierView — Pre-Trade Market Impact Visualiser

## What problem does this solve?

Every time an institutional investor wants to buy or sell a large position, they face a fundamental dilemma: trade quickly and pay more, or trade slowly and bear more risk. This is not a trivial problem. A fund managing $5 billion that routinely executes poorly can lose tens of millions of dollars a year to execution costs that never appear on a P&L line — they show up as slippage, as underperformance relative to benchmark, as the gap between the price you decided to trade at and the price you actually got.

FrontierView makes this trade-off visible. Given an order — a stock, a size, and a time horizon — it computes the expected cost and risk of execution across a range of strategies and shows you the efficient frontier: the set of strategies from which no improvement is possible without accepting more risk or paying more cost.

This is the core question of pre-trade transaction cost analysis (TCA), and it is answered every day by execution desks at hedge funds, asset managers, and investment banks using models exactly like this one.

---

## Background: why execution costs matter

When a large investor buys shares, they move the market against themselves. Buying pressure pushes prices up; the more aggressively you buy, the more you pay above the price you saw when you made the decision to trade. This is **market impact** — and unlike commissions or spreads, it scales non-linearly with order size and speed.

Market impact has two distinct components:

**Temporary impact** is the instantaneous price dislocation caused by your order hitting the book. It reflects the liquidity you consume in the moment. It decays after you stop trading — the market recovers. If you trade faster, you consume more liquidity per unit time and pay a higher temporary impact cost.

**Permanent impact** is the information content of your order. If you are buying, the market infers (correctly or not) that you have information suggesting the stock is cheap. Other participants reprice accordingly. This impact does not recover — it is the lasting signal your order sends to the market. It is proportional to total order size, not speed.

The tension between these two defines the execution problem: trading fast reduces exposure to permanent impact (you finish before the market fully adjusts) but increases temporary impact. Trading slowly reduces temporary impact but gives the market more time to move against you. There is an optimal schedule that minimises the total cost for a given level of risk tolerance — and that optimum is different for every investor depending on how much uncertainty they can accept.

---

## The model

FrontierView implements the market impact model from:

> Almgren, R., Thum, C., Hauptmann, E., & Li, H. (2005). *Direct estimation of equity market impact.* Risk, 18(7), 57–62.

This is an empirical extension of the foundational Almgren-Chriss (2001) framework, fitted to US equity trade data. It is the reference model taught in quantitative finance courses, cited in academic literature, and used as a baseline by TCA vendors.

**Important:** this is not the 2001 paper. Almgren-Chriss (2001) assumes linear temporary impact. The 2005 paper establishes empirically that temporary impact follows a power law with exponent approximately 0.6 — a meaningful difference in how costs scale with participation rate.

### Functional forms

**Temporary impact** (cost of trading at rate v, in bps):

```
h(v) = η · σ · (v / (6.5 · V))^0.6
```

**Spread cost** (paid once per share regardless of speed):

```
s = ε · sgn(v)
```

**Permanent impact** (lasting price change from trading rate v):

```
g(v) = γ · σ · (v / V)
```

Where:
- `v` — trading rate (shares per hour); this is a rate, not a cumulative quantity
- `V` — average daily volume (shares per day)
- `σ` — daily return volatility (dimensionless, e.g. 0.0155 = 1.55%)
- `η = 0.142` — temporary impact coefficient (Almgren 2005, Table 3)
- `γ = 0.314` — permanent impact coefficient (Almgren 2005, Table 3)
- `ε` — half bid-ask spread (per-symbol default)
- `6.5` — trading hours per day (US equities convention)

The result is a dimensionless fractional price change. Multiply by 10,000 for basis points.

### Execution schedules

Rather than solving a full numerical optimisation (which requires assumptions that cannot be validated without real order flow data), FrontierView compares four canonical execution strategies:

| Schedule | Description | Character |
|---|---|---|
| TWAP | Equal participation rate throughout | Neutral baseline |
| Front-loaded | Exponentially decreasing rate | Aggressive; accepts higher temporary cost to reduce market exposure time |
| Back-loaded | Exponentially increasing rate | Passive; accepts higher uncertainty to minimise initial impact |
| AC Linear | Almgren-Chriss closed-form optimal (linearised model) | Theoretically motivated; typically front-loaded |

Each schedule is evaluated for expected cost and variance of execution shortfall.

### The efficient frontier

For each schedule, plotting expected cost (bps) against variance of cost (bps²) produces a point in cost-risk space. Together, these points trace an approximation of the **efficient frontier** — the boundary below which no strategy can achieve lower cost without accepting higher variance, or lower variance without accepting higher cost.

The frontier is indexed by a risk aversion parameter **λ**, which represents how much additional expected cost the trader is willing to pay to reduce variance by one unit. A λ of zero means the trader is indifferent to risk (minimise cost only); a high λ means the trader strongly prefers certainty over cheapness.

This is the central result of the Almgren-Chriss framework and the primary visualisation in FrontierView. It directly answers the question: *given how risk-averse I am, what is the best way to execute this order?*

---

## How FrontierView works

1. **User inputs** an order specification: stock symbol, order size (shares), direction (buy/sell), and execution time horizon (hours).

2. **The API** (FastAPI, Python) looks up per-symbol market parameters (ADV, volatility, half-spread), computes temporary and permanent impact for each canonical schedule, and returns the frontier, schedule time series, and impact decomposition.

3. **Three visualisations** are rendered:

   - **Efficient frontier chart** — scatter plot of expected cost vs. variance for each schedule, with a λ slider that highlights the risk-aversion-optimal choice
   - **Execution schedule** — participation rate over time bins, showing how aggressively the order is worked throughout the horizon
   - **Impact decomposition** — bar chart breaking total cost into spread, temporary impact, and permanent impact components

4. **Model parameters** are displayed explicitly — symbol defaults, η, γ, σ, ADV — so the user can see exactly what assumptions are driving the output.

---

## Limitations

These are not caveats buried in small print. They are explicit, intentional constraints on an MVP model, and understanding them is part of what the tool is designed to demonstrate.

**Parameters are not calibrated.** η and γ are literature central estimates from Almgren 2005 with material standard errors. Real TCA vendors calibrate these from their own execution data — millions of historical fills matched against contemporaneous market conditions. Without that data, the numbers are indicative, not predictive.

**Volatility and ADV are static defaults.** Real impact models condition on realised intraday volatility, current order book depth, and time-of-day volume profiles. A 2pm AAPL trade looks very different from an 8am open or a 3:50pm close. FrontierView uses daily averages.

**Equities only.** The model assumes a centralised, lit exchange with a measurable average daily volume. FX spot is OTC, fragmented across venues, with no canonical ADV. Applying this model to FX would require a fundamentally different microstructure framework (e.g., Butz & Oomen, 2019).

**No intraday volume profile.** Real VWAP execution weights participation by expected intraday volume — higher at open and close, lower mid-day. FrontierView uses flat time bins.

**The optimizer uses canonical schedules, not a full numerical optimiser.** The true Almgren-Chriss optimum with the 0.6 exponent has no closed-form solution (unlike the linear case). Solving it numerically introduces convergence complexity not warranted for a demonstration tool. The canonical schedule menu is an honest alternative: it shows you where real strategies sit on the frontier without pretending to have solved a problem that requires calibrated data to solve meaningfully.

**Variance is variance of execution shortfall, not P&L variance.** The y-axis of the frontier measures uncertainty in execution cost relative to arrival price. It is not total portfolio P&L variance, which would include alpha decay, factor exposures, and other components entirely outside this model's scope.

---

## What this demonstrates

FrontierView is a portfolio project. Its purpose is to demonstrate:

- Fluency with quantitative execution models at the level expected of a buy-side technology role
- Understanding of the distinction between temporary and permanent impact, and why it matters for schedule design
- Ability to implement a non-trivial Python model cleanly, expose it via an API, and connect it to an interactive visualisation
- Intellectual honesty about model limitations — which, in a real TCA context, is exactly what separates credible analysis from dangerous overconfidence

The natural extensions — real-data calibration, intraday volume profiles, multi-asset support, backtesting against historical fills — are the subject of the synthetic calibration tab, which demonstrates the estimation methodology even without production data.

---

## References

Almgren, R., & Chriss, N. (2001). Optimal execution of portfolio transactions. *Journal of Risk*, 3(2), 5–39.

Almgren, R., Thum, C., Hauptmann, E., & Li, H. (2005). Direct estimation of equity market impact. *Risk*, 18(7), 57–62.

Butz, M., & Oomen, R. (2019). Internalisation by electronic FX spot dealers. *Quantitative Finance*, 19(1), 35–56.

Gatheral, J. (2010). No-dynamic-arbitrage and market impact. *Quantitative Finance*, 10(7), 749–759.

Huberman, G., & Stanzl, W. (2004). Price manipulation and quasi-arbitrage. *Econometrica*, 72(4), 1247–1275.

Kyle, A. S. (1985). Continuous auctions and insider trading. *Econometrica*, 53(6), 1315–1335.
