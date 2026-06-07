from __future__ import annotations

import uuid
from collections import OrderedDict

_MAX_ENTRIES = 256

# TODO: per-request scoping + locking when the /agent endpoint lands (Phase 4)
_store: OrderedDict[str, dict] = OrderedDict()


def put(payload: dict) -> str:
    key = str(uuid.uuid4())
    _store[key] = payload
    if len(_store) > _MAX_ENTRIES:
        _store.popitem(last=False)  # FIFO: evict oldest entry
    return key


def get(key: str) -> dict | None:
    return _store.get(key)


def clear() -> None:
    """Clear all stored payloads (for testing)."""
    _store.clear()
