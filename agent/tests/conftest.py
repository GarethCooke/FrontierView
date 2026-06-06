import pytest

from agent import detail_store


@pytest.fixture(autouse=True)
def _clear_detail_store():
    detail_store.clear()
    yield
    detail_store.clear()
