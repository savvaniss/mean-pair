# tests/test_pair_health_persistence.py
from datetime import datetime, timedelta

from database import SessionLocal, PairHealth, PriceSnapshot


def _seed_snapshots(session, count: int = 40):
    now = datetime.utcnow()
    for i in range(count):
        ratio = 2.0 + (i % 5) * 0.05
        session.add(
            PriceSnapshot(
                ts=now - timedelta(minutes=i),
                asset_a="HBAR",
                asset_b="DOGE",
                price_a=1.0 + i * 0.01,
                price_b=0.5 + i * 0.01,
                ratio=ratio,
                zscore=0.1 * i,
            )
        )


def test_pair_history_persists_health_records(client):
    session = SessionLocal()
    try:
        session.query(PriceSnapshot).delete()
        session.query(PairHealth).delete()
        _seed_snapshots(session, count=12)
        session.commit()
    finally:
        session.close()

    first = client.get("/pair_history").json()
    assert len(first["health_history"]) == 1

    second = client.get("/pair_history").json()
    assert len(second["health_history"]) == 2
    assert second["health_history"][-1]["std"] >= first["health_history"][0]["std"]


def test_generate_best_config_endpoint(client):
    session = SessionLocal()
    try:
        session.query(PriceSnapshot).delete()
        session.query(PairHealth).delete()
        _seed_snapshots(session, count=60)
        session.commit()
    finally:
        session.close()

    resp = client.get("/config_best")
    assert resp.status_code == 200
    data = resp.json()

    assert data["window_size"] >= 20
    assert 1.0 <= data["z_entry"] <= 4.0
    assert data["use_ratio_thresholds"] is True
    assert data["sell_ratio_threshold"] > data["buy_ratio_threshold"]
