# CC Brief — Phase 4c UI tab: wiring

Save to `agent/docs/briefs/cc_brief_agent_phase4c_ui_tab.md`.

Implementation-only brief. The page itself is **already authored** (Opus 4c session),
reconciled to the live assets, and syntax-checked. CC's job here is to **wire it into
the repo**, reconcile nav across pages, confirm one contract, add a route test, and do
roadmap housekeeping. **Do not re-author `ask.html`'s logic.**

Run tests inside the venv: `.\.venv\Scripts\Activate.ps1` then `pytest -q`. Branch:
`feature/agent-phase4c` off `master` (confirm 4b is on `master` first). Adversarial diff
review before merge — fresh `git diff master..feature/agent-phase4c`, read directly.

---

## Already done (don't redo)

`docs/ask.html` is complete and reconciled:
- Uses FV's real `--md-*` tokens → light/dark follows the existing toggle; charts
  recolour on toggle via the site's `window.updateChartColors` hook.
- Carries a full `<header class="topnav">` with the IDs `nav.js` enhances
  (`#topnav-desktop`, `#topnav-pill`, `#topnav-mobile-menu`, `#hamburger-icon`,
  `#hamburger-btn`, `.theme-icon`).
- SSE via `fetch` + `ReadableStream`; both rejection paths (pre-stream HTTP status;
  in-stream `error(kind=loop|budget)` → `run_finished`); cold-start state;
  reference-not-live badge.
- **Generic result-card renderer** covering all 8 tools' summary shapes (not just
  `cost_and_variance`), surfacing each tool's `caveat`/`warning`.
- Charts per ROADMAP §3b: cost decomposition straight from `tool_result.summary`;
  efficient-frontier curve re-derived from `/analyse` **plus** the agent's own
  `low_lambda_end`/`high_lambda_end`/`knee` overlaid from the stream.

These reflect findings from `tools.py` (the real `EfficientFrontierInput` carries
`side`/`lambda_range`/`n_points`, and its summary exposes low/high/knee, not top-level
cost/variance). No further `ask.html` logic changes required.

---

## Wiring tasks

### 1. Place the page
Save the 4c artifact verbatim as `docs/ask.html`.

### 2. Page route — `api/main.py`
Add beside the existing `/about` and `/calibration` page routes:

```python
@app.get("/ask")
def ask():
    return FileResponse("docs/ask.html", media_type="text/html")
```

No `_STATIC_ASSETS` change — it already serves `/nav.css`, `/design-tokens.css`,
`/nav.js`, `/iguana.svg`, `/analytics.js`. The page is reachable at `/ask` (and, via the
`StaticFiles` mount, `/docs/ask.html`; the clean route is canonical).

### 3. Nav across **all** pages (`nav.js` enhances, it does not inject)
Each page carries its own nav markup, so the tab must be added everywhere and the
reconstruction in `ask.html` swapped for the canonical block:

- **`docs/index.html`, `docs/about.html`, `docs/calibration.html`** — add the Ask entry
  to the desktop nav and the mobile menu, in the same position used in `ask.html`:
  ```html
  <a class="topnav-link" href="/ask">Ask the model</a>          <!-- in #topnav-desktop -->
  <a class="topnav-mobile-link" href="/ask">Ask the model</a>   <!-- in #topnav-mobile-menu -->
  ```
- **`docs/ask.html`** — replace the reconstructed `<header class="topnav">…</header>`
  with the canonical block copied from an existing page (guarantees label/order/logo/
  structure parity), then set `class="topnav-link active"` + `aria-current="page"` on the
  Ask link (and `topnav-mobile-link active` on the mobile one).
- **Left `.nav` rail** — check whether the existing pages render `<nav class="nav">…</nav>`
  and wrap content in `<div class="layout">` (nav.css defines both; `.layout` adds the
  80px left margin the fixed rail needs):
  - If they **do**: add an "Ask the model" `.nav-item` (reuse an existing inline SVG icon
    or a neutral glyph), mark it `.active` on `ask.html`, and wrap `ask.html`'s `<main>`
    in `<div class="layout">`.
  - If they **don't**: leave `ask.html` as-is (full-width under the topnav, no rail) — do
    **not** add `.layout` without the rail or you'll get an empty 80px gutter.

### 4. Fonts & analytics parity
- Inter + Space Grotesk: `nav.css` names them but doesn't load them. Check how the
  existing pages load them. If self-hosted → **delete** the Google Fonts `<link>` block in
  `ask.html`. If they use the same CDN `<link>` → keep it.
- Confirm the `<script defer src="/analytics.js"></script>` include matches the other
  pages (Umami). Adjust placement/attrs to match.

### 5. Confirm the charting contract (verified — just sanity-check the model)
The frontier chart maps the agent's `efficient_frontier` tool to `/analyse`. From
`tools.py` + `main.py`:
- `EfficientFrontierInput = {symbol, side, order_size, horizon_hours,
  lambda_range=[1e-9,1e-1], n_points=17, n_bins?}`.
- `AnalyseRequest = {symbol, order_size, horizon_hours, schedule_type}`; `schedule_type`
  Literal includes `"twap"`.
- `ask.html` sends only `{symbol, order_size, horizon_hours, schedule_type:"twap"}` (the
  shared fields; the cost/variance locus is side- and λ-range-independent) and overlays
  the agent's low/high/knee from the stream. The overlay is what makes "charts agree with
  the agent" visible — the `/analyse` curve is `generate_frontier`'s own λ-grid, not a
  point-for-point echo of the tool's grid.

Action: open `api/models.py` and confirm `AnalyseRequest` accepts that 4-field body
(`schedule_type` required-with-`"twap"`-valid or optional — either is fine; we always send
it). No code change expected; the recompute is fail-soft regardless.

### 6. Curated-set coverage (informational, not blocking)
Open `agent/eval/questions/curated.py` (the 4b allowlist source) and note which of the 20
questions invoke `cost_and_variance` (→ decomposition chart), `efficient_frontier`
(→ frontier chart + overlay), or other tools (trace only). If none drive
`efficient_frontier`, that chart path is dormant in the demo — fine; the trace +
decomposition carry it. Record the finding in the PR description.

### 7. Route smoke test
Mirror the existing API test style (likely `api/tests/`):
- `GET /ask` → `200`, `content-type` starts `text/html`, body contains a sentinel
  (`id="trace"` or `<title>Ask the model`).
- If page routes are already covered by a parametrised test, extend the params to include
  `/ask` rather than adding a bespoke test.
- The page's JS is not unit-tested in-repo; the real check is the manual run in §9. Run
  `pytest -q` in the venv — expect the prior green count **+1**, no regressions.

### 8. Roadmap housekeeping — `agent/docs/ROADMAP.md`
- Flip the **Phase 4b** status header `TODO → MERGED` (checkboxes already ticked).
- Add a line under the locked stream-terminal decision: `error(kind="budget")` now joins
  `loop`/`tool` (friendly cap message on the stream); 4c special-cases it in the UI.
- Tick the Phase 4c checkboxes once verified; add this brief to the Artifacts list.

---

## Locked — do not relitigate (carry from ROADMAP + 4c handover §7)

- Haiku 4.5 demo provider; bounded curated questions, ID-based contract, **no free text**.
- **Summary-only `tool_result`** over the wire; arrays stay in the out-of-band detail store.
- **Charts via re-derivation from existing FV endpoints** — do **not** extend the agent
  stream to carry arrays, and do **not** add a detail-store HTTP endpoint.
- No framework / no build; new page in `docs/`; same-origin `fetch` + `ReadableStream`.
- Spend cap lives at the Anthropic key/workspace level; in-app rate limiting is UX only.

---

## Acceptance criteria

- `/ask` serves the page; the "Ask the model" entry is present and `.active` on every page.
- Theme toggle works and recolours both charts.
- Both rejection paths handled (pre-stream HTTP non-200; in-stream `budget`/`loop` error
  → `run_finished`).
- Decomposition chart renders from the stream; frontier chart renders the curve + the 3
  agent overlay points (when a curated question drives `efficient_frontier`).
- Reference-not-live badge and cold-start state present.
- Full suite green in the venv; no regressions.

---

## Manual verification (local; gate ON; **dev key**, not the capped `frontierview-public`)

`AGENT_PUBLIC_ENABLED=1`, `ANTHROPIC_API_KEY=<dev>`, `uvicorn api.main:app --reload`,
open `localhost:8000/ask`.

- Buttons populate from `/agent/questions`.
- `cost_and_variance` question → trace: call → result (decomposition kv) → reasoning →
  answer; decomposition bar chart renders.
- `efficient_frontier` question (if in the set) → result card shows low/high/knee; frontier
  chart shows the curve + 3 overlaid agent points; legend present.
- Theme toggle recolours both charts live.
- 429: spam the endpoint to trip the per-IP limiter → countdown state shows.
- Cold-start: first request after idle spin-down shows the waking note (or simulate
  latency locally).
- (Optional) budget path: temporarily force `ProviderBudgetError` in `agent/llm.py` to
  confirm the `kind="budget"` cap message renders and the run ends cleanly.
- Mobile: nav collapses at ≤700px, grid stacks at ≤900px, keyboard focus visible,
  reduced-motion honoured.

---

## Out of scope (still open in Phase 4)

- **4d** — technical write-up (`agent/docs/agent_writeup.md`).
- **4e** — portfolio framing on the FV project page.
