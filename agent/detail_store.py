from __future__ import annotations

import uuid

_store: dict[str, dict] = {}


def put(payload: dict) -> str:
    key = str(uuid.uuid4())
    _store[key] = payload
    return key


def get(key: str) -> dict | None:
    return _store.get(key)


def clear() -> None:
    """Clear all stored payloads (for testing)."""
    _store.clear()
