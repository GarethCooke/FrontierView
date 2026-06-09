# CC Brief — Agent Phase 3: Adversarial-Review Remediation

**Branch:** continue on `feature/agent-phase3` (these are pre-merge fixes). Do not merge until every fix below is applied, all new tests pass, and the existing FV + agent suites stay green.

**Context:** Opus adversarial diff review of the Phase 3 eval harness (Layer 1 + Layer 2 + Layer 3 judge) found the items below. Layer 3 (judge, rubric, validation, gold set) passed review and needs only minor touch-ups (N5, N6). The temperature single-source, eval-mode flag-off, groundedness data-source, and DRY contract were verified correct — do not regress them.

Severity order: **B** = blocker (correctness), **S** = significant (distorts the metric), **M** = moderate (DRY/robustness), **N** = minor. Fix all.

---

## B1 — `optimal_schedule` ground truth assumes it equals `ac_linear`

Every `optimal_schedule` question derives GT from `_gt_cost(..., "ac_linear")`: `gen_optimal_*` (generated.py), and `cur_optimal_msft_lambda`, `cur_multi_optimal_and_frontier`, `cur_variance_spy_optimal` (curated.py). This is an **unverified assumption**, and the project's own locked stance — the AC closed-form sits *above* the true efficient frontier under the 0.6 power-law — suggests `optimal_schedule` may not return the `ac_linear` cost at all.

**Fix:**
1. Inspect what `optimal_schedule` actually computes and what its summary reports as the cost/variance basis.
2. If it returns the AC closed-form cost → the current GT is correct; lock it in with the DRY test below.
3. If it returns anything else (a numerically-optimised point, a different schedule) → re-derive the GT for those four question groups by calling whatever in-process function `optimal_schedule` wraps, not `schedule_ac_linear`.
4. **Add a DRY equality test** (`test_eval_dry.py`): `optimal_schedule` GT == `dispatch("optimal_schedule", ...)` summary, parametrised over the symbols/λ used, so this can never silently drift again.

## B2 — `cur_compare_var_msft` (#14) GT is semantically wrong

The question asks for the **variance** of front- vs back-loaded; the GT is `cheapest_cost_bps` (a cost).

**Fix:** inspect `compare_schedules`' summary shape. If it exposes per-schedule variance, set GT to the correct variance field for the question's intent. If it does not, rephrase the question to a cost comparison consistent with the cost GT. Do not leave a variance question scored against a cost.

---

## S1 — Numeric groundedness throws false positives (three causes)

In `layer2.check_numeric_groundedness` / `_extract_numbers`:

- **S1a — no arithmetic allowance.** The brief requires a prose number to count as grounded if it traces to a tool result **or simple arithmetic over tool results**. Currently only direct pool membership is accepted, so legitimate derived figures (deltas, percentages, ratios) are flagged. Extend `_within_tol` (or a new check) to also accept a number within tolerance of a **bounded** combination of pool numbers: pairwise differences, sums, ratios, and percentage-of (`100*a/b`). Keep it pairwise only — do not allow arbitrary n-ary combinations (that would make the check vacuous).
- **S1b — comma fragmentation.** `_extract_numbers` splits `"100,000"` into `100` and `000`. Strip thousands separators in grouped numbers before parsing (regex for `\d{1,3}(?:,\d{3})+(?:\.\d+)?` → remove commas), so `"100,000"` → `100000.0`.
- **S1c — echoed-constant filter is wrong.** The comment claims `abs(n) > 0.1` ignores question-echoed integers like "2 hours"/"5 symbols"; it does not (2 and 5 pass the filter and get flagged). Fix by **seeding the grounded pool with the question's own numeric literals** (order size, horizon, λ, σ/η range endpoints) — those are legitimately grounded in the prompt — and correct the comment to match actual behaviour.

## S2 — `check_tool_path` fails legitimate alternative routes

`gen_cost_ac_*` declares `expected_tool_path=["cost_and_variance"]` but its own note says `optimal_schedule` is an acceptable route; the check fails any "missing" expected tool, so the valid alternative is a spurious fail.

**Fix:** add an optional `acceptable_tool_paths: list[list[str]]` (or `alternative_tools: set[str]`) field to `Question` (types.py). `check_tool_path` passes if the actual calls satisfy `expected_tool_path` **or** any declared alternative. Populate it for `gen_cost_ac_*` (`cost_and_variance` OR `optimal_schedule`) and any other question with a legitimate second route.

## S3 — Unspecified-schedule questions pin GT to TWAP → false fails at temp 1.0

`cur_cost_aapl_natural` (#1), `cur_cost_aapl_30min` (#9), and the generated TWAP items score the numeric answer against the TWAP GT while telling the agent any schedule is fine. At temperature 1.0 the agent will vary its schedule choice across the 20 runs, so the success rate measures schedule-choice variance, not correctness.

**Fix (default):** split the intent.
- For questions whose purpose is **numeric correctness**, specify the schedule in the text (e.g. "…using a TWAP schedule"), so the single-schedule GT is valid.
- Keep genuinely unspecified-schedule phrasing only on questions intended as **assumption-handling probes**, flagged with empty `gt_values` so Layer 1 skips them and they are scored by the Layer 3 `assumption_handling` dimension instead.

---

## M1 — GT-helper duplication across `generated.py` and `curated.py`

`_gt`/`_with_sigma`/`_with_eta` in curated.py are copy-pasted from generated.py's `_gt_cost`/`_gt_sweep_sigma`. Leaf FV calls are shared (hard constraint holds) but the orchestration is duplicated.

**Fix:** extract one shared module `agent/eval/questions/gt.py` exposing `gt_cost(symbol, order_size, horizon, schedule_type, lambda_risk=…)` and `gt_with_param_override(symbol, order_size, horizon, *, sigma=None, eta=None, gamma=None, schedule_type="twap")`. Both question modules import from it. Delete the per-file copies.

## M2 — Sweep and compare GTs are unguarded by any DRY test

The contract test only covers `cost_and_variance`. The sweep param-override GT and the `min(...)` compare GT can silently drift from the `sweep` / `compare_schedules` tools, and `test_generated_questions_gt_consistent_with_dispatch` skips them (no `expected_cost_bps`).

**Fix:** after M1, add equality tests in `test_eval_dry.py`: shared sweep GT (start/end/delta) == `dispatch("sweep", …)` summary for the same override; shared compare `cheapest` == `dispatch("compare_schedules", …)` `cheapest_cost_bps`.

## M3 — Vacuous-pass hole in `test_generated_questions_gt_consistent_with_dispatch`

It `continue`s past unparseable ids with no assertion that anything was checked, so an id-convention change makes it verify nothing and stay green.

**Fix:** count checked questions; `assert checked > 0`, and ideally `assert checked == <number of generated questions carrying an expected_cost_bps GT>` so it can't silently skip a subset.

## M4 — No per-run exception isolation in the harness

`harness._run_once` does not wrap `loop.run`, so one persistent failure (API error after retries, etc.) aborts the whole ~260-run eval and loses all results.

**Fix:** wrap `loop.run` in try/except inside `_run_once`; on exception record a `RunResult` with `layer1_passed=False` and a new `error: str | None` field, then continue. Surface an exception/run-failure rate in `_build_results_dict` and the printed summary.

---

## N1 — Synthetic-caveat path is unexercised

No question sets `synthetic=True`, so `check_synthetic_caveat` and the decision-#5 scoring are dead code. This is consistent with no recovery tool being exposed (calibration is covered as `out_of_tool` #10) — leave the machinery in place. **Fix:** add a unit test that feeds a hand-built capture (with a synthetic result + a question flagged `synthetic=True`) through the caveat check, so the path is at least unit-covered; and leave a `# TODO` noting synthetic=True eval questions are required if a recovery-demo tool is added.

## N2 — Generalise and tighten the caveat check (folds in N4)

`check_synthetic_caveat`'s keyword list includes `"approximate"` (too broad, false-pass), and `cur_large_order_jpm` (#15) requires a `>ADV` reliability caveat that nothing currently enforces.

**Fix:** rename to `check_caveat_presence`, driven by a `Question.requires_caveat: bool` flag (synthetic implies it). Maintain a tight, caveat-specific keyword/phrase set (drop `"approximate"`); for the `>ADV` case include the reliability-warning vocabulary. Set `requires_caveat=True` (or `synthetic=True`) on #15.

## N3 — `tool_path` extras are observed but not gated

Intentional, but make it explicit: keep extras non-failing, and **report an extra-call rate** in the summary as a soft efficiency signal rather than implying `tool_path` enforces efficiency.

## N5 — Judge agreement is raw exact-match only

The brief mentioned kappa; partial-vs-pass near-misses currently count as full disagreement and chance agreement isn't corrected.

**Fix:** in `validation._per_dimension_agreement`, add Cohen's κ per dimension alongside the exact-match rate (implement directly, no new deps); report both. Keep the existing threshold flag on exact-match (or switch to κ — your call; report both regardless).

## N6 — `domain_correctness` has no "no domain claim" default

For purely numeric answers that touch none of the four anchors, the judge's domain score is undefined-by-rubric → noise. Unlike `assumption_handling` / `out_of_tool_handling`, there's no default-pass instruction.

**Fix:** add to the judge prompt's per-dimension defaults: "If the answer makes no claim touching any of the four domain anchors, score `domain_correctness` as `pass`." Add a numeric-only-answer → pass exemplar to the rubric.

## N7 — Minor consistency

- `judge.run_layer3` signature uses literal defaults (`sample_m=3`, `budget_calls=100`) that duplicate `JUDGE_SAMPLE_M` / `JUDGE_BUDGET_CALLS`. Reference the config constants (or a `None` sentinel resolving to them) so they can't drift. (The `run.py` CLI already wires config — this is just the direct-call path.)
- Strengthen `test_eval_mode_off_system_prompt_unchanged` to assert `captured_prompts[0] == _SYSTEM_PROMPT` (byte-identity) rather than substring-absence.

---

## Acceptance criteria

- [ ] B1 resolved: `optimal_schedule` GT verified against the tool; GT re-derived if it differs; DRY equality test added and passing.
- [ ] B2 resolved: #14 question/GT made semantically consistent.
- [ ] S1a/b/c: groundedness accepts pairwise arithmetic over the pool, parses comma-grouped numbers, seeds the pool with the question's literals; comment corrected. New unit tests cover each (derived-delta grounded; "100,000" parsed; echoed horizon not flagged).
- [ ] S2: `Question` carries acceptable alternatives; `check_tool_path` honours them; `gen_cost_ac_*` no longer fails on the `optimal_schedule` route. Test added.
- [ ] S3: numeric-correctness questions specify their schedule; unspecified-schedule items moved to empty-`gt_values` assumption probes.
- [ ] M1: single `gt.py` helper; per-file duplicates deleted.
- [ ] M2: sweep + compare DRY equality tests added and passing.
- [ ] M3: consistency test asserts `checked > 0` (and the expected count).
- [ ] M4: per-run exception isolation; `error` field + failure-rate in the summary.
- [ ] N1: synthetic caveat path unit-covered; TODO left.
- [ ] N2: caveat check generalised + tightened; #15 flagged.
- [ ] N3: extra-call rate reported; efficiency framed as observed-only.
- [ ] N5: Cohen's κ reported per dimension.
- [ ] N6: domain_correctness no-claim default added to prompt + exemplar.
- [ ] N7: config constants referenced in `run_layer3`; flag-off test asserts byte-identity.
- [ ] Test assertions match their names; no vacuous passes (esp. M3).
- [ ] Existing FV + agent suites green on the branch.

## Do not regress (verified correct in review)

- Temperature single-sourced through `config.TEMPERATURE` in the one `llm.call` path; no per-call override.
- Eval-mode flag-off = prod system prompt by construction.
- Groundedness pool drawn from model-visible summaries only, never the detail store.
- Judge: Sonnet, eval_answer stripped, structurally blind to L1/L2, anchored rubric incl. the four domain traps, real gold set + agreement + self-consistency.
