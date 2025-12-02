# tests/test_api_status.py

from datetime import datetime, timedelta

from engines import mean_reversion as mr
from routes import mean_reversion as mr_routes
from database import SessionLocal, PriceSnapshot
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
        # btc, asset_a, asset_b
        return 50000.0, 0.10, 0.05  # ratio = 2

    monkeypatch.setattr(mr, "get_prices", fake_get_prices)

    # Mock balances: base 100, asset_a 10, asset_b 5
    def fake_get_free_balance(asset: str) -> float:
        mapping = {
            config.BASE_ASSET: 100.0,
            "HBAR": 10.0,
            "DOGE": 5.0,
        }
        return mapping.get(asset, 0.0)

    monkeypatch.setattr(mr, "get_free_balance_mr", fake_get_free_balance)

    r = client.get("/status")
    assert r.status_code == 200
    data = r.json()

    # Price checks
    assert data["btc"] == 50000.0
    assert data["price_a"] == 0.10
    assert data["price_b"] == 0.05
    assert data["asset_a"] == mr.bot_config.asset_a
    assert data["asset_b"] == mr.bot_config.asset_b

    # Ratio = hbar/doge = 2
    assert data["ratio"] == 0.10 / 0.05

    # Balance checks
    assert data["asset_a_balance"] == 10.0
    assert data["asset_b_balance"] == 5.0


def test_pair_history_endpoint(client):
    session = SessionLocal()
    try:
        session.query(PriceSnapshot).delete()
        now = datetime.utcnow()
        for i in range(5):
            session.add(
                PriceSnapshot(
                    ts=now - timedelta(minutes=i),
                    asset_a="HBAR",
                    asset_b="DOGE",
                    price_a=1.0 + i * 0.01,
                    price_b=0.5 + i * 0.01,
                    ratio=2 + i * 0.1,
                    zscore=float(i),
                )
            )
        session.commit()
    finally:
        session.close()

    r = client.get("/pair_history")
    assert r.status_code == 200
    payload = r.json()
    assert payload["pair"] == "HBAR/DOGE"
    assert len(payload["history"]) == 5
    assert "is_good_pair" in payload
