# CC Brief — Agent Phase 3: Eval Harness (Layer 3 — Reasoning-Quality Judge)

**Branch:** continues on `feature/agent-phase3` (cut fresh from `master` once the Layer 1+2 brief has merged). Merge only when this brief's acceptance criteria, the agent suite, and the existing FV suite all pass.

**Scope:** the non-deterministic layer — an LLM-as-judge scoring the *quality of the agent's reasoning and explanation*, covering what Layer 1 (answer correctness) and Layer 2 (deterministic trace checks) structurally cannot. Builds directly on the harness, tracer, and question sets from `cc_brief_agent_phase3_eval_harness.md`.

---

## Why

Layer 1 says whether the number is right; Layer 2 says whether the trace is well-formed and the numbers are grounded. Neither can tell you whether the agent **reasoned well**: stated its assumptions on an ambiguous question, characterised the tool outputs faithfully in prose, declined out-of-tool requests substantively rather than by reflex, and avoided claims a buy-side quant interviewer would catch. That is this layer.

---

## Locked design decisions (carry verbatim; do not relitigate)

1. **Judge is a stronger model than the agent under test.** Default: Sonnet judging Haiku, configurable via the same single-switch pattern as Layer 1/2. Never judge with the model under test (no self-grading).

2. **The judge sees the full trace, not just the answer.** Judge input = the question, the complete trace (tool calls + model-visible tool results + final prose answer), and the known-correct behaviour / ground truth for that question. Fidelity can only be judged against what the tools actually returned. **Strip the `eval_answer` block before judging** — it is a scoring affordance, not part of the reasoning, and rewarding it would bias the judge.

3. **Anchored discrete rubric, not a vague 1–10.** Each dimension scored on a short scale (fail / partial / pass) with explicit anchor descriptions and at least one correct + one incorrect exemplar per dimension. The judge must cite the trace span that justifies each score.

4. **Do not trust the judge to know quant finance.** All domain-correctness checks are encoded as explicit anchored exemplars (below), not left to the judge's own knowledge. The judge checks against the supplied anchors; it does not free-associate.

5. **A judge is worthless until validated.** Ship a hand-labelled gold set and report judge-vs-human agreement per dimension. Below an agreement threshold, the rubric/anchors are the deliverable that needs work — the scores are not trustworthy yet.

---

## Rubric dimensions

Score each **independently** (avoid one halo score):

- **Assumption handling** — on an underspecified question, did the agent state its assumption or ask, rather than silently guess? (pass = explicit; partial = guessed but reasonable; fail = silent/wrong guess)
- **Explanation fidelity** — does the prose correctly characterise what the tools returned? Catches misattribution (e.g. calling a temporary-impact term permanent), over/under-claiming, and narrative that contradicts the numbers. Layer 2 checks the numbers *exist*; this checks the *story* about them.
- **Out-of-tool handling** — for requests the toolset cannot satisfy, did the agent decline/caveat substantively and explain why, rather than fabricate or reflexively refuse?
- **Confidence calibration** — appropriate hedging; no unsupported confident claims.
- **Domain correctness** — checked only against the anchored exemplars below.

---

## Domain-anchor checklist (encode as explicit exemplars; the agent should never trip these)

- Cites **"Almgren et al. (2005)"**, not "Almgren-Chriss 2001."
- Treats **permanent impact as linear (β=1)** per the 2005 no-arbitrage constraint — flag if the agent implies otherwise.
- Does **not** claim an AC closed-form schedule is optimal under the 0.6 power-law — those schedules sit above the true efficient frontier under nonlinear impact; calling them optimal is a reasoning error.
- Presents **synthetic recovery as recovery-from-planted-parameters, never as a fit to market data** — if the agent frames recovered η/γ as market-calibrated, that is a fidelity failure (ties to the Layer 1/2 synthetic-caveat rule).

---

## Structured judge output

Judge returns JSON only (no prose preamble), parsed by the harness:

```
{"scores": {"<dimension>": {"score": "pass|partial|fail", "evidence": "<trace span>"}, ...}}
```

Run the judge at low temperature for stability; it is still non-deterministic, so measure its self-consistency on the validation set (multiple judge runs there) and report it.

---

## Scope of runs / cost

The judge is a Sonnet call per trace, so it is the most expensive thing in the harness. Defaults:

- **Run Layer 3 primarily on the curated set** — the hard/ambiguous/out-of-tool questions are where reasoning quality is the whole point. The generated core is simple and adequately covered by Layer 1/2.
- Judge a **configurable sample of the N=20 agent traces per question** (default: a small *m*, not all 20), plus all curated traces if budget allows.
- Hard budget cap + reuse the prompt caching in `llm.py`. Extend the cost model in `agent/docs/` with judge-call rates.

---

## Judge validation (gold set)

- A hand-labelled gold set of ~20–40 (question, trace) pairs with per-dimension labels, under `agent/eval/judge/gold/`.
- Harness scores the judge against the gold set: per-dimension agreement (agreement rate / kappa).
- Report agreement in the summary; flag dimensions below threshold as "rubric needs work."
- Mitigate known judge biases: independent per-dimension scoring, mandatory evidence-citation, `eval_answer` stripped, and the judge **blind to Layer 1/2 pass/fail** so it does not anchor on whether the number happened to be right.

---

## Structure

- Judge + rubric + validation under `agent/eval/judge/` (alongside the Layer 1/2 harness in `agent/eval/`).
- Results: extend the existing JSON + human-readable summary with per-dimension reasoning scores, judge self-consistency, and judge-human agreement.

---

## Acceptance criteria

- [ ] Rubric implemented with anchored discrete scores + exemplars per dimension, including the domain-anchor checklist.
- [ ] Judge harness feeds question + full trace (`eval_answer` stripped) + known-correct behaviour to a configurable judge model (default Sonnet); parses JSON-only output with per-dimension score + evidence.
- [ ] Layer 3 runs on the curated set + a configurable sample of agent traces; hard budget cap respected; cost model extended.
- [ ] Gold set exists; judge-vs-human agreement reported per dimension; sub-threshold dimensions flagged.
- [ ] Judge self-consistency measured on the gold set and reported.
- [ ] Bias mitigations in place: independent dimension scoring, evidence-citation required, judge blind to Layer 1/2 outcomes.
- [ ] Summary integrates Layer 1 reliability + Layer 2 behavioural + Layer 3 reasoning scores.
- [ ] Existing FV + agent suites green on the branch.

---

## Out of scope

- Any change to the agent's prod behaviour. The judge is read-only over the traces the Layer 1/2 harness already produces.
