import pytest
import gc
from django.db import connections


@pytest.fixture(autouse=True)
def close_all_db_connections():
    yield
    for conn in connections.all():
        try:
            conn.close()
        except Exception:
            pass


def pytest_sessionfinish(session: pytest.Session, exitstatus: pytest.ExitCode) -> None:
    """At the end of the test session, close connections and force GC."""
    try:
        from django.db import connections

        for conn in connections.all():
            try:
                conn.close()
            except Exception:
                pass
    except ImportError:
        pass
    gc.collect()  # Force garbage collection to clean up any lingering objects
