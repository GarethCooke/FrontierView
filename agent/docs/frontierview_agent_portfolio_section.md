# FrontierView Project Page — Agent Section (Phase 4e portfolio framing)

> The prominent agent section to add to the existing FrontierView project page on
> garethcooke.com, with the Phase 4d technical write-up
> ([`agent/docs/agent_writeup.md`](../FrontierView/agent/docs/agent_writeup.md))
> linked as the long-form. Keeps the agent billed under FrontierView (locked
> decision). Written in the same honest, substantive register as
> `frontierview_interview_prep.md`, and drawing the model-correctness points from
> the same source material so the two stay consistent. Implementation notes
> (placement, link targets, the gate precondition) are at the end.

---

## ⟶ Copy (lift this into the page section)

### Ask the model

*A hand-rolled LLM agent over the market-impact model.*

FrontierView answers execution questions in plain English. Ask it *"what does it
cost to work 200,000 AAPL over two hours, and how does that split between
temporary and permanent impact?"* and it decides which model functions to call,
calls them, reads the results, and explains the answer — streaming its tool-call
trace live, so you watch it work rather than wait on a black box.

The point isn't the chat. It's that the agent reasons over the **exact same
compute the rest of the site runs**. Its tools import FrontierView's
market-impact core directly and call it in-process — there is no second model and
no re-implemented formula, and a build-time equality test fails if the two ever
drift apart. The numbers the agent quotes are the numbers the site computes, by
construction.

It's built **without an agent framework**, which was the point: every hard part
of an LLM agent had to be solved deliberately rather than inherited from a
library — context compaction, a two-part tool-result shape that keeps bulky
arrays out of the model's context, error handling that distinguishes *"the model
can recover from this"* from *"it can't,"* prompt caching, and SSE streaming that
leaves the non-streaming path byte-for-byte identical.

And because the model underneath is deterministic, **"is the answer correct" is a
number, not an opinion**. A three-layer offline evaluation harness scores answer
correctness against ground truth computed from the same functions, checks the
tool-call trace structurally, and runs an LLM-as-judge against a rubric that
encodes the quant facts explicitly — the Almgren et al. (2005) parameterisation,
linear permanent impact, and the closed-form "AC-optimal" schedule as a
linearised benchmark rather than the true optimum under the 0.6 power law.

**What it demonstrates**

- A tool-use agent hand-rolled from the loop up — no LangChain, no agent framework.
- Tools that *cannot* silently diverge from the model they wrap — DRY enforced by a test, not a convention.
- A three-layer evaluation harness that turns a deterministic model into computable ground truth.
- A guarded public surface: a curated question set with no free-text field (zero prompt-injection surface) and a hard spend cap at the API-key level.
- Quant domain taken as seriously as the engineering — the model's subtleties are encoded and graded against, not glossed.

The public demo is deliberately bounded: a curated set of questions over stored
**reference parameters** (not a live market feed), behind a spend cap. It's a
demonstration of agent engineering and evaluation discipline, not a production
execution system.

**[ Ask the model → ]**(LIVE_DEMO_URL)    **[ Read the technical write-up → ]**(WRITEUP_URL)

---

## Implementation notes (not page copy)

**Placement.** Add as a prominent section on the existing FrontierView project
page — the agent is the newest and most differentiated workstream, so it earns a
spot high on the page, after the one-paragraph model/tool overview and before the
deeper model detail. It should read as *the* headline piece of recent work, not a
footnote.

**Link targets — two decisions to resolve before wiring:**

1. `LIVE_DEMO_URL` → the "Ask the model" tab on the FV subsite, i.e.
   `https://frontierview.garethcooke.com/ask` (the `/ask` page shipped in 4c).
   **Precondition:** that tab only responds when `AGENT_PUBLIC_ENABLED` is set on
   the Render service. The spend cap and curated-question guardrails are already
   live (4b), so flipping the gate on is what makes the CTA real. If you want to
   ship the page section *before* flipping the gate, either drop the demo CTA or
   label it "demo coming soon" so a dead link doesn't read as a broken portfolio.

2. `WRITEUP_URL` → the Phase 4d long-form. It currently lives as a repo doc
   (`agent/docs/agent_writeup.md`), which isn't a clean portfolio link. Pick one:
   - **(recommended)** render it as a page on garethcooke.com or the FV subsite
     (e.g. `/frontierview/agent` or `frontierview.garethcooke.com/writeup`) — a
     first-class portfolio URL, consistent typography, no GitHub chrome; or
   - link the GitHub file directly — zero work, but sends a hiring reader into a
     raw repo rather than the portfolio.

**Voice / scope.** This section is about the *agent*. The model's calibration
caveats (0.6 vs square-root, illustrative absolute cost levels, uncalibrated
parameters) belong to the model's own section / explainer and to the interview
prep — deliberately not surfaced here, to keep the agent section focused. The one
honest note that *does* belong here — bounded demo, reference (not live) data — is
in the copy.

**Next step in the workflow.** The copy is the Opus-level framing; wiring it into
the Next.js 14 / Tailwind v4 project page (matching the existing project-page
section component, design tokens, and the analytics event pattern) is mechanical
CC work. Say the word and I'll write the `cc_brief_agent_phase4e_portfolio.md`
brief for that handoff.

**Roadmap housekeeping.** On ship, flip Phase 4e `TODO → MERGED` in
`agent/docs/ROADMAP.md` — that closes Phase 4 and the agent project's roadmap end
to end.
