# CC Brief — Phase 4e fast-follow: agent write-up loop diagram

**Goal.** The reason–act–observe diagram on the hosted agent write-up page is
linearised — it renders `question → … → shape result → final answer` as a straight
pipeline, which misstates the control flow. Make the diagram show (1) the loop —
act/observe steps return to the reason step — and (2) `final answer` as the branch
taken when the model emits text with no tool call (the loop's exit). One component,
one file. (The `role="img"` aria-label already describes the loop correctly; this
brings the *visual* up to what the label already claims.)

**Target.** `src/app/frontierview/agent/page.tsx` in the **garethcooke.com**
portfolio repo — the `FlowDiagram` component (and its `FLOW_NODES` data / any
connector styling). **Nothing else.** File this brief at
`agent/docs/briefs/cc_brief_agent_phase4e_followup.md` in the FV repo for the paper
trail; implement in the portfolio repo.

---

## Ground truth (the topology to render)

From the Phase 4d source mermaid — this is the spec:

```
question ──▶ reason (LLM call)
reason ──[tool_use]──▶ act (dispatch tool) ──▶ compute core (api/market_impact.py)
        ──▶ observe (shape result) ──▶ back to reason          ← the loop
reason ──[text, no tool]──▶ final answer                        ← terminal exit
loop (reason → act → compute → observe → reason) bounded by MAX_ITERS = 8
```

Two things the current linear version dropped and must come back: the **observe →
reason loop-back**, and the **two branch conditions** (`tool_use` vs `text, no
tool`) that distinguish the loop from the exit. `final answer` is a branch off
`reason`, not a node appended after `observe`.

## Tasks

1. **Re-render the diagram to the topology above**, hand-built in the same style as
   the current `FlowDiagram` (portfolio tokens, no new design primitives). Two
   acceptable realizations — pick whichever fits the page and wraps cleanly:
   - **(A)** A loop row `reason → act → compute → observe` with a visible return
     connector (arrow/elbow, or a labelled `↩ back to reason` element) from
     `observe` to `reason`; `question` feeds `reason`; `final answer` drops off
     `reason` as a labelled `text, no tool` branch.
   - **(B)** Keep one horizontal row, label the `reason→act` edge `tool_use`, draw a
     visible return arrow from `observe` back to `reason` beneath the row, and peel
     `final answer` out as a labelled `text, no tool` branch off `reason` (not the
     tail node).
   Either way the `tool_use` / `text, no tool` edge labels and the `MAX_ITERS = 8`
   loop bound must be present.

2. **Keep the aria-label and caption accurate** to the new visual (the aria-label
   already describes the looped structure — keep it true; adjust the caption only if
   the wording no longer fits).

## Constraints

- **No mermaid, no graph/diagram/charting library, no new npm dependency** — the
  whole reason this is hand-built is to avoid a runtime dependency for one diagram.
  Plain JSX + the existing style consts.
- **Wrap-robust.** The current `FlowDiagram` row uses `flexWrap`. Whatever
  represents the loop-back/branch must not overlap or break when the row wraps —
  avoid absolutely-positioned overlay arrows that assume a fixed single-row layout
  unless they degrade cleanly. Verify at the page's mobile breakpoint.

## Acceptance criteria

- [ ] Diagram visually shows the act/observe steps returning to `reason` (a clear
      loop-back), not a straight pipeline.
- [ ] `final answer` is presented as the `text, no tool` branch off `reason` (the
      exit), not a node after `observe`.
- [ ] Both branch conditions (`tool_use`, `text, no tool`) are labelled; the loop
      carries the `MAX_ITERS = 8` bound.
- [ ] Renders correctly at desktop **and** the page's mobile breakpoint — no
      overlapping or broken connectors on wrap.
- [ ] aria-label/caption accurate to the new visual.
- [ ] No change to the write-up prose, `FrontierViewAgentSection`, the tab wiring,
      or any other file/route. Copy unchanged.
- [ ] `npm run build`, lint, typecheck clean; no new console errors.

## Optional (same file, fold in only if one-line)

- The illustrative `SSE_TRACE` `run_started.config_summary` reads
  `{model:"…", max_iters:8}` with no elision marker, implying those are the only
  fields. Add `eval_mode:false` (or a trailing `…`) so it doesn't misstate the
  payload. (`tool_result.summary` already carries `…`, so leave it.)

## Workflow

- Branch `feature/agent-phase4e-followup` off the portfolio repo's default branch
  **after 4e has landed** — this edits `page.tsx`, which 4e creates. If 4e isn't
  merged yet, **stack** it on `feature/agent-phase4e` and run the adversarial diff
  two-dot against *that* branch (`git diff feature/agent-phase4e..feature/agent-phase4e-followup`),
  not the default branch, or the diff inflates to include all of 4e.
- Merge gate = adversarial diff review (fresh `git diff`, read directly). The diff
  is tiny — one component plus maybe a couple of style consts.
- No roadmap status change (4e already closes Phase 4); add this brief to *Artifacts*
  in `agent/docs/ROADMAP.md`, and optionally log the polish under 4e if you track
  follow-ups.
