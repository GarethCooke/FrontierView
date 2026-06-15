# FrontierView Agent — Roadmap & Tracker

Single source of truth for the agent project. Update statuses as you go. Seed each new working session with this file.

---

## Locked decisions (don't relitigate)

- **Tools call the model in-process** — `agent/` imports FV's functions directly; no model logic copied. DRY enforced by an equality test.
- **Hand-rolled loop**, no agent framework.
- **Hosted brain, native tool use.** Default: Anthropic Claude Haiku 4.5, isolated in `llm.py` so a provider swap is a one-file change. Confirmed for the public demo too (consistency with the eval baseline); Flash stays the documented fallback only if cost bites.
- **Lives in the FrontierView repo** as the `agent/` package — not a separate repo. Extract a shared `core/` only if a real second consumer of the model appears.
- **UI = an integrated tab on FrontierView** (not a separate microsite). Code lives within FV; the project can still get its own billing in the portfolio narrative.
- **Deployment stays on Render**, same service. No model self-hosted. The `/agent` endpoint runs server-side and is **streamed** (timeout safety + the watch-it-think demo).
- **Stream transport = SSE**, one-directional (question in once, trace out). Consumed by the 4c UI via `fetch` + `ReadableStream`, not native `EventSource` (which is GET-only; the endpoint is POST). Frame shape: `event: <type>\ndata: <json>\n\n`, with `type` also inside `data`.
- **Loop emits via an optional event-sink** (`event_sink=None` ⇒ byte-identical to prod; CLI and eval harness pass no sink). The streaming path uses a queue-bridging sink (`call_soon_threadsafe`) from the blocking worker thread to the async generator.
- **Uniform stream terminal:** every stream ends with `run_finished` — normal: `… → final_answer → run_finished(turns=N)`; error: `… → error(kind="loop") → run_finished(turns=None)`. `tool_result` events stream the in-band summary only, never the detail store.
  - Phase 4b adds `error(kind="budget")` alongside `kind="loop"` on this terminal — same `error → run_finished(turns=None)` shape, carrying a friendly monthly-cap message; the 4c UI special-cases it (warning styling, run ends cleanly).
- **Public `/agent` is gated behind `AGENT_PUBLIC_ENABLED` (default off)** so the endpoint can sit on `master` without exposing a live, cost-bearing surface before the 4b guardrails land.
- **Hard spend cap lives at the Anthropic key/workspace level**, not in-app — Render's ephemeral filesystem + idle spin-down make any in-app counter an unreliable financial backstop. In-app rate limiting handles throttling/UX only.
- **Public demo = bounded curated questions first** (reuse the eval curated set: known-good behaviour, zero prompt-injection surface). Free-text is gated/deferred.
- **Portfolio framing = a prominent section on the FV project page + the technical write-up linked as the long-form.** Keeps the agent billed under FV.
- **CLI-first.** The public surface is Phase 4 and optional/guarded.
- **Docs in `agent/docs/`**; briefs in `agent/docs/briefs/`; cost model in `agent/docs/`.

## Workflow conventions

- **Branch per phase** (`feature/agent-phaseN`); merge only when that phase's acceptance criteria _and_ the existing FV test suite pass. Phase 4 is split into sub-phases `4a`–`4e`, each its own branch/gate.
- **Opus** for architecture/adversarial review, **Sonnet** for scoping/iteration, **Claude Code** for implementation.
- **New chat at each phase boundary**, seeded with this roadmap.
- **Run tests inside the venv** (`.\.venv\Scripts\Activate.ps1` then `pytest -q`) — the global Python lacks the project deps and will fail collection with `ModuleNotFoundError`, not real failures.
- **Adversarial diff review before merge** is standard: generate `git diff master..feature/agent-phaseN`, upload it, read it directly — never trust a prose summary or the green-tests count alone (a vacuous test passes green).

---

## Phase plan

### Phase 1 — Spine · status: MERGED

Loop + two tools (`cost_and_variance`, `optimal_schedule`) + CLI + tracer.

- [x] Spine built by CC; DRY constraint held (no core refactor needed).
- [x] Review fixes applied (Fix 1 schedule-format + Fix 2 truncation gate cleared). See `cc_brief_agent_phase1_review_fixes.md`.
- [x] Merged to `master` (FV suite green on branch).

### Phase 2 — Full scaffolding · status: MERGED

The real learning phase — "everything hard lives in the scaffolding."

- [x] Full tool set: `compare_schedules`, `efficient_frontier`, `sweep` (structural/calibrated/market classes), `list_symbols`, `get_symbol_reference`, `describe_model` — all read-only; two-part result shape (summary + out-of-band detail store).
- [x] Context/transcript management: tool-result shaping + thresholded compaction (running-state preserves established facts) + stable prefix ordering.
- [x] Harder error cases and recovery: two-surface model (tool-errors → model, loop-errors → harness); backoff, duplicate-call guard, retry budgets.
- [x] Schema validation: one Pydantic model per tool, JSON schema generated from it, contract test (advertised == validated).
- [x] Probe review passed + compaction integration test hardened to assert fact-survival. See `cc_brief_agent_phase2_scaffolding.md` + `cc_brief_agent_phase2_compaction_test_fix.md`.
- [x] Opus adversarial diff pass + remediation: F1 (max_tokens budget-raise recovery), F2 (custom-weight bin width), M1–M3, then `_n_bins_for` single-sourced to `api.market_impact.default_n_bins`. See `cc_brief_agent_phase2_review_fixes.md` + `cc_brief_agent_phase2_premerge_cleanup.md`.
- [x] Merged to `master` (agent suite + FV suite green on the branch).

### Phase 3 — Eval harness · status: MERGED (PR #3)

The differentiator; exploits the deterministic model for ground truth. Three layers: Layer 1 (answer correctness vs in-process FV ground truth), Layer 2 (deterministic trace checks), Layer 3 (Sonnet judge with anchored rubric, real gold set, judge-human agreement + Cohen's κ + self-consistency).

- [x] Layer 1 + Layer 2 + Layer 3 built. See `cc_brief_agent_phase3_eval_harness.md`, `cc_brief_agent_phase3_reasoning_judge_1.md`, `cc_brief_agent_phase3_groundedness_fix.md`.
- [x] Opus adversarial pass + remediation (B1/B2, S1–S3, M1–M4, N1–N7, S1a). All blockers closed with real, non-vacuous tests — `optimal_schedule` GT pinned to the live tool by a DRY equality test; #14 rephrased cost↔GT consistent; groundedness accepts pairwise ± (ratios deliberately excluded with an anti-leniency regression test), parses comma-grouped numbers, seeds the pool with question literals; `check_tool_path` honours alternatives; unspecified-schedule items specify TWAP or move to assumption probes; per-run exception isolation in the harness. See `cc_brief_agent_phase3_review_fixes.md`.
- [x] Merged to `master` via PR #3.

### Phase 4 — Surface + writeup · status: IN PROGRESS

Public-facing surface + the portfolio write-up. Decomposed into gated sub-phases; 4b builds on 4a's stream, 4c builds on both.

#### Phase 4a — Streaming spine · status: MERGED

- [x] `/agent` SSE endpoint in the existing FastAPI service; event-sink refactor of the loop (`None` ⇒ byte-identical prod path); off-by-default `AGENT_PUBLIC_ENABLED` gate; uniform `run_finished` terminal; summary-only `tool_result` events.
- [x] Opus adversarial pass + remediation: F1 (vacuous byte-identical test → real constructor-spy + positive control), F2 (uniform terminal: optional `RunFinished.turns`, worker emits `error(loop) → run_finished(turns=None)`), F3 (AC2 docstring claims only what it tests). Live `curl -N` confirmed SSE framing + the error terminal over the wire. 137 tests green (agent + FV). See `cc_brief_agent_phase4a_streaming_spine.md` + `cc_brief_agent_phase4a_review_fixes.md`.
- [x] Merged to `master`.

#### Phase 4b — Guardrails · status: MERGED

- [x] Per-IP / per-session rate limiting (throttling/UX).
- [x] Hard spend cap at the Anthropic key/workspace level (dedicated low-cap key) — the true financial backstop, independent of app state.
- [x] Demo-mode: bounded question allowlist (reuse the eval curated set); turn/size caps.

#### Phase 4c — UI tab · status: MERGED

- [x] "Ask the model" tab on FV showing the live tool-call trace + charts responding (consumed via `fetch` + `ReadableStream`).
- [x] Make clear in the UI that symbol params are stored reference values, not a live feed.
- [x] First-request cold-start note (Render free-tier spin-down).
- Wiring: `docs/ask.html` served at `/ask`; "Ask the model" tab + left-rail item, `.active` per page; canonical topnav reconciled into `ask.html`; `/ask` route smoke test added. Automated suite green; live-LLM manual verification (brief §9, needs a dev key) pending before merge.

#### Phase 4d — Technical write-up · status: MERGED

- [x] **`agent/docs/agent_writeup.md`** — the detailed "what / how / why":
  - *What*: purpose + capabilities — natural-language Q&A over the FV market-impact model via native tool use, plus the guarded public demo surface.
  - *How*: hand-rolled loop; hosted brain (Haiku 4.5) isolated in `llm.py`; in-process tools wrapping FV functions (no copied logic); two-part result shape (summary + out-of-band detail store); thresholded compaction with running-state; two-surface error recovery; Pydantic single-source schemas; prompt caching; SSE streaming via the event-sink; the three-layer eval harness.
  - *Why*: the locked-decision rationale — DRY as a hard constraint; no framework ("everything hard lives in the scaffolding"); in-process over copied model logic; deterministic model as eval ground truth; synthetic recovery as estimator behaviour, not market calibration; AC schedules sub-optimal under the 0.6 power-law (feature, not bug); Almgren et al. (2005) correctness points.
  - Assembled from the locked decisions in this roadmap + the CC briefs — consolidation, not reconstruction.

#### Phase 4e — Portfolio framing · status: TODO

- [ ] Prominent section on the FV project page + the write-up linked as the long-form. Shares source material with `frontierview_interview_prep.md`.

---

## Backlog (deferred, with target phase)

- **Phase 3 residuals (post-merge, non-blocking):**
  - Extend the optimal-schedule DRY test to cover `cur_optimal_msft_lambda` (MSFT at λ=1e-5) — the one optimal case whose GT-vs-tool equivalence isn't yet parametrised (current test covers AAPL/SPY/JPM at default λ). One-line tightening.
  - Confirm FV's `cost_and_variance` >ADV warning string contains a phrase in `_CAVEAT_KEYWORDS`; if not, #15 (`cur_large_order_jpm`) will undercount on the L2 caveat check — a measurement artifact, not agent failure. 30-second grep.
- Trim `schedule_bins` from model-visible results — **shipped in P2** via the summary + out-of-band detail-store split (bin/frontier arrays never re-enter the transcript).
- `calibrate` — **partially unblocked (reason corrected).** FV _does_ have a real WLS fitting routine (`fit_parameters`), so the old "no fitting routine" reason was wrong. But FV's calibration is **synthetic recovery** — it generates fills from the reference η/γ and recovers them — not a fit to supplied trades. A `calibrate(trades)` tool still needs real data ingestion FV lacks, so that version stays deferred. A _recovery-demo_ tool wrapping the existing synthetic routine in-process is feasible and read-only, but must be framed as synthetic recovery, not market calibration (mandatory caveat, like `sweep`'s structural caveat). Optional; lower priority than P4.
- Prompt caching on the system+tools prefix — **shipped early in P2** (`llm.py` marks the system block + last tool `cache_control: ephemeral`; the stable prefix ordering supports it).
- Second provider via the `llm.py` isolation point — later, only if needed.
- Split the agent onto a separate worker so a long request can't block the web service — later, only if traffic warrants. (Streaming + the worker thread mitigate this for now; client disconnect leaves the bounded run to finish and discard.)
- Keep the cost model's rates current as providers change pricing — ongoing.
- Rationale capture is ongoing — locked decisions + briefs are the raw material for the 4d technical write-up; keep them current so the write-up stays assembly, not archaeology.

## Resolved questions (were open; settled in Phase 4 planning)

- **Provider:** Haiku 4.5 for the demo (matches the eval baseline so observed behaviour matches the measured numbers); Flash is the documented fallback only. → locked above.
- **Public-demo exposure:** bounded curated questions first; free-text gated/deferred. Cost controlled by the key-level hard cap, not by provider choice. → locked above.
- **Portfolio framing:** prominent FV project-page section + linked technical write-up. → locked above.

## Artifacts

- `agent/docs/briefs/cc_brief_agent_phase1_spine.md` — Phase 1 build brief.
- `agent/docs/briefs/cc_brief_agent_phase1_review_fixes.md` — Phase 1 review remediation brief.
- `agent/docs/briefs/cc_brief_agent_phase2_scaffolding.md` — Phase 2 build brief.
- `agent/docs/briefs/cc_brief_agent_phase2_compaction_test_fix.md` — compaction test hardening brief.
- `agent/docs/briefs/cc_brief_agent_phase2_review_fixes.md` — Phase 2 adversarial-review remediation (F1/F2/M1–M3).
- `agent/docs/briefs/cc_brief_agent_phase2_premerge_cleanup.md` — binning single-source + recovery-docstring refresh.
- `agent/docs/briefs/cc_brief_agent_phase3_eval_harness.md` — Phase 3 Layer 1+2 build brief.
- `agent/docs/briefs/cc_brief_agent_phase3_reasoning_judge_1.md` — Phase 3 Layer 3 judge brief.
- `agent/docs/briefs/cc_brief_agent_phase3_groundedness_fix.md` — groundedness fix brief.
- `agent/docs/briefs/cc_brief_agent_phase3_review_fixes.md` — Phase 3 adversarial-review remediation (B1/B2, S1–S3, M1–M4, N1–N7).
- `agent/docs/briefs/cc_brief_agent_phase4a_streaming_spine.md` — Phase 4a build brief.
- `agent/docs/briefs/cc_brief_agent_phase4a_review_fixes.md` — Phase 4a adversarial-review remediation (F1/F2/F3).
- `agent/docs/briefs/cc_brief_agent_phase4c_ui_tab.md` — Phase 4c UI-tab wiring brief.
- `agent/docs/frontierview_agent_cost_model.xlsx` — driveable per-query / monthly cost model across providers.
