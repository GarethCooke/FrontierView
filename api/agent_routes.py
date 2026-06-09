"""
SSE streaming endpoint for the agent loop.

Gate: POST /agent is enabled only when AGENT_PUBLIC_ENABLED=1 (default off).
Merging this file to master is safe — with the flag unset the route returns 404.

Transport contract:
  - Each SSE frame:  event: <type>\ndata: <json>\n\n
  - `type` is also inside `data` so fetch-stream clients can dispatch without
    relying on the `event:` field (native EventSource is GET-only; 4c uses fetch).
  - Keepalive:  `: keepalive\n\n`  on 15-second queue drain timeout.
  - The stream always terminates with `run_finished`:
      normal path:  ... → `final_answer` → `run_finished` (turns=N)
      error path:   ... → `error(kind="loop")` → `run_finished` (turns=None)
  - The stream always closes cleanly — the worker guarantees a sentinel in finally.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from collections.abc import AsyncGenerator

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from agent.events import ErrorEvent, EventSink, RunFinished, _Base
from agent.loop import run as _agent_run

router = APIRouter()

_SENTINEL = object()


# ---------------------------------------------------------------------------
# Request model
# ---------------------------------------------------------------------------

class AgentRequest(BaseModel):
    question: str
    config: dict | None = None


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
            queue.put_nowait(ErrorEvent(seq=base + 1, t=time.time(), kind="loop", message=str(exc)))
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
# Endpoint
# ---------------------------------------------------------------------------

@router.post("/agent")
async def agent_stream(payload: AgentRequest) -> StreamingResponse:
    enabled = os.getenv("AGENT_PUBLIC_ENABLED", "").lower() in ("1", "true", "yes")
    if not enabled:
        raise HTTPException(status_code=404, detail="Agent endpoint is disabled.")

    return StreamingResponse(
        _sse_stream(payload.question),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
