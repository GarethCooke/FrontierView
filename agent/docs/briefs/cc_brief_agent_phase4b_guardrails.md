# CC Brief — Agent Phase 4b: Guardrails

Branch: `feature/agent-phase4b` (off `master`, post-4a). Adversarial diff review before merge. Agent + FV suites must stay green (137 baseline).

## Scope

Two code components: (1) demo-mode question allowlist on the public `/agent` surface, (2) in-app per-IP rate limiting. The third 4b item — the hard spend cap — is **manual Console work, not code**; operator checklist in Appendix A. Do not build any in-app spend counter: Render's ephemeral filesystem + spin-down make it an unreliable financial control, and the key-level cap is the backstop (locked decision).

Out of scope: UI (4c), free-text input in any form, Redis/external state, the eval harness. One deliberate, narrow extension to the 4a stream contract is in scope: the `error(kind="budget")` terminal (Component 3).

## Locked decisions this brief implements

- Public demo = **bounded curated questions, zero prompt-injection surface**. The public endpoint accepts a question **ID**, never text. Server maps ID → canonical question string.
- Allowlist **single-sourced by import** from `agent/eval/questions/curated.py::CURATED_QUESTIONS` — no copied list, no duplicated strings. Enforced by an equality test (same pattern as the binning and optimal-GT DRY tests).
- Rate limiting is **throttling/UX only**, not a security or financial control. In-memory, per-process; counter reset on spin-down is accepted (if the instance spun down, there was no traffic worth throttling). IP spoofability is accepted for the same reason — money is guarded at the key level.
- Demo turn/size caps = the **existing loop bounds**, unchanged. Do **not** introduce a tighter public-only turn cap: the demo must reproduce eval-measured behaviour (the locked reason Haiku 4.5 is the demo provider), and a tighter cap could truncate a curated question that legitimately needed more turns under eval. Document the existing max-turns as the guardrail.

## Component 1 — Demo-mode allowlist

### Request contract

`POST /agent` (the existing 4a endpoint, still gated by `AGENT_PUBLIC_ENABLED`, default off) changes its public body to:

```json
{ "question_id": "cur_cost_aapl_natural" }
```

- Pydantic request model with `extra="forbid"` — a body containing `text`, `question`, `prompt`, or any other field is a 422. This is the injection guarantee: there is no field a prompt can travel in.
- Unknown but well-formed `question_id` → **404**, JSON body `{"error": "unknown question_id"}`. Malformed body → **422** (Pydantic default).
- Valid ID → resolve to the canonical `Question.text`, run the loop exactly as 4a does today. Stream contract unchanged: SSE, summary-only `tool_result`, uniform `run_finished` terminal.
- All rejections (404/422/429) are **plain JSON responses before the stream opens** — never in-stream `error` frames. In-stream `error(kind="loop") → run_finished(turns=None)` remains reserved for loop escapes after a stream has started. (4c consumer will need both paths; lock the distinction here.)

### Allowlist source

```python
from agent.eval.questions.curated import CURATED_QUESTIONS
ALLOWED: dict[str, str] = {q.id: q.text for q in CURATED_QUESTIONS}
```

- All 20 curated questions are exposed, **including** the out-of-tool items (#10–12) and the >ADV caveat case (#15) — declines and caveats are demo features, not liabilities.
- **Known import-time cost, accepted:** `curated.py` executes `gt_cost(...)` calls in its module-level literals, so importing it runs FV GT computations at server startup. This is in-process deterministic math and cheap; accept it. If startup time measurably regresses, the fallback is extracting `(id, text)` pairs to a lighter module that both `curated.py` and the server import — do **not** solve it by copying strings.

### Question listing for the UI

Add `GET /agent/questions` (same `AGENT_PUBLIC_ENABLED` gate): returns `[{"id": ..., "text": ...}, ...]` for all allowlisted questions, in curated-set order. This is the 4c UI's button source. Static, cheap, exempt from rate limiting (see below).

## Component 2 — Rate limiting

- **Limits:** 5 requests/min and 50 requests/day per client IP, applied to `POST /agent` only. `GET /agent/questions` is exempt (static, no LLM cost).
- **Identity:** first hop of `X-Forwarded-For` (Render fronts the service with a proxy; `request.client.host` is the proxy). Fall back to `request.client.host` if the header is absent (local dev).
- **Mechanism:** in-memory per-IP counters — fixed windows are fine (minute window + UTC-day window); no token-bucket sophistication required. Guard with a `threading.Lock` — 4a runs the loop in worker threads and FastAPI handlers may interleave.
- **Rejection:** HTTP **429**, `Retry-After` header (seconds to window reset), JSON body `{"error": "rate limited"}`. Before the stream opens, per the contract note above.
- **Testability:** inject a clock (callable returning epoch seconds) into the limiter so window-expiry tests need no sleeps.
- **Eviction:** prune stale IP entries on touch or with a size cap — unbounded dict growth is slow-motion memory leak; a simple "drop entries whose day-window has lapsed" pass when the map exceeds ~10k entries is sufficient.
- Document in the module docstring: single-process assumption; resets on restart/spin-down; not a security control.

## Component 3 — Budget-exhausted terminal

When the capped key's monthly spend limit is hit, the Anthropic API returns 429 and the SDK raises `RateLimitError`. Without handling, this escapes as a generic `error(kind="loop")` — technically uniform, useless to a demo visitor. Add a classified terminal:

- **Classification rule:** an `anthropic.RateLimitError` (or equivalent 429-class SDK error) that survives the existing retry budget is classified as budget exhaustion. Do not add short-circuit logic to skip retries on 429 — a transient provider rate limit should still get its retries, and the wasted retries on a true spend-cap hit cost seconds, not money (the requests are rejected upstream). Retry exhaustion *is* the classifier.
- **Stream contract:** emit `error(kind="budget", message=<text below>) → run_finished(turns=None)`. The uniform terminal is preserved; `kind="budget"` is a new value alongside `"loop"`, and the 4c UI will special-case it for display. All other escaped exceptions keep `kind="loop"` unchanged.
- **Message text** (honest about the ambiguity — a sustained 429 is either the monthly demo budget or provider throttling, indistinguishable client-side):
  > "The demo's usage budget has been reached for now — likely the monthly cap. It resets at the start of next month; the rest of FrontierView works as normal in the meantime."
- **Where:** classification lives in the worker's exception handling (where escaped exceptions already become `error → run_finished`), not inside `llm.py` — `llm.py` stays a thin provider isolation layer. If keeping the worker provider-agnostic matters, have `llm.py` wrap/re-export the SDK exception as `ProviderBudgetError`; implementer's choice, note it in the diff.

## Tests (all real, non-vacuous — assert behaviour, not just non-exception)

1. **DRY equality:** the served allowlist keys == `{q.id for q in CURATED_QUESTIONS}` and each served text == the corresponding `Question.text`. Guards against future drift to a copied list.
2. **No free-text surface:** body with an extra `text` (or `prompt`) field → 422; body with only `question_id` but unknown value → 404. Positive control: a known ID passes validation.
3. **Gate:** with `AGENT_PUBLIC_ENABLED` unset/false, both `POST /agent` and `GET /agent/questions` return the 4a-established disabled behaviour (unchanged).
4. **Rate limit, minute window:** 5 requests pass, 6th → 429 with `Retry-After`; after injected-clock advance past the window, requests pass again.
5. **Rate limit, day window:** 50 pass, 51st → 429, independent of the minute window (advance the clock between bursts).
6. **Per-IP isolation:** IP A exhausted does not throttle IP B.
7. **Exemption:** `GET /agent/questions` unaffected by an exhausted POST limit.
8. **Budget terminal:** with the LLM call stubbed to raise the 429-class error persistently, the stream ends `error(kind="budget", message=...) → run_finished(turns=None)`. Positive control: a non-429 escaped exception still yields `kind="loop"`. Assert the message text is the locked wording, not the raw exception string.
9. **Stream-contract regression:** a valid-ID request still produces the 4a event sequence ending in `run_finished` (reuse/extend the 4a SSE test rather than duplicating it).
10. Existing 137 agent + FV tests green.

## Acceptance criteria

- AC1: Public `POST /agent` accepts only `{"question_id"}` from the curated set; no request field can carry free text (enforced by `extra="forbid"` + tests 1–2).
- AC2: Allowlist is import-sourced from `CURATED_QUESTIONS` with an equality test; zero copied question strings anywhere in the diff.
- AC3: Per-IP limits of 5/min and 50/day on `POST /agent`, 429 + `Retry-After` as plain JSON pre-stream; clock-injected tests, no sleeps.
- AC4: No new turn/size caps on the loop; existing loop bounds documented as the demo guardrail.
- AC5: 4a stream contract unchanged for a valid request except the additive `kind="budget"` error value; gate default remains off.
- AC6: Persistent 429-class provider errors terminate as `error(kind="budget", <locked message>) → run_finished(turns=None)`; all other escapes remain `kind="loop"`.

## Appendix A — Spend cap (operator, not CC)

Manual Console steps, to be done before the gate is ever flipped:

1. Console → create dedicated workspace (`frontierview-public`).
2. Set workspace **monthly spend limit: $10**. Note the granularity honestly: the cap is monthly, so a worst-case single day can burn the full $10 — the figure is chosen as acceptable on that basis (cost model: Haiku 4.5 over bounded curated questions).
3. Create a workspace-scoped API key; set it as the Render service's `ANTHROPIC_API_KEY`. CLI and eval continue on the existing key — only the deployed public surface moves to the capped key.
4. Verify: when the limit is hit, API calls 429 — which the agent now surfaces as the `error(kind="budget")` terminal with the friendly message (Component 3), not a raw failure.
