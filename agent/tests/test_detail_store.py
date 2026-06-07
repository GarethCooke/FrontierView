"""Tests for agent.detail_store — FIFO cap enforcement and retrieval (M3)."""
from agent.detail_store import _MAX_ENTRIES, _store, clear, get, put


def setup_function():
    clear()


def test_store_bounded_at_cap():
    """Inserting more than _MAX_ENTRIES keeps the store at exactly the cap."""
    for i in range(_MAX_ENTRIES + 10):
        put({"i": i})
    assert len(_store) == _MAX_ENTRIES


def test_most_recent_entries_retrievable():
    """After overflow the most-recent _MAX_ENTRIES keys remain; the oldest are evicted."""
    keys = [put({"i": i}) for i in range(_MAX_ENTRIES + 10)]
    # Oldest 10 must be gone
    for key in keys[:10]:
        assert get(key) is None, f"Expected key {key} to be evicted"
    # Most-recent _MAX_ENTRIES must still be present
    for key in keys[10:]:
        assert get(key) is not None, f"Expected key {key} to be retrievable"


def test_put_returns_retrievable_key():
    key = put({"hello": "world"})
    assert get(key) == {"hello": "world"}


def test_get_missing_key_returns_none():
    assert get("no-such-key") is None
