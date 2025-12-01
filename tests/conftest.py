import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# Make sure the repo root is on PYTHONPATH so `import app` works in CI
ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


# Make sure the app does not start real bot threads in tests
os.environ.setdefault("BOT_DISABLE_THREADS", "1")

# Provide dummy API keys so app.create_mr_client() doesn't explode on import
os.environ.setdefault("BINANCE_TESTNET_API_KEY", "dummy")
os.environ.setdefault("BINANCE_TESTNET_API_SECRET", "dummy")
os.environ.setdefault("BINANCE_MAINNET_API_KEY", "dummy")
os.environ.setdefault("BINANCE_MAINNET_API_SECRET", "dummy")
os.environ.setdefault("BINANCE_BOL_MAINNET_API_KEY", "dummy")
os.environ.setdefault("BINANCE_BOL_MAINNET_API_SECRET", "dummy")

from fastapi.testclient import TestClient
import app


@pytest.fixture(autouse=True)
def _reset_histories():
    """
    Auto-run fixture: clear global histories between tests so they
    don't bleed into each other.
    """
    app.ratio_history.clear()
    app.boll_price_history.clear()
    app.boll_ts_history.clear()
    yield
    app.ratio_history.clear()
    app.boll_price_history.clear()
    app.boll_ts_history.clear()


@pytest.fixture
def client():
    return TestClient(app.app)
