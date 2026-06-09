# CC Brief — Agent Phase 3: Groundedness Fix (completes S1a)

**Branch:** continue on `feature/agent-phase3`. Merge only after the new tests pass and the existing FV + agent suites stay green.

**Context:** The review-fixes pass (`cc_brief_agent_phase3_review_fixes.md`) landed the S1a groundedness rewrite logic but (a) added **no unit tests** for `check_numeric_groundedness` — the brief's S1 acceptance explicitly required them — and (b) shipped an **over-lenient `_within_tol`**. The marquee hallucination check is therefore both unverified by the green suite and probably too loose. This brief closes both.

Do not touch the parts already verified correct: the groundedness **data source** (model-visible summaries only, never the detail store) and the **question-literal pool seeding**. Those are right — keep them.

---

## Fix 1 — Tighten `_within_tol` (layer2.py)

The current pairwise candidate set is `{a−b, b−a, a+b, a/b, b/a, 100·a/b, 100·b/a}` for every pool pair. Over a pool that mixes bps (~1–100), coefficients (~0.1–0.6) and share counts (10⁵–10⁷), the **ratio and percentage** terms scatter ~150 candidates across the line for a typical pool, each with a 2% band, so a hallucinated mid-range number can match one by coincidence. The `100·a/b` term is also not the derived form answers actually state (percent-*change* is `100·(a−b)/a`, a 3-term expression this scheme never captured), so it adds leniency without capturing anything legitimate.

**Change:** keep **direct match** and the **pairwise sum/difference** candidates (`a−b`, `b−a`, `a+b`) — those cover the legitimate total/delta derivations. **Remove** the ratio (`a/b`, `b/a`) and percentage (`100·a/b`, `100·b/a`) candidates. Update the docstring to match.

**If a specific question's correct answer genuinely includes a ratio or percentage the tool does not return:** handle it per-question (e.g. include the derived value in that tool's summary, or add an expected-derived value for that question) rather than re-loosening `_within_tol` globally. Do not reintroduce blanket ratio/percentage grounding.

---

## Fix 2 — Add groundedness unit tests (new file `agent/tests/test_groundedness.py`)

Build small `capture` lists by hand (one `tool_result` with a `summary`, one `answer`) plus a `Question` with the relevant `text`, and assert on `check_numeric_groundedness(capture, question)["ungrounded_numbers"]` / `["passed"]`. Cover:

1. **Direct match grounded** — a prose number equal to a summary value → grounded, `passed=True`.
2. **Derived delta grounded** — a prose number equal to `summary_a − summary_b` (e.g. a cost delta) → grounded.
3. **Sum grounded** — a prose number equal to `summary_a + summary_b` → grounded.
4. **Comma parsing** — prose `"100,000"` is parsed as `100000.0` and grounded when present in the pool/question (regression test for S1b).
5. **Echoed constant grounded** — a question literal (order size, horizon) that appears in **no** tool summary is **not** flagged, because the pool is seeded from `question.text` (regression test for S1c).
6. **Anti-leniency (the load-bearing test)** — given a *realistic* mixed-magnitude pool (a summary with a couple of bps figures + a variance + a share count, and a question with an order size and horizon), a clearly-hallucinated number that is **not** a direct member and **not** a simple `±` combination of pool members must appear in `ungrounded_numbers` (`passed=False`). This is the test whose absence let the leniency regression through; it must fail against the old ratio/percentage logic and pass against the tightened version.

---

## Acceptance criteria

- [ ] `_within_tol` keeps direct + pairwise `±` only; ratio and percentage candidates removed; docstring updated.
- [ ] `test_groundedness.py` added with all six cases above; the anti-leniency case asserts a planted hallucinated number is flagged.
- [ ] Groundedness data source (model-visible summaries only) and question-literal seeding unchanged.
- [ ] Existing FV + agent suites green on the branch.

Send just this diff when done — `_within_tol` + the new test file — and I'll confirm the anti-leniency test actually discriminates before sign-off.
