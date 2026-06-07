# CC Brief — FrontierView Agent, Phase 2 review fixes

**Branch:** rides the existing `feature/agent-phase2` (pre-merge; same PR).
**Origin:** adversarial diff review of the full Phase 2 change set.
**Scope:** remediation only. All changes are in `agent/` plus `.gitignore`. **Do NOT modify `api/`** — the FV core was reviewed and is correct (see §0).

---

## 0. Do not touch (verified correct)

Leave these alone; "fixing" them would introduce regressions:

- **FV cost math.** `compute_cost_breakdown` is behavior-preserving — `total_bps` equals the old `expected_cost_bps`, and the permanent-cost **midpoint** convention (`cumulative_drift + own/2`) is intentional and schedule-invariant. Do not change the perm convention, the `temp_exponent=0.6` default, or any `api/` arithmetic.
- The DRY consolidation in `api/main.py` (`_decompose_impact` → `compute_cost_breakdown`, the static-asset loop, `_n_bins_for`) is correct.
- Read-only guarantee on `sweep`, the schema contract test, and the recovery-table wiring all hold.

---

## Fix before merge

### F1 — `max_tokens` recovery addresses the wrong dimension (`agent/loop.py`)

**Problem.** On `response.stop_reason == "max_tokens"` the loop compacts the _input_ and `continue`s. But `max_tokens` means the _output_ hit the `MAX_TOKENS` cap; shrinking the input gives the model no extra output room. When truncation is caused by a long generation (the usual cause) the transcript is often below `COMPACTION_THRESHOLD_TOKENS`, so `compact_messages` is a no-op, the identical call is re-issued, and the loop spins to `MAX_ITERS` and returns the generic "Stopped after N iterations." It terminates but does not recover. The row-5 test passes only because it mocks a clean answer on retry.

**Fix.** Address output length and bound the retries:

1. Thread an optional `max_tokens` override into `llm.call(...)`.
2. On `max_tokens`, retry with a raised budget (e.g. `min(MAX_TOKENS * 2, 8192)`), tracked by a dedicated counter, capped at 1–2 truncation-retries.
3. Only call `compact_messages` here if `should_compact(messages)` is genuinely true (input bloat is a real but secondary contributor).
4. If it still truncates after the cap, return the best partial text with an explicit `[response truncated]` note — never silently spin to `MAX_ITERS`.

Do **not** append the raw truncated assistant turn (it may contain an incomplete `tool_use` block → API 400), and do not append a second consecutive user turn.

**Acceptance.** A test where the mock returns `max_tokens` on _every_ call must terminate with a `[response truncated]`-style answer (not the generic MAX_ITERS message), and the retry must use a raised budget. Keep the existing happy-path row-5 test.

### F2 — custom weight vectors costed over the wrong horizon (`agent/tools.py`)

**Problem.** `_weights_to_schedule` computes `dt = horizon_hours / n_bins` using the passed `n_bins` (default 13), but returns a schedule of `len(weights)` bins. `CompareSchedulesInput` validates that weights sum to 1 but never checks `len(weights) == n_bins`. When they differ, participation rates are scaled by `horizon/13` while `compute_cost_breakdown` re-derives `dt = horizon/len(schedule)` — a double inconsistency that silently produces a wrong cost for an advertised capability. Silent wrong-but-plausible numbers are the worst failure mode for a cost tool.

**Fix.** In `_weights_to_schedule`, derive the bin width from the vector itself: `n = len(weights); dt = horizon_hours / n`. The weight vector defines its own bin count; `n_bins` should not enter the custom-vector path.

**Acceptance.** Add a test: a uniform custom weight vector of length `k` must produce the same cost as canonical `twap` with `n_bins=k` (uniform weights ≡ TWAP). Use `k ≠ 13` (e.g. 5) so it exercises the bug — pre-fix this diverges, post-fix it matches within `1e-6`.

---

## Fast follow (moderate)

### M1 — compaction drops results and caveats for half the tools (`agent/compaction.py`)

**Problem.** `_format_fact` only recognises `expected_cost_bps` / `cheapest_cost_bps` / `variance_bps2` / `warning`. A compacted `sweep` fact keeps the tool+args but loses `cost_delta_bps` **and** `structural_caveat`; `efficient_frontier` loses its knee/endpoints. Since `seen_calls` then blocks re-calling, those values become irretrievable, and the verified structural caveat does not survive compaction. The docstring's "preserves every established tool-call fact" is overstated.

**Fix.** Replace the cherry-pick with a compact dump of the whole `summary` (it already excludes the heavy bin/frontier arrays — those live behind `detail_id`). Preserve any `*_caveat` and `warning` fields verbatim.

**Acceptance.** Extend the hardened compaction loop test: after compacting a transcript containing a structural `sweep`, the running-state summary text must contain both `cost_delta_bps` and the `structural_caveat` substring.

### M2 — blanket `except Exception` mislabels impl bugs as `unreliable` (`agent/tools.py::dispatch`)

**Problem.** The impl-call `except Exception` returns `{"error": ..., "unreliable": True}` for _any_ exception, so a genuine code bug is fed to the model as a soft "unreliable result" rather than surfaced. The legitimate domain case (order ≫ ADV) is already handled by `_adv_warning` warn-and-proceed, so the catch-all shouldn't imply a shaky number.

**Fix.** Drop the `unreliable: True` tag from the generic handler and label it `"error": "ToolExecutionError"` with the detail. Reserve `unreliable` for the explicit warn-and-proceed path. Keep the structured shape; never leak a traceback.

**Acceptance.** A forced impl exception returns `{"error": "ToolExecutionError", "detail": ...}` with no `unreliable` key and no `"Traceback"`/`"File "` substring.

### M3 — `detail_store` is unbounded and not thread-safe (`agent/detail_store.py`)

**Problem.** Module-global dict that only grows; `clear()` is test-only. Harmless for the CLI, but a memory leak and concurrency hazard once it backs the Phase 4 server endpoint.

**Fix (cheap insurance now).** Cap the store with FIFO eviction (e.g. keep the most recent 256 payloads via an `OrderedDict`). Leave a `# TODO: per-request scoping + locking when the /agent endpoint lands` note for Phase 4.

**Acceptance.** Inserting more than the cap keeps the store bounded; the most-recent payloads remain retrievable.

---

## Nits (do while in here; lower priority)

- **N1 — tighten recovery test assertions (`agent/tests/test_recovery.py`).** Rows 1, 3, and the second row-6 test end in `... or isinstance(answer, str)` / `assert answer is not None`, which is always true. Assert the named behavior (e.g. duplicate detection / structured error reaching the transcript), the same way the compaction test was hardened. Rows 4 and 5 are already properly asserted — leave them.
- **N2 — agent/web bin-count divergence.** Agent tools default `n_bins=13`; FV `/analyse` uses `_n_bins_for(horizon)`. Same order/horizon → different cost numbers between the agent and the web charts (a Phase 4 credibility risk). Default the agent tools to `_n_bins_for(horizon_hours)` so binning has one source of truth. (Import the helper from `api.main` or lift it to a shared location — read-only use, no FV behavior change.)
- **N3 — schema enum asymmetry (`agent/tools.py`).** `symbol` gets an enum injected into the advertised schema, but `sweep.param` and compare's canonical schedule names are validator-only. Inject enums for `param` and the schedule-name fields too, for parity and more reliable tool calls.
- **N4 — `.DS_Store` is committed.** Add to `.gitignore` and `git rm --cached` it from the branch.
- **N5 — cosmetics.** The calibrated-class caveat is stored under the key `structural_caveat` (rename to a class-neutral `caveat`); `get_symbol_reference`'s description promises "price" but the impl returns none (drop "price" or add it); `dispatch` sets `got=None` for nested-field validation errors (optional).

(**N6 — prompt caching** shipped early in `llm.py`; placement is correct. This is a roadmap-accuracy note, not a code change — handle in the roadmap, not here.)

---

## Global acceptance

- New/updated tests for F1, F2, and M1 as specified above.
- Full agent suite + FV regression suite green on `feature/agent-phase2`.
- DRY equality + schema contract tests still green.
- No changes under `api/`; no change to FV cost arithmetic or the midpoint convention.
