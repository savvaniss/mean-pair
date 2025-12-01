# tests/test_api_status.py
import app


def test_get_config_roundtrip(client):
    r = client.get("/config")
    assert r.status_code == 200
    data = r.json()
    # a couple of key fields
    assert "poll_interval_sec" in data
    assert "use_testnet" in data


def test_status_uses_mocked_prices_and_balances(monkeypatch, client):
    # Mock get_prices → fixed deterministic numbers
    def fake_get_prices():
        # btc, hbar, doge
        return 50000.0, 0.10, 0.05  # ratio = 2

    monkeypatch.setattr(app, "get_prices", fake_get_prices)

    # Mock balances: 100 base, 10 HBAR, 5 DOGE, 50 USDC
    def fake_get_free_balance(asset: str) -> float:
        mapping = {
            app.BASE_ASSET: 100.0,
            "HBAR": 10.0,
            "DOGE": 5.0,
            "USDC": 50.0,
        }
        return mapping.get(asset, 0.0)

    monkeypatch.setattr(app, "get_free_balance_mr", fake_get_free_balance)

    r = client.get("/status")
    assert r.status_code == 200
    data = r.json()

    assert data["btc"] == 50000.0
    assert data["hbar"] == 0.10
    assert data["doge"] == 0.05
    assert data["ratio"] == 0.10 / 0.05

    # correct balances passed through
    assert data["hbar_balance"] == 10.0
    assert data["doge_balance"] == 5.0
