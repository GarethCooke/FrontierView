# CC Brief — Phase 4e: FrontierView Agent Portfolio Section

**Goal.** Add a prominent "Ask the model" section about the FrontierView agent to
the FrontierView project page on **garethcooke.com**, and make the Phase 4d
technical write-up reachable as the linked long-form. This closes Phase 4.
(Locked decision: the agent gets a section on the FV project page, not its own
microsite — it stays billed under FV.)

**Target repo.** This work lands in the **garethcooke.com** portfolio repo
(Next.js 14 App Router, Tailwind v4, TypeScript, AWS Amplify) — **not** the
FrontierView repo. The agent code and its docs live in the FV repo; this is the
portfolio-hub presentation layer only. File this brief at
`agent/docs/briefs/cc_brief_agent_phase4e_portfolio.md` in the FV repo for the
agent project's paper trail, but do the implementation in the portfolio repo.

**Copy = single source of truth.** The exact section copy is in
`frontierview_agent_portfolio_section.md` (the `⟶ Copy` block). **Use it verbatim
— do not rewrite or re-tone.** That file is the input and must be attached
alongside this brief. The copy contains two placeholder tokens to resolve:
`LIVE_DEMO_URL` and `WRITEUP_URL` (see *Decisions*).

---

## Tasks

1. **Discover, then conform.** Read the existing FrontierView project page and the
   section/component pattern it uses — how other project pages structure a content
   section, the design tokens, heading scale, the CTA/button component, spacing.
   Match that pattern exactly; the section must read as native to the page, not
   bolted on. Do **not** introduce new design primitives.

2. **Add the section.** Insert "Ask the model" prominently — after the
   one-paragraph model/tool overview, above the deeper model detail. Render the
   copy verbatim, including the "What it demonstrates" list and both CTAs.

3. **Make the write-up reachable (resolves `WRITEUP_URL`).** The 4d long-form is
   `agent/docs/agent_writeup.md` in the FV repo — currently a repo doc, not a
   portfolio URL. **Preferred:** render it as a first-class page on garethcooke.com
   (e.g. `/frontierview/agent`, or under the existing project route), following
   whatever long-form / MDX / content pattern the site already uses for prose
   pages; bring the markdown across as page content and match site typography.
   **Fallback:** if the site has no existing markdown/MDX rendering path and
   standing one up is out of proportion for a single doc, link the GitHub file as
   an explicit interim and leave a `// TODO: host write-up as a page`. State which
   path you took.

4. **Wire the CTAs + analytics.** Point both CTAs at the resolved URLs. If the
   project pages already instrument outbound/CTA clicks via Umami, add events for
   both (`ask-the-model` demo click, `agent-writeup` click) following the existing
   event-naming pattern. If there's no such instrumentation, skip it — don't
   invent a pattern.

## Decisions (resolve before merge)

- **`LIVE_DEMO_URL` = `https://frontierview.garethcooke.com/ask`** (the `/ask` tab
  shipped in 4c). That tab only responds when `AGENT_PUBLIC_ENABLED` is set on the
  FV Render service — an **ops flip Gareth makes out of band**, not a code change
  in this repo. **Do not ship a live link to a dead tab.** Default handling: gate
  the demo CTA behind a build-time flag (e.g. `NEXT_PUBLIC_FV_AGENT_DEMO_LIVE`) —
  flag off ⇒ render a disabled "demo coming soon" affordance; flag on ⇒ render the
  live link. Section ships now; the demo is one env-flip away.
- **`WRITEUP_URL`** — set to whatever path Task 3 produces.

## Acceptance criteria

- [ ] Section renders on the FV project page, visually native to the existing page
      (same section component, tokens, type scale, spacing) — verified at desktop
      and the page's standard mobile breakpoint.
- [ ] Copy is **verbatim** from `frontierview_agent_portfolio_section.md`; no
      wording changes.
- [ ] `WRITEUP_URL` resolves to a working destination (hosted page preferred;
      GitHub interim acceptable if flagged).
- [ ] Demo CTA shows the live link when the demo flag is on and the "coming soon"
      affordance when off; **no dead link ships**.
- [ ] Analytics events added iff the existing pattern supports it; otherwise omitted.
- [ ] `npm run build`, lint, and typecheck pass clean (plus any existing CI/tests);
      no new console errors.

## Out of scope

- Flipping `AGENT_PUBLIC_ENABLED` (ops, Gareth's call).
- Any change to the FrontierView repo's agent code or the `/ask` tab — 4e is
  presentation on the hub only.
- The model's calibration caveats (0.6-vs-square-root, absolute-level accuracy) —
  those belong to the model's own section/explainer, not this section.
- Re-toning, trimming, or expanding the copy.

## Workflow

- Branch `feature/agent-phase4e` off the portfolio repo's default branch (your
  standing default is `master`; confirm per repo). One branch, this scope only.
- **Merge gate = adversarial diff review** (standard): generate a fresh
  `git diff <base>..feature/agent-phase4e`, read it directly — never sign off from
  a prose summary. Findings classified fix-before-merge vs fast-follow. The diff
  here is small (one section + a link target + possibly a write-up page), so it
  should review fast.
- On merge: flip Phase 4e `TODO → MERGED` in `agent/docs/ROADMAP.md` and add this
  brief to the roadmap's *Artifacts* list. That closes Phase 4 and the agent
  roadmap end to end.
