"""
SSE streaming endpoint for the agent loop.

Gate: POST /agent and GET /agent/questions are enabled only when
AGENT_PUBLIC_ENABLED=1 (default off).  Merging this file to master is safe —
with the flag unset both routes return 404.

Demo-mode guardrails (Phase 4b):
  - POST /agent accepts only {"question_id": <id>} where the ID is in the
    curated-question allowlist.  extra="forbid" means any field named "text",
    "prompt", "question", or anything else is a 422 before the handler runs —
    there is no field a prompt can travel in.
  - Unknown question_id → 404 JSON.  Unknown/extra field or missing
    question_id → 422 (Pydantic default).  Both are plain JSON pre-stream.
  - Per-IP rate limiting: 5 req/min and 50 req/day on POST /agent only.
    GET /agent/questions is exempt (static, no LLM cost).
    Rejection is 429 + Retry-After as plain JSON before the stream opens.
  - Demo turn/size limits are the existing loop bounds (`MAX_ITERS` /
    `MAX_TOKENS` — defined in agent/config.py, applied in agent/loop.py) and
    are unchanged here.  A tighter public-only cap would risk truncating a
    curated question that legitimately needs those turns.

Transport contract (unchanged from 4a):
  - Each SSE frame:  event: <type>\\ndata: <json>\\n\\n
  - `type` is also inside `data` so fetch-stream clients can dispatch without
    relying on the `event:` field (native EventSource is GET-only; 4c uses fetch).
  - Keepalive:  `: keepalive\\n\\n`  on 15-second queue drain timeout.
  - The stream always terminates with `run_finished`:
      normal path:   ... → `final_answer` → `run_finished(turns=N)`
      loop-error:    ... → `error(kind="loop")` → `run_finished(turns=None)`
      budget-error:  ... → `error(kind="budget")` → `run_finished(turns=None)`
  - Pre-stream rejections (gate/allowlist/rate-limit) are plain JSON HTTP
    responses — never in-stream error frames.
  - The stream always closes cleanly — the worker guarantees a sentinel in finally.
"""
from __future__ import annotations

import asyncio
import os
import time
from collections.abc import AsyncGenerator

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, ConfigDict

from agent.eval.questions.curated import CURATED_QUESTIONS
from agent.events import ErrorEvent, RunFinished, _Base
from agent.llm import ProviderBudgetError
from agent.loop import run as _agent_run
from api.rate_limiter import RateLimiter

router = APIRouter()

_SENTINEL = object()

# Locked wording from Phase 4b brief — do not paraphrase.
_BUDGET_MESSAGE = (
    "The demo's usage budget has been reached for now — likely the monthly cap. "
    "It resets at the start of next month; the rest of FrontierView works as "
    "normal in the meantime."
)

# ---------------------------------------------------------------------------
# Allowlist — single-sourced from CURATED_QUESTIONS; no copied question strings
# ---------------------------------------------------------------------------

ALLOWED: dict[str, str] = {q.id: q.text for q in CURATED_QUESTIONS}

# ---------------------------------------------------------------------------
# Rate limiter — module-level singleton; per-process, resets on spin-down
# ---------------------------------------------------------------------------

_rate_limiter = RateLimiter()


# ---------------------------------------------------------------------------
# Request model
# ---------------------------------------------------------------------------

class DemoRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question_id: str


# ---------------------------------------------------------------------------
# Queue-based sink: bridges the blocking worker thread → async event loop
# ---------------------------------------------------------------------------

class _QueueSink:
    def __init__(self, loop: asyncio.AbstractEventLoop, queue: asyncio.Queue) -> None:
        self._loop = loop
        self._queue = queue
        self._last_seq = -1

    def emit(self, event: _Base) -> None:
        self._last_seq = event.seq
        self._loop.call_soon_threadsafe(self._queue.put_nowait, event)

    @property
    def last_seq(self) -> int:
        return self._last_seq


# ---------------------------------------------------------------------------
# Budget-error classifier
# ---------------------------------------------------------------------------

def _is_budget_exhausted(exc: BaseException) -> bool:
    """True when a ProviderBudgetError appears anywhere in the cause chain.

    The provider-specific 429 classification lives in agent/llm.py; here we
    only walk __cause__ for the provider-agnostic signal, so a re-wrap of the
    chain can't silently degrade the budget terminal back to kind="loop".

    The ``seen`` guard is cheap cycle insurance: cause chains shouldn't cycle,
    but a future refactor shouldn't be able to hang the worker on this walk.
    """
    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        if isinstance(cur, ProviderBudgetError):
            return True
        seen.add(id(cur))
        cur = cur.__cause__
    return False


# ---------------------------------------------------------------------------
# SSE generator
# ---------------------------------------------------------------------------

async def _sse_stream(question: str) -> AsyncGenerator[str, None]:
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[object] = asyncio.Queue()
    sink = _QueueSink(loop, queue)

    async def _worker() -> None:
        try:
            await asyncio.to_thread(_agent_run, question, event_sink=sink)
        except Exception as exc:
            base = sink.last_seq
            if _is_budget_exhausted(exc):
                kind: str = "budget"
                message: str = _BUDGET_MESSAGE
            else:
                kind = "loop"
                message = str(exc)
            queue.put_nowait(ErrorEvent(seq=base + 1, t=time.time(), kind=kind, message=message))
            queue.put_nowait(RunFinished(seq=base + 2, t=time.time(), turns=None))
        finally:
            queue.put_nowait(_SENTINEL)

    task = asyncio.create_task(_worker())

    try:
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=15.0)
            except asyncio.TimeoutError:
                yield ": keepalive\n\n"
                continue

            if item is _SENTINEL:
                break

            # item is an AgentEvent Pydantic model
            data = item.model_dump_json()
            yield f"event: {item.type}\ndata: {data}\n\n"
    finally:
        await task


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _gate_enabled() -> bool:
    return os.getenv("AGENT_PUBLIC_ENABLED", "").lower() in ("1", "true", "yes")


def _client_ip(request: Request) -> str:
    """First hop of X-Forwarded-For (Render proxy); falls back to direct host."""
    xff = request.headers.get("X-Forwarded-For", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/agent/questions")
async def list_questions() -> Response:
    """Return all curated questions available to the demo UI (exempt from rate limiting)."""
    if not _gate_enabled():
        raise HTTPException(status_code=404, detail="Agent endpoint is disabled.")
    payload = [{"id": qid, "text": text} for qid, text in ALLOWED.items()]
    return JSONResponse(content=payload)


@router.post("/agent")
async def agent_stream(payload: DemoRequest, request: Request) -> Response:
    if not _gate_enabled():
        raise HTTPException(status_code=404, detail="Agent endpoint is disabled.")

    ip = _client_ip(request)
    allowed, retry_after = _rate_limiter.check(ip)
    if not allowed:
        return JSONResponse(
            status_code=429,
            content={"error": "rate limited"},
            headers={"Retry-After": str(retry_after)},
        )

    question_text = ALLOWED.get(payload.question_id)
    if question_text is None:
        return JSONResponse(status_code=404, content={"error": "unknown question_id"})

    return StreamingResponse(
        _sse_stream(question_text),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
