# tests/test_bollinger_pricefetch.py
import pytest

import config
from engines import bollinger as eng


def test_bollinger_price_fetch_accepts_positional_lambda(monkeypatch):
    """
    Ensure get_symbol_price_boll works when the underlying client
    defines get_symbol_ticker(symbol) with a positional parameter
    (this is how the real Binance client behaves).
    """

    class FakeClientPositional:
        def get_symbol_ticker(self, symbol):
            # Simulate Binance response
            return {"symbol": symbol, "price": "0.12345"}

    # Replace the whole boll_client with our fake one
    monkeypatch.setattr(config, "boll_client", FakeClientPositional(), raising=False)

    price = eng.get_symbol_price_boll("HBARUSDC")
    assert price == pytest.approx(0.12345)


def test_bollinger_price_fetch_keyword(monkeypatch):
    """
    Ensure get_symbol_price_boll also works when get_symbol_ticker
    is defined with a keyword-only parameter: get_symbol_ticker(*, symbol).
    This mimics the lambda we used in tests that previously caused the
    'takes 1 positional argument but 2 were given' TypeError.
    """

    class FakeClientKeyword:
        def get_symbol_ticker(self, *, symbol):
            return {"symbol": symbol, "price": "2.50"}

    monkeypatch.setattr(config, "boll_client", FakeClientKeyword(), raising=False)

    price = eng.get_symbol_price_boll("BTCUSDC")
    assert price == pytest.approx(2.50)
