import config
from engines.bollinger import get_symbol_price_boll

def test_bollinger_price_fetch_accepts_positional_lambda(monkeypatch):
    """
    Ensure we support test lambdas that define positional-only get_symbol_ticker().
    This reproduces the exact situation that caused a 500 in /boll_status.
    """
    # Fake ticker response
    def fake_get_ticker(symbol):
        return {"symbol": symbol, "price": "0.12345"}

    monkeypatch.setattr(config.boll_client, "get_symbol_ticker", fake_get_ticker)

    price = get_symbol_price_boll("BTCUSDT")
    assert price == 0.12345

def test_bollinger_price_fetch_keyword(monkeypatch):
    def fake_keyword(*, symbol):
        return {"symbol": symbol, "price": "2.50"}

    monkeypatch.setattr(config.boll_client, "get_symbol_ticker", fake_keyword)

    price = get_symbol_price_boll("BTCUSDT")
    assert price == 2.50
