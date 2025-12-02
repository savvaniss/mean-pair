import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# Make sure the repo root is on PYTHONPATH so `import app` etc. works in CI
ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

# -----------------------------------------------------------------------------
# Environment for tests
# -----------------------------------------------------------------------------

# Make sure the app does not start real bot threads in tests
os.environ.setdefault("BOT_DISABLE_THREADS", "1")

# Disable real Binance clients – tests will monkeypatch the engines/config instead
os.environ.setdefault("DISABLE_BINANCE_CLIENT", "1")

# Provide dummy API keys so create_*_client won't explode on import
os.environ.setdefault("BINANCE_TESTNET_API_KEY", "dummy")
os.environ.setdefault("BINANCE_TESTNET_API_SECRET", "dummy")
os.environ.setdefault("BINANCE_MAINNET_API_KEY", "dummy")
os.environ.setdefault("BINANCE_MAINNET_API_SECRET", "dummy")
os.environ.setdefault("BINANCE_BOL_MAINNET_API_KEY", "dummy")
os.environ.setdefault("BINANCE_BOL_MAINNET_API_SECRET", "dummy")

# Import FastAPI app entrypoint
import app  # noqa: E402

# Import the new engine modules where the in-memory state lives now
from engines import mean_reversion as mr_engine  # noqa: E402
from engines import bollinger as boll_engine     # noqa: E402


@pytest.fixture(autouse=True)
def _reset_histories():
    """
    Auto-run fixture: clear global histories between tests so they
    don't bleed into each other.

    IMPORTANT: we now reset the histories from the *engine* modules,
    not attributes on `app`.
    """
    mr_engine.ratio_history.clear()
    boll_engine.boll_price_history.clear()
    boll_engine.boll_ts_history.clear()
    try:
        yield
    finally:
        mr_engine.ratio_history.clear()
        boll_engine.boll_price_history.clear()
        boll_engine.boll_ts_history.clear()


@pytest.fixture
def client():
    """
    Test client against the FastAPI instance defined in app.py
    (which now just includes the routers).
    """
    return TestClient(app.app)
