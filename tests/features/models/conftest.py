import threading
from contextlib import contextmanager

import pytest


@pytest.fixture
def threadsafe_db(mock_db):
    lock = threading.RLock()
    original = mock_db.get_cursor

    @contextmanager
    def serialized():
        with lock:
            with original() as cursor:
                yield cursor

    mock_db.get_cursor = serialized
    yield mock_db
