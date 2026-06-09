# CC Brief — Agent Phase 3: Eval Harness (Layer 1 + Layer 2)

**Branch:** `feature/agent-phase3` off `master`. Merge only when this brief's acceptance criteria, the agent suite, and the existing FV suite all pass.

**Scope of this brief:** the deterministic core of the eval harness — Layer 1 (answer-correctness against model-computed ground truth) and Layer 2 (trace-based behavioural checks). Layer 3 (LLM-as-judge reasoning quality) is a **separate follow-up brief** and is explicitly out of scope here.

See also: `cc_brief_agent_phase2_scaffolding.md` for the tracer, the two-part result shape (summary + out-of-band detail store), and the existing duplicate-call guard / retry budget this brief builds on.

---

## Why

Phase 3 is the differentiator: it exploits the deterministic FV model to get ground truth for free. The eval is a test suite for the **agent's judgment** — does it route to the right tool, pass the right parameters, report faithfully, and caveat synthetic results — **not** a test of the model's math, which the FV suite already owns.

---

## Locked design decisions (carry verbatim; do not relitigate)

1. **Ground truth via in-process FV calls.** Compute each expected answer by calling the *same* FV function the corresponding tool wraps — import it, don't reimplement, don't round-trip through the agent. DRY: add a contract test asserting the GT path and the tool path resolve to the same FV callables (same spirit as the Phase 1/2 DRY equality test).

2. **The agent is non-deterministic → the metric is a success rate over N=20 runs per question, not pass/fail.** Ground truth is exact; agent *reliability* is what's measured. Report per-question success rate (k/20) plus an aggregate.

3. **Temperature is read from the same config `llm.py` uses for prod — never hardcoded.** The eval must track prod automatically. N=20 is the only sampling knob set by hand.

4. **Eval mode = append, don't disturb.** A flag (off in prod) that makes the agent append a single structured block after its normal prose answer:

   ```
   <eval_answer>{"values": {"cost_bps": 12.3456, ...}, "synthetic": false}</eval_answer>
   ```

   It must NOT alter the reasoning path, tool selection, or the prose answer. Inject it as a minimal system-prompt addendum only when the harness sets the flag. Emit **full-precision floats** so tolerance scoring isn't fighting display rounding. Verify the flag-off path is identical to current prod (no addendum, no block).

5. **Synthetic-recovery is an estimator-behaviour check, not a market-truth oracle.** Whenever a recovery answer is scored, score the agent against the **recovered estimate** the routine returns, NOT the planted η/γ — scoring against the planted truth would penalise the agent for the estimator's own recovery error. Also require the synthetic caveat to be present in the answer; **caveat-presence is itself a scored assertion**. (See dependency note below for when this actually fires.)

---

## Dependency to confirm against the repo (affects the question set)

The Phase 2 tool set did **not** include a recovery/calibration tool, and the roadmap defers the recovery-demo tool (optional, lower priority than P3). So before wiring recovery questions, CC must check whether a recovery/calibration tool is currently exposed to the agent:

- **If no recovery tool is exposed (expected):** the synthetic-recovery routine is used by the harness **only as a ground-truth data generator**, and the agent-facing recovery case reduces to the **out-of-tool decline** scenario (e.g. "calibrate to my trades" → agent should decline or caveat, not fabricate). Decision #5's scoring rule is implemented but only the caveat/decline behaviour is exercised.
- **If a recovery tool *is* exposed:** add recovery-*answering* questions and score them per decision #5 (against recovered values, caveat mandatory).

Do not build the recovery-demo tool as part of this brief.

---

## Question set

Two sources, both under `agent/eval/questions/`, clearly separated:

- **Generated core.** Enumerate symbol × schedule (and the other obvious tool parameterisations); template natural-language questions; auto-compute GT via the in-process FV call. Each generated item records its canonical/minimal expected tool path (known from the template). Regenerable when the symbol set changes.
- **Curated set (~20).** Hand-written, ground-truth verified, annotated with expected tool path. Cover realistic phrasing, ambiguity, multi-part/compositional questions, and **out-of-tool requests** whose correct behaviour is decline-or-caveat rather than fabrication.

---

## Layer 1 — answer correctness

Per question:

1. Compute GT via the in-process FV function.
2. Run the agent N=20× at prod temperature (from config), eval mode on.
3. Parse the `eval_answer` block. Absent/unparseable → that run is a fail, and surface the rate of parse failures (a high rate means eval mode is misbehaving, not that the agent is wrong).
4. Score each value against GT with tolerance: default relative tolerance lenient enough to absorb sensible rounding (~3 significant figures), an absolute-tolerance floor for near-zero quantities, and **per-quantity overrides allowed** (variance vs bps may need different bands).
5. Emit per-question success rate (k/20) and an aggregate.

---

## Layer 2 — trace-based behavioural checks (deterministic; off the existing tracer)

Run on every agent invocation; assert:

- **Tool-path efficiency** — actual tool calls vs the question's known-minimal path; flag redundant/extra calls.
- **No duplicate calls** beyond what the existing duplicate-call guard / retry budget permits.
- **Synthetic caveat present** whenever a synthetic-recovery result was produced.
- **Numeric groundedness** — every number in the final answer must trace to a **model-visible** tool result (the summary half of the two-part result shape, NOT the out-of-band detail store) or to simple arithmetic over such numbers. This is the key hallucination check and must be fully deterministic.

---

## Provider / cost

The eval is the most call-heavy thing in the project. Iterate the harness on the **Flash free tier**; produce the **headline reliability numbers on Haiku** (the prod model). (Layer 3's judge, later, runs on Sonnet.) Make the under-test model a single config switch so Flash-dev → Haiku-confirm is a one-line change. Respect a configurable run budget and reuse the prompt caching already in `llm.py`.

---

## Structure

- Harness + scorers under `agent/eval/`.
- Question sets under `agent/eval/questions/` (generated + curated, clearly separated).
- Results: machine-readable JSON + a short human-readable summary (per-question rates, aggregate, Layer-2 failures, parse-failure rate).
- Reuse the existing tracer and two-part result shape. No model logic copied (DRY).

---

## Acceptance criteria

- [ ] Generated + curated question sets exist; GT auto-computed for the generated core; curated items GT-verified and tool-path-annotated.
- [ ] Harness runs the agent N=20× per question at prod temperature (read from config) with eval mode on; reports per-question success rate + aggregate.
- [ ] Tolerance scoring with per-quantity overrides; missing/garbled `eval_answer` counted as fail and surfaced as a parse-failure rate.
- [ ] Layer-2 assertions implemented and run per invocation: path efficiency, no excess duplicates, caveat presence, numeric groundedness (model-visible results only).
- [ ] Recovery dependency resolved per the dependency note; if scored, recovery answers use recovered values with a mandatory caveat-presence check.
- [ ] Eval-mode flag appends the structured block without changing reasoning/tool path; flag-off path verified identical to current prod.
- [ ] Temperature read from prod config, not hardcoded.
- [ ] DRY contract test: GT path and tool path resolve to the same FV callables.
- [ ] Existing FV suite + agent suite green on the branch.

---

## Out of scope (next brief)

- **Layer 3 — LLM-as-judge reasoning quality:** rubric for assumption-stating / explanation fidelity / out-of-tool handling, judge = Sonnet (stronger than the agent under test), judge validated against a small hand-labelled set.
