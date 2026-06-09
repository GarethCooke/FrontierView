# FrontierView Agent — Roadmap & Tracker

Single source of truth for the agent project. Update statuses as you go. Seed each new working session with this file.

---

## Locked decisions (don't relitigate)

- **Tools call the model in-process** — `agent/` imports FV's functions directly; no model logic copied. DRY enforced by an equality test.
- **Hand-rolled loop**, no agent framework.
- **Hosted brain, native tool use.** Default: Anthropic Claude Haiku 4.5, isolated in `llm.py` so a provider swap is a one-file change.
- **Lives in the FrontierView repo** as the `agent/` package — not a separate repo. Extract a shared `core/` only if a real second consumer of the model appears.
- **UI = an integrated tab on FrontierView** (not a separate microsite). Code lives within FV; the project can still get its own billing in the portfolio narrative.
- **Deployment stays on Render**, same service. No model self-hosted. New `/agent` endpoint runs server-side and is **streamed** (timeout safety + the watch-it-think demo).
- **CLI-first.** The public surface is Phase 4 and optional/guarded.
- **Docs in `agent/docs/`**; briefs in `agent/docs/briefs/`; cost model in `agent/docs/`.

## Workflow conventions

- **Branch per phase** (`feature/agent-phaseN`); merge only when that phase's acceptance criteria _and_ the existing FV test suite pass.
- **Opus** for architecture/adversarial review, **Sonnet** for scoping/iteration, **Claude Code** for implementation.
- **New chat at each phase boundary**, seeded with this roadmap.

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

### Phase 3 — Eval harness · status: MERGED

The differentiator; exploits the deterministic model for ground truth.

- [x] Question set with model-computed ground-truth answers; auto-score answer correctness.
- [x] Reasoning-quality eval (the harder, non-deterministic part).

### Phase 4 — Surface + writeup · status: TODO

- [ ] `/agent` streaming endpoint in the existing FastAPI service.
- [ ] "Ask the model" tab on FV showing the live tool-call trace + charts responding.
- [ ] Rate limiting + hard spend cap + demo-mode (bounded example questions).
- [ ] Make clear in the UI that symbol params are stored reference values, not a live feed.
- [ ] **Technical write-up** (`agent/docs/agent_writeup.md`) — the detailed "what / how / why":
  - *What*: purpose + capabilities — natural-language Q&A over the FV market-impact model via native tool use, plus the guarded public demo surface.
  - *How*: hand-rolled loop; hosted brain (Haiku 4.5) isolated in `llm.py`; in-process tools wrapping FV functions (no copied logic); two-part result shape (summary + out-of-band detail store); thresholded compaction with running-state; two-surface error recovery; Pydantic single-source schemas; prompt caching; the three-layer eval harness.
  - *Why*: the locked-decision rationale — DRY as a hard constraint; no framework ("everything hard lives in the scaffolding"); in-process over copied model logic; deterministic model as eval ground truth; synthetic recovery as estimator behaviour, not market calibration; AC schedules sub-optimal under the 0.6 power-law (feature, not bug); Almgren et al. (2005) correctness points.
  - Assembled from the locked decisions in this roadmap + the CC briefs — consolidation, not reconstruction.
- [ ] **Portfolio billing / framing** — where the write-up surfaces (prominent FV-project-page section / dedicated post / short standalone). Shares source material with `frontierview_interview_prep.md`.

---

## Backlog (deferred, with target phase)

- Trim `schedule_bins` from model-visible results — **shipped in P2** via the summary + out-of-band detail-store split (bin/frontier arrays never re-enter the transcript).
- `calibrate` — **partially unblocked (reason corrected).** FV _does_ have a real WLS fitting routine (`fit_parameters`), so the old "no fitting routine" reason was wrong. But FV's calibration is **synthetic recovery** — it generates fills from the reference η/γ and recovers them — not a fit to supplied trades. A `calibrate(trades)` tool still needs real data ingestion FV lacks, so that version stays deferred. A _recovery-demo_ tool wrapping the existing synthetic routine in-process is feasible and read-only, but must be framed as synthetic recovery, not market calibration (mandatory caveat, like `sweep`'s structural caveat). Optional; lower priority than P3.
- Prompt caching on the system+tools prefix — **shipped early in P2** (`llm.py` marks the system block + last tool `cache_control: ephemeral`; the stable prefix ordering supports it).
- Second provider via the `llm.py` isolation point — later, only if needed.
- Split the agent onto a separate worker so a long request can't block the web service — later, only if traffic warrants.
- Keep the cost model's rates current as providers change pricing — ongoing.
- Rationale capture is ongoing — locked decisions + briefs are the raw material for the Phase 4 technical write-up; keep them current so the write-up stays assembly, not archaeology.

## Open questions (unresolved)

- **Provider:** Haiku 4.5 (default) vs Google AI Studio free Flash tier for zero-cost dev. Decide at build time.
- **Public-demo exposure:** fully open with tight limits / gated / recorded walkthrough only.
- **Portfolio framing:** prominent section on the FV project page / dedicated writeup / short standalone post.

## Artifacts

- `agent/docs/briefs/cc_brief_agent_phase1_spine.md` — Phase 1 build brief.
- `agent/docs/briefs/cc_brief_agent_phase1_review_fixes.md` — review remediation brief.
- `agent/docs/briefs/cc_brief_agent_phase2_scaffolding.md` — Phase 2 build brief.
- `agent/docs/briefs/cc_brief_agent_phase2_compaction_test_fix.md` — compaction test hardening brief.
- `agent/docs/briefs/cc_brief_agent_phase2_review_fixes.md` — adversarial-review remediation brief (F1/F2/M1–M3).
- `agent/docs/briefs/cc_brief_agent_phase2_premerge_cleanup.md` — binning single-source + recovery-docstring refresh.
- `agent/docs/frontierview_agent_cost_model.xlsx` — driveable per-query / monthly cost model across providers.
