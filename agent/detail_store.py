"""
Out-of-band store for bulky tool payloads (per-bin schedules, full frontier
curves). Tools return a small in-band ``summary`` plus a ``detail_id`` key into
this store, keeping large arrays out of the model transcript and the SSE stream.
"""
from __future__ import annotations

import threading
import uuid
from collections import OrderedDict

_MAX_ENTRIES = 256

# Process-global payload store, content-addressed by random UUID so entries
# never collide across concurrent requests. The public /agent endpoint runs
# each loop on its own worker thread (asyncio.to_thread), so put() can be
# called concurrently — the lock guards the check-then-evict against the
# OrderedDict being mutated from two threads at once.
_lock = threading.Lock()
_store: OrderedDict[str, dict] = OrderedDict()


def put(payload: dict) -> str:
    key = str(uuid.uuid4())
    with _lock:
        _store[key] = payload
        if len(_store) > _MAX_ENTRIES:
            _store.popitem(last=False)  # FIFO: evict oldest entry
    return key


def get(key: str) -> dict | None:
    with _lock:
        return _store.get(key)


def clear() -> None:
    """Clear all stored payloads (for testing)."""
    with _lock:
        _store.clear()
