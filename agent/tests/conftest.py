import pytest

from agent import detail_store


@pytest.fixture(autouse=True)
def _clear_detail_store():
    detail_store.clear()
    yield
    detail_store.clear()


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """Replace the module-level rate limiter with a fresh instance before each test."""
    try:
        from api import agent_routes
        from api.rate_limiter import RateLimiter
        original = agent_routes._rate_limiter
        agent_routes._rate_limiter = RateLimiter()
        yield
        agent_routes._rate_limiter = original
    except ImportError:
        yield
