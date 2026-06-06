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

### Phase 1 — Spine · status: BUILT, fixes applied — merge pending

Loop + two tools (`cost_and_variance`, `optimal_schedule`) + CLI + tracer.

- [x] Spine built by CC; DRY constraint held (no core refactor needed).
- [x] Review fixes applied (Fix 1 schedule-format + Fix 2 truncation gate cleared). See `cc_brief_agent_phase1_review_fixes.md`.
- [x] Merge `feature/agent-phase1` once FV suite passes on the branch.

### Phase 2 — Full scaffolding · status: BUILT — merge pending

The real learning phase — "everything hard lives in the scaffolding."

- [x] Full tool set: `compare_schedules`, `efficient_frontier`, `sweep`, `list_symbols`, `get_symbol_reference`, `describe_model`. DRY held; existing tools migrated to two-part result shape.
- [x] Context/transcript management: thresholded compaction, stable prefix ordering.
- [x] Harder error cases and recovery: full §4 recovery-policy table with tests per row.
- [x] Schema validation: one Pydantic model per tool; schema advertised == schema validated (contract test). Semantic validation (weights sum to 1, λ>0, symbol in list, ADV sanity).

### Phase 3 — Eval harness · status: TODO

The differentiator; exploits the deterministic model for ground truth.

- [ ] Question set with model-computed ground-truth answers; auto-score answer correctness.
- [ ] Reasoning-quality eval (the harder, non-deterministic part).

### Phase 4 — Surface + writeup · status: TODO

- [ ] `/agent` streaming endpoint in the existing FastAPI service.
- [ ] "Ask the model" tab on FV showing the live tool-call trace + charts responding.
- [ ] Rate limiting + hard spend cap + demo-mode (bounded example questions).
- [ ] Make clear in the UI that symbol params are stored reference values, not a live feed.
- [ ] Writeup / portfolio billing.

---

## Backlog (deferred, with target phase)

- Trim/summarise `schedule_bins` in tool results to cut transcript tokens — P2/scale.
- `calibrate(trades) → η, γ with standard errors` — **deferred out of P2.** FV uses fixed Almgren 2005 Table 3 values and has no fitting routine or trade dataset; adding it means new model logic + data ingestion, which breaks the wrap-existing-funcs / DRY principle. Revisit only if FV itself gains a calibration capability.
- Prompt caching on the resent system+tools prefix (~90% off cached input) — P4/when cost matters.
- Second provider via the `llm.py` isolation point — later, only if needed.
- Split the agent onto a separate worker so a long request can't block the web service — later, only if traffic warrants.
- Keep the cost model's rates current as providers change pricing — ongoing.

## Open questions (unresolved)

- **Provider:** Haiku 4.5 (default) vs Google AI Studio free Flash tier for zero-cost dev. Decide at build time.
- **Public-demo exposure:** fully open with tight limits / gated / recorded walkthrough only.
- **Portfolio framing:** prominent section on the FV project page / dedicated writeup / short standalone post.

## Artifacts

- `agent/docs/briefs/cc_brief_agent_phase1_spine.md` — Phase 1 build brief.
- `agent/docs/briefs/cc_brief_agent_phase1_review_fixes.md` — review remediation brief.
- `agent/docs/frontierview_agent_cost_model.xlsx` — driveable per-query / monthly cost model across providers.
