# CC Brief — FrontierView Agent, Phase 2 pre-merge cleanup

**Branch:** rides the existing `feature/agent-phase2` (pre-merge; same PR, after the review-fixes commit).
**Scope:** two small items surfaced by the fix-diff verification. No behavior change. Do **not** touch FV cost math or any other logic.

---

## C1 — Single-source the binning helper (`api/market_impact.py`, `api/main.py`, `agent/tools.py`)

**Problem.** The N2 fix introduced a _second_ copy of the binning formula: `agent/tools.py` now defines `_n_bins_for(horizon_hours)` whose docstring reads "Mirrors `api.main._n_bins_for`." Both compute `max(2, round(horizon_hours * 2))`. This is hand-maintained duplicated logic that affects cost numbers — exactly the copied-model-logic the project's DRY rule forbids — and "mirrors" means it will silently drift the moment one side changes, reintroducing the agent/web divergence N2 was meant to remove.

**Fix.** One definition, imported by both consumers.

1. In `api/market_impact.py`, add a public helper next to the other shared model functions:
   ```python
   def default_n_bins(horizon_hours: float) -> int:
       """Canonical bin count for a horizon; shared by the API and the agent."""
       return max(2, round(horizon_hours * 2))
   ```
2. In `api/main.py`: delete its private `_n_bins_for`, add `default_n_bins` to the existing `from api.market_impact import (...)`, and replace both call sites (`_n_bins_for(payload.horizon_hours)` → `default_n_bins(payload.horizon_hours)`).
3. In `agent/tools.py`: delete its private `_n_bins_for`, add `default_n_bins` to the existing `from api.market_impact import (...)`, and replace all five resolution sites (`... else _n_bins_for(inp.horizon_hours)` → `... else default_n_bins(inp.horizon_hours)`).

Agent importing an FV function is the sanctioned pattern (the locked "agent imports FV functions directly" decision); the agent re-implementing one is not.

**Acceptance.**

- `grep -rn "_n_bins_for" api/ agent/` returns nothing; `def default_n_bins` exists exactly once (in `api/market_impact.py`).
- Behavior is unchanged — the formula and call sites are identical, so all existing cost numbers are preserved.
- Full agent suite + `make test` (FV) green.
- Optional but nice: a one-line test that `default_n_bins(2.0) == 4` and that a `cost_and_variance` call with `n_bins` unspecified resolves to `default_n_bins(horizon)`, so the agent/web binding stays pinned. If the existing DRY equality test can be extended to assert single-sourcing of the binning helper, do that instead.

---

## C2 — Refresh the stale recovery-table docstring (`agent/loop.py`)

**Problem.** The recovery-policy table in the module docstring no longer matches the code after F1 and M2:

- The **max_tokens** row still says "trigger compaction, retry" — but the loop now raises the output budget to `_TRUNCATION_RAISED_BUDGET`, compacts only if `should_compact`, caps at `_MAX_TRUNCATION_RETRIES`, and returns a `[response truncated]` partial after the cap.
- The **numerical blow-up / order ≫ ADV** row still says "structured warning + `unreliable=True`" — but M2 removed `unreliable` from the dispatch catch-all, and that path is just a warn-and-proceed `summary["warning"]`.

**Fix.** Update the two rows to describe current behavior:

- max_tokens → "raise output budget and retry; compact only if over threshold; return partial + `[response truncated]` after the retry cap."
- numerical blow-up → "structured `warning` in summary (warn-and-proceed)" — drop the `unreliable=True` reference. (Genuine impl exceptions now return `ToolExecutionError` via the dispatch catch-all; mention that if you want the table complete.)

Comment-only change; no code.

**Acceptance.** The docstring table matches the implemented handlers. No test needed.

---

## Out of scope

No changes to FV cost arithmetic, the midpoint convention, `temp_exponent`, or any behavior. This is deduplication + a comment refresh only. After this, the branch is merge-ready.
