# CC Brief — Agent Phase 4a · Streaming spine

**Branch:** `feature/agent-phase4a` (off `master`)
**Lands in:** `agent/docs/briefs/cc_brief_agent_phase4a_streaming_spine.md`
**Depends on:** Phase 3 merged (eval baseline locked).
**Blocks:** 4b (guardrails), 4c (UI tab) — both need the event stream to exist.

---

## Goal

Add a streaming `POST /agent` SSE endpoint to the existing FastAPI service, fed by an **optional event-sink** on the agent loop. The sink is pure observation: when it is `None` (CLI + eval paths), behaviour must be **byte-identical** to current `master`. No model behaviour, tool execution, retry, compaction, or ordering changes.

This is the spine only. It must merge to `master` **without exposing a live, cost-bearing public endpoint** — gate it behind an off-by-default env flag (see Scope/out).

---

## Locked constraints (inherit; do not relitigate)

- Hosted brain Haiku 4.5 stays the provider (eval baseline ran on it). No provider swap here.
- Two-part result shape holds: **only the in-band compact summary** enters the stream. The out-of-band UUID-keyed detail store (bin/frontier arrays) is **never** streamed — same discipline as "numeric groundedness checks only model-visible summaries."
- Pydantic is single-source for the new event schema, as for tool schemas.
- DRY equality test stays green; no model logic copied.

---

## Scope — in

1. **Event model** (`agent/events.py`, suggested — follow existing layout).
   Pydantic models, discriminated on a `type` literal, each with a monotonic `seq: int` and `t: float` (epoch). JSON-serialisable. Event types:
   - `run_started` — `{question, config_summary}`
   - `assistant_text` — model turn text (full block is fine; deltas optional, not required for 4a)
   - `tool_call` — `{name, input}` (the **validated** Pydantic input, serialised)
   - `tool_result` — `{name, summary}` — the in-band summary string **only**; never the detail store
   - `compaction` — `{before_tokens, after_tokens}` (emit when compaction fires; good for the "watch it think" demo)
   - `error` — `{kind: "tool"|"loop", message}` (user-facing message; tool-errors mirror what already goes back to the model, loop-errors are the terminal failure)
   - `final_answer` — `{answer}`
   - `run_finished` — `{turns, usage?}` terminal; include token/cost totals only if already on hand — do not compute anything new on the flag-off path
   A `RecordingSink` and the `EventSink` protocol live here too (see below).

2. **EventSink interface + refactor.**
   Minimal protocol: `class EventSink(Protocol): def emit(self, event: AgentEvent) -> None: ...`
   Add `event_sink: EventSink | None = None` to the loop's public entrypoint. Guard **every** emit with `if event_sink is not None:` so the `None` path constructs and emits nothing. Emit points, at the natural observation seams of the reason-act-observe loop:
   - after the model turn → `assistant_text` (+ `tool_call` per requested call)
   - after each tool executes → `tool_result` (summary only)
   - when compaction triggers → `compaction`
   - on tool-error / loop-error → `error`
   - on terminal answer → `final_answer` then `run_finished`
   **Invariant:** emits read already-computed values and must not alter control flow, ordering, retry budgets, the transcript, or the returned result object. The byte-identical guarantee is only about the `None` path; the sink path may do extra work freely.

3. **SSE transport + endpoint** (`api/agent_routes.py` router, included by `api/main.py` — suggested; defer to existing structure).
   - `POST /agent`, JSON body `{question: str, config?: {...}}`, returns `text/event-stream`.
   - The loop is blocking/in-process. Run it via `asyncio.to_thread(run_agent, question, sink)`. The sink is a `QueueSink` that, on `emit`, does `loop.call_soon_threadsafe(q.put_nowait, event)` onto an `asyncio.Queue` captured from the running loop. On completion/exception, enqueue a sentinel in a `finally`.
   - SSE generator drains the queue: `await asyncio.wait_for(q.get(), timeout=15)`; on `TimeoutError` yield a keepalive comment (`: keepalive\n\n`) to defeat Render/proxy idle timeouts; on sentinel, stop.
   - Frame format: `event: <type>\ndata: <json>\n\n`. Put `type` inside `data` too, so a `fetch`-stream client (4c) can dispatch without relying on the `event:` field.
   - Wrap the worker in try/except: on exception emit a terminal `error` event, then sentinel. The stream must always close cleanly, never hang.

4. **Tests** (see acceptance).

## Scope — out (do not build; later phases will)

- Rate limiting, per-IP/session throttling, spend cap → **4b**.
- Demo-mode bounded-question allowlist (will reuse the curated eval set) → **4b**.
- Auth / gating, the "Ask the model" UI tab, charts → **4c**.
- Token-delta streaming, thread cancellation on client disconnect → not needed. Note disconnect as a known limitation: the worker thread runs to its bounded max-turns and the result is discarded; cost is bounded by 4b's caps. Do not engineer thread cancellation.

**Merge safety:** the endpoint sits behind `AGENT_PUBLIC_ENABLED` (default **off**). With it off, `POST /agent` returns 404/disabled. Merging 4a to `master` must not create a live public LLM endpoint.

---

## Endpoint client note (for 4c, not built here)

Native `EventSource` is GET-only; we use `POST` + body, so 4c consumes via `fetch` + `ReadableStream`, not `EventSource`. This keeps the question out of the URL query string (clean default) and supports a request body. 4a is verified with `curl -N`.

---

## Acceptance criteria (the gate)

1. **Streaming integration test:** hit `POST /agent` (flag on, in-process test client) with a fixed known question; parse frames; assert well-formed SSE and event ordering `run_started → (assistant_text / tool_call / tool_result)* → final_answer → run_finished`; assert at least one keepalive path is exercised (can stub a slow step or assert the timeout branch directly).
2. **Byte-identical (flag-off):** run the loop on a fixed question with `event_sink=None` and again with a passive `RecordingSink`; assert the returned result objects (final answer, full transcript, tool-call sequence) are **equal**. The recorded events must be a faithful projection of the run that actually happened (tool_result summaries match the real summaries; tool_call inputs match the validated inputs).
3. **Schema round-trip:** every event type serialises to JSON and re-parses to an equal model.
4. **No detail-store leakage:** assert no `tool_result` event payload contains bin/frontier arrays or detail-store contents — summaries only.
5. **Caller contract:** CLI entrypoint and eval harness call the loop with **no** sink (assert via the call sites, mirroring the eval byte-identical discipline).
6. **Error path:** a forced loop-error yields a terminal `error` event and a closed stream (no hang).
7. **Both suites green:** agent suite + FV suite on the branch. DRY equality test still green.

---

## Conventions

- `feature/` branch prefix; target `master`.
- Brief stored at `agent/docs/briefs/`.
- Pydantic single-source for events; `Protocol` for the sink.
- Match existing module layout — paths above are suggested, not prescriptive.

---

## Notes for the build report

Confirm with: the SSE frame dump from a `curl -N` run against a known question (so I can eyeball ordering), the byte-identical test result, and both suite counts. As usual I'll read the diff directly — generate it and upload — rather than a prose summary.
