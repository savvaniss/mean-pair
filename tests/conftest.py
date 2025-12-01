# tests/conftest.py
import os
import pytest

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
