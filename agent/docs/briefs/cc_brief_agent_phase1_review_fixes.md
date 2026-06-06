# CC Brief — FrontierView Agent, Phase 1 Review Fixes

**Project:** FrontierView agent
**Phase:** 1 (remediation, post-review)
**Branch:** continue on `feat/agent-phase1` — these land *before* the Phase 1 merge to `main`.
**Source:** adversarial review of `agent/loop.py` and `agent/tools.py`.

## Constraints

- Same hard constraints as the spine brief: no model logic in `agent/`, hand-rolled loop, don't break the existing FV API, no secrets committed.
- Several items are **verify-first**: inspect the named file and only change code if the stated condition holds. Don't assume a fix is needed — confirm, act, and state in the PR which path you took.

## Merge gate (must fix before merging Phase 1)

### Fix 1 — Confirm schedule-format consistency across all four builders
The four builders are called with different signatures but all feed the same `compute_cost_variance(schedule, order_size, params, horizon_hours)`. If they don't all return the same schedule shape, cost/variance is silently wrong for the non-AC schedules.

- Inspect `api/market_impact.py`: confirm `schedule_twap`, `schedule_front_loaded`, `schedule_back_loaded`, and `schedule_ac_linear` all return the **same** structure (per `_optimal_schedule`, the AC builder yields a list of `(bin_index, participation_rate)` pairs), and that `compute_cost_variance` interprets that structure identically for all four.
- If they differ, fix so all four return the canonical shape — do **not** special-case inside `compute_cost_variance`.
- **Lock-in test** (`tests/test_tools.py`): for one representative input (e.g. `symbol="AAPL"`, `order_size=100000`, `horizon_hours=6.5`, `n_bins=13`), call `cost_and_variance` for each of the four `schedule_type` values; assert each returns **finite** `expected_cost_bps` and `variance_bps2`, and that the four results are **not all identical** (a silent format collapse would make them coincide or go NaN).

### Fix 2 — Stop treating truncation as a final answer
`loop.run` returns on any `stop_reason != "tool_use"`, so a `max_tokens` (truncated) response is returned as if complete.

- In `llm.py`: confirm `max_tokens` is generous (enough for reasoning plus a final answer; suggest ≥ 2048) and source it from `config.py`.
- In `loop.py`: handle `stop_reason == "max_tokens"` explicitly — trace a clear truncation notice and return a distinct message (e.g. `[TRUNCATED] ...`) so it never masquerades as a clean answer. Keep the existing empty-answer guard.

## Should fix (this brief)

### Fix 3 — Remove the AC near-duplicate (DRY)
`_optimal_schedule` duplicates the symbol lookup, `schedule_ac_linear(...)` call, and `compute_cost_variance(...)` call already present in the `ac_linear` branch of `_cost_and_variance`.

- Extract a private helper, e.g. `_run_ac(symbol, order_size, horizon_hours, lambda_risk, n_bins) -> (schedule, cost, variance)`, and call it from both sites. The existing DRY equality test must still pass.

### Fix 4 — Verify the agent isn't dragging in FastAPI (conditional core extraction)
`agent/` imports from `api/`, which is only clean if `api.market_impact` is import-light.

- Check what `import api.market_impact` transitively loads. If it pulls in FastAPI or app wiring, extract the pure model functions and `SYMBOL_PARAMS` into a new `core/` module that **both** `api/` and `agent/` import (API re-imports from core; zero duplication; `parameters.py` single-source unchanged).
- If `api.market_impact` is already pure, **make no change** and say so in the PR.

## Minor (low effort, include)

- **Fix 5 — Schema description.** On both tools, change the `n_bins` description from "Number of half-hour time bins. Default: 13." to "Number of time bins; each bin's length = horizon_hours / n_bins. Default: 13." (the half-hour claim only holds when `horizon_hours / n_bins = 0.5`).
- **Fix 6 — Friendly unknown-symbol error.** Guard `SYMBOL_PARAMS[symbol]` so an out-of-enum symbol returns `{"error": "Unknown symbol '<x>'. Allowed: AAPL, MSFT, GOOGL, JPM, SPY"}`, mirroring the unknown-tool message.
- **Fix 7 — Loop guard.** Restore the `response = None` initialisation before the loop in `loop.run`, or assert `MAX_ITERS >= 1` in `config.py`, so the tail `response.content` access can never hit an unbound name.

## Non-goals (defer — do NOT build here)

- Full schema validation of tool inputs beyond the friendly symbol error (Phase 2).
- Summarising/trimming `schedule_bins` in tool results to save tokens (Phase 2 / scale).
- Any new tools, endpoint, UI, streaming, or eval (later phases).

## Acceptance criteria

1. All four schedule types return distinct, finite cost/variance (Fix 1 test passes).
2. A truncated response is surfaced distinctly, never returned as a clean final answer (Fix 2).
3. `_run_ac` helper exists and is used by both AC call sites; DRY equality test still passes (Fix 3).
4. PR states whether the `core/` extraction was needed and what was done (Fix 4).
5. Minors 5–7 applied.
6. The full existing FV test suite and all `agent/tests` pass.
