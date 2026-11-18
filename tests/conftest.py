import pytest

from agora.store.sqlite import SqliteStore


@pytest.fixture
def open_store():
    """Opens stores (in memory by default) and closes them when the test ends, so no connection leaks."""
    stores = []

    def open(path=":memory:"):
        store = SqliteStore(path)
        stores.append(store)
        return store

    yield open
    for store in stores:
        store.close()
