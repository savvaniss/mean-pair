# tests/test_api_status.py

from engines import mean_reversion as mr
from routes import mean_reversion as mr_routes
import config


def test_get_config_roundtrip(client):
    r = client.get("/config")
    assert r.status_code == 200
    data = r.json()

    # Basic sanity of returned config
    assert "poll_interval_sec" in data
    assert "use_testnet" in data


def test_status_uses_mocked_prices_and_balances(monkeypatch, client):
    # Mock get_prices → deterministic
    def fake_get_prices():
        # btc, hbar, doge
        return 50000.0, 0.10, 0.05  # ratio = 2

    monkeypatch.setattr(mr, "get_prices", fake_get_prices)

    # Mock balances: base 100, hbar 10, doge 5, usdc 50
    def fake_get_free_balance(asset: str) -> float:
        mapping = {
            config.BASE_ASSET: 100.0,
            "HBAR": 10.0,
            "DOGE": 5.0,
            "USDC": 50.0,
        }
        return mapping.get(asset, 0.0)

    monkeypatch.setattr(mr, "get_free_balance_mr", fake_get_free_balance)

    r = client.get("/status")
    assert r.status_code == 200
    data = r.json()

    # Price checks
    assert data["btc"] == 50000.0
    assert data["hbar"] == 0.10
    assert data["doge"] == 0.05

    # Ratio = hbar/doge = 2
    assert data["ratio"] == 0.10 / 0.05

    # Balance checks
    assert data["hbar_balance"] == 10.0
    assert data["doge_balance"] == 5.0
