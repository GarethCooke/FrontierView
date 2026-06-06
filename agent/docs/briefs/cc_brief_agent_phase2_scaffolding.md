# CC Brief — FrontierView Agent, Phase 2 (Full Scaffolding)

**Branch:** `feature/agent-phase2` (off current `main`, post Phase 1 merge)
**Package:** `agent/` inside the FrontierView repo
**Merge gate:** Phase 2 acceptance criteria (below) **and** the existing FV regression suite both green on the branch.

This phase is "everything hard lives in the scaffolding": the full tool set, context/transcript management, harder error handling, and real schema validation. The two spine tools and the hand-rolled loop already exist from Phase 1 — extend them, do not rewrite them.

---

## 0. Read before writing

1. `agent/docs/ROADMAP.md` — locked decisions and conventions. Do not relitigate them.
2. The Phase 1 code: the loop, the two existing tools (`cost_and_variance`, `optimal_schedule`), the CLI, the tracer, and `llm.py`. **Match the existing module layout and naming** — the structure below is descriptive, not prescriptive. Bind new tools to the actual Phase 1 wrappers / FV API you find; do not invent function names.
3. `api/parameters.py` and its guarding tests — this is the protected single source of truth for η, γ, the 0.6 temporary exponent, and the half-spread term. Nothing in this phase writes to it.
4. The existing DRY equality test that enforces "no model logic copied."

## 1. Non-negotiable constraints

- **DRY.** No model maths is reimplemented in `agent/`. Every compute tool composes existing FV operations (or the Phase 1 wrappers of them). The DRY equality test must stay green.
- **Read-only.** No tool mutates FV state or `parameters.py`. `sweep` perturbs parameters on a **per-call copy** only; global params are unchanged after any tool call. This is tested (§6).
- **Hand-rolled loop, no framework.** Extend the Phase 1 loop; do not introduce an agent framework.
- **In-process.** Tools import and call FV functions directly. No HTTP hop to the FV API.
- **Provider isolation.** Anything provider-specific stays in `llm.py`. Default model unchanged (Claude Haiku 4.5).

---

## 2. Workstream A — Full tool set

All tools are read-only. Each returns the **two-part result shape** defined in §3 (compact summary in-band + `detail_id` for out-of-band payload). Existing tools (`cost_and_variance`, `optimal_schedule`) must be migrated to that shape as part of this phase.

### Compute tools

**`compare_schedules(symbol, side, quantity, schedules)`**
Costs each schedule in `schedules` (a list of canonical names — TWAP / front-loaded / back-loaded / AC-linear — and/or explicit weight vectors) by composing the existing cost path. Returns each schedule's cost (bps), variance, and decomposition, plus ranked pairwise deltas vs the cheapest. This is the primary tool for "how much cheaper is optimal than TWAP." Do **not** make the agent compose this from repeated `cost_and_variance` calls — it is dedicated to cut loop iterations and transcript churn and to give Phase 3 a clean comparison object.

**`efficient_frontier(symbol, side, quantity, lambda_range)`**
Samples the cost/variance frontier across a grid of risk-aversion values spanning `lambda_range` (composing the optimal-schedule + cost path per λ). Summary returns the grid endpoints, the knee, and point count; full per-λ points go to the detail payload (they feed the Phase 4 charts).

**`sweep(symbol, side, quantity, schedule, param, range)`**
Sensitivity engine: re-runs the cost (and, where the param affects it, the optimal schedule) with one parameter varied over `range`, on a per-call copy of params. Drives "how much does the gap widen if temporary impact is +50%." `param` is namespaced by **class**, and the result annotates which class was swept so the agent can caveat correctly:

- `structural` — temporary-impact exponent (0.6), permanent-impact linear form. Sweeping these changes model identity; annotate accordingly so the agent flags it (e.g. permanent impact is linear by no-arbitrage; its sensitivity shouldn't be over-read).
- `calibrated` — η, γ.
- `market` — volatility σ, half-spread ε, ADV, price.

### Inspection / grounding tools (small, cheap, high-leverage)

**`list_symbols()`** — available symbols.
**`get_symbol_reference(symbol)`** — the stored reference values (ADV, σ, price, spread) for a symbol. Both must carry the "stored reference values, not a live feed" caveat in their output so the agent never implies real-time data.
**`describe_model()`** — current parameters and provenance (Almgren 2005 Table 3) so the agent explains assumptions from ground truth rather than memory.

### Out of scope for this phase

`calibrate(trades) → η, γ` is **deferred** (no fitting routine or trade data in FV; would add new model logic and break DRY). It is in the backlog; do not implement it here.

---

## 3. Workstream B — Context / transcript management

### Tool-result shaping (do this first; it benefits every call)

Every tool returns:

```
{ "summary": { ...headline numbers the model reads... },
  "detail_id": "<opaque id>" }
```

The full payloads — per-bin `schedule_bins` vectors, per-λ frontier points — are written to an **out-of-band detail store** keyed by `detail_id`, readable by the tracer and (later) the charts, and **never re-fed into the model transcript**. `summary` carries only what the model needs to reason: total cost (bps), variance, decomposition, a handful of bin stats, grid endpoints/knee, etc. Migrate the two Phase 1 tools onto this shape too.

### Thresholded compaction (build it light)

Instrument running transcript token count. When it crosses a configurable threshold, compact: keep the `[system + tool schemas]` prefix, the original user question, and the most recent N turns verbatim; replace older turns with a short **running-state** summary that **preserves established facts** (e.g. `TWAP=1.52bps; optimal@λ=2e-6=1.31bps`). The running state must record that a tool already ran so the model does not re-call it after its result was summarised away.

Do not gold-plate this — for bounded demo questions it may rarely fire. Prove it works with one deliberately long multi-tool eval question (§6).

### Stable prefix ordering (costs nothing now, enables Phase 4)

Order every request as `[system + tool schemas]` → `[compacted history]` → `[recent turns]`, keeping the prefix byte-stable across turns. **Do not** implement prompt caching now (Phase 4 backlog) — just don't fight the prefix structure.

---

## 4. Workstream C — Error handling & recovery

Two distinct surfaces — never conflate them.

**Tool-errors** go back into the transcript as structured results the model can recover from:

```
{ "error": "<class>", "field": "<name>", "detail": "<human-readable>",
  "got": <value>, "allowed": <range|set> }
```

Cap self-correction retries per tool-call site (~2) so the model can't thrash on the same bad call.

**Loop-errors** are handled by the harness and never shown to the model.

Deliver a **recovery-policy table** in code (or a clearly-commented dispatch) with a test per row:

| Error class                                                               | Surface            | Handler                      | Action                                     | Retry budget     |
| ------------------------------------------------------------------------- | ------------------ | ---------------------------- | ------------------------------------------ | ---------------- |
| Invalid tool arg (bad symbol, qty<0, λ≤0, weights≠1, unknown sweep param) | tool-error → model | dispatcher/validation        | structured error back to model             | ~2 self-corrects |
| Numerical blow-up (order ≫ ADV, degenerate schedule)                      | tool-error → model | tool layer maps FV exception | structured warning/error + unreliable flag | n/a              |
| Unknown tool / malformed tool-use JSON                                    | loop               | dispatcher                   | reject with valid-tool list back to model  | bounded          |
| Provider 429 / timeout / 5xx                                              | loop               | `llm.py`                     | exponential backoff retry                  | capped           |
| Over-length response (Fix 2 family)                                       | loop               | harness                      | trigger compaction, retry                  | capped           |
| Repeated identical tool call / runaway iterations                         | loop               | loop guard                   | nudge once, then graceful abort            | max-iter cap     |

No path may surface a raw stack trace to the user; terminal failures abort gracefully with a clear message and the partial trace.

---

## 5. Workstream D — Schema validation

- One **Pydantic model per tool input**. Generate the tool-use JSON schema advertised to the model **from** that Pydantic model — single definition, no hand-written second copy.
- Validate incoming model args against the same Pydantic model at dispatch. Validation failures become tool-errors in the §4 shape.
- **Semantic validation beyond types:** schedule weights sum to 1 ± tol; λ > 0; `symbol ∈ list_symbols()`; quantity sanity-checked against ADV. Be explicit per rule about **hard-fail vs warn-and-proceed** — prefer warn-and-proceed where the analysis is still meaningful (e.g. "order is 3× ADV, estimate unreliable") so the agent stays useful and inherits the caveat.

---

## 6. Tests (part of the merge gate)

- **DRY equality test** — still green (new compute tools compose FV; no maths copied).
- **Schema contract test** — for every tool, schema advertised to the model == schema validated at dispatch.
- **Read-only test** — sweep each param class, then assert global `parameters.py` / FV state is byte-identical afterwards; assert no tool writes FV state.
- **Tool unit tests** — each tool returns `summary` + valid `detail_id`; numbers match a direct FV call (deterministic ground truth).
- **Compaction test** — one long multi-tool question crosses the threshold; assert compaction fired, established facts preserved, and no already-run tool is re-called.
- **Recovery tests** — one per row of the §4 table.
- **FV regression suite** — green on `feature/agent-phase2`.

---

## 7. Suggested commit sequence

1. Pydantic input schemas + JSON-schema generation + contract test.
2. Two-part result shape + out-of-band detail store; migrate the two Phase 1 tools onto it.
3. New compute tools (`compare_schedules`, `efficient_frontier`, `sweep`) composing FV + unit tests + read-only test.
4. Inspection tools (`list_symbols`, `get_symbol_reference`, `describe_model`).
5. Error surfaces + recovery-policy table + per-row tests.
6. Thresholded compaction + stable prefix ordering + compaction test.
7. Full suite + FV regression green → open PR.

## 8. Explicitly out of scope (do not build)

`calibrate`; prompt caching (keep the prefix stable, nothing more); separate worker process; the `/agent` streaming endpoint, the FV "Ask the model" tab, the public surface, rate limiting and spend caps — all Phase 4 / backlog.
