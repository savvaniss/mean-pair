import asyncio
import threading
import time
from typing import Dict, Iterable, List, Optional

try:
    import ccxt
except ImportError as exc:  # pragma: no cover - dependency missing in some envs
    raise ImportError("ccxt is required; install it with `pip install ccxt`.") from exc

_ccxtpro_import_error = None
ccxtpro = None
try:  # pragma: no cover - runtime dependency
    import ccxt.pro as ccxtpro
except Exception as exc:  # pragma: no cover - ccxt.pro not installed/licensed
    _ccxtpro_import_error = exc


class ExchangeError(Exception):
    """Raised when an exchange operation fails."""


class ExchangeClient:
    """Thin ccxt/ccxtpro wrapper with optional WebSocket ticker streaming."""

    def __init__(self, api_key: str, api_secret: str, *, testnet: bool = False):
        if ccxtpro is None:
            raise ExchangeError(
                "ccxt.pro is required for WebSocket streaming. Install the licensed "
                "`ccxtpro` package or expose ccxt.pro on your PYTHONPATH."
            ) from _ccxtpro_import_error

        if not api_key or not api_secret:
            raise ExchangeError("API key/secret required for exchange client")

        if not hasattr(ccxtpro, "binance"):
            raise ExchangeError(
                "ccxt.pro exchange class for Binance is unavailable. Ensure your ccxt.pro "
                "installation includes the Binance client."
            )

        self._rest = ccxt.binance({
            "apiKey": api_key,
            "secret": api_secret,
            "options": {"defaultType": "spot"},
            "enableRateLimit": True,
        })
        self._ws = ccxtpro.binance({
            "apiKey": api_key,
            "secret": api_secret,
            "options": {"defaultType": "spot"},
            "enableRateLimit": True,
        })

        if testnet:
            self._rest.set_sandbox_mode(True)
            self._ws.set_sandbox_mode(True)

        self._ticker_cache: Dict[str, Dict] = {}
        self._stream_symbols: List[str] = []
        self._ws_loop: Optional[asyncio.AbstractEventLoop] = None
        self._ws_thread: Optional[threading.Thread] = None
        self._ws_stop = threading.Event()
        self._cache_lock = threading.Lock()

    def _normalize_symbol(self, symbol: str) -> str:
        """Return a unified symbol without separators (e.g., BTC/USDC -> BTCUSDC)."""
        return symbol.replace("/", "") if symbol else symbol

    def _format_symbol(self, symbol: str) -> str:
        if "/" in symbol:
            return symbol
        markets = self.load_markets()
        if symbol in markets:
            return markets[symbol]["symbol"]
        for market in markets.values():
            if market.get("id") == symbol:
                return market.get("symbol", symbol)
        if len(symbol) > 4:
            return f"{symbol[:-4]}/{symbol[-4:]}"
        return symbol

    # Compatibility shim for legacy Binance-style exchange info endpoints
    def get_exchange_info(self) -> Dict:
        markets = self.load_markets()
        symbols = []
        for market in markets.values():
            raw_filters = market.get("info", {}).get("filters") or []
            symbols.append(
                {
                    "symbol": self._normalize_symbol(market.get("symbol") or market.get("id") or ""),
                    "status": "TRADING" if market.get("active", True) else "BREAK",
                    "baseAsset": market.get("base"),
                    "quoteAsset": market.get("quote"),
                    "filters": raw_filters,
                    "precision": market.get("precision", {}),
                }
            )

        return {"symbols": symbols, "timezone": "UTC"}

    # =========================
    # Market + account metadata
    # =========================
    def load_markets(self) -> Dict:
        try:
            return self._rest.load_markets()
        except Exception as exc:  # pragma: no cover - network / credentials
            raise ExchangeError(str(exc))

    def fetch_symbol_info(self, symbol: str) -> Dict:
        markets = self.load_markets()
        market = markets.get(symbol) or next(
            (m for m in markets.values() if m.get("id") == symbol or m.get("symbol") == symbol),
            None,
        )
        if not market:
            raise ExchangeError(f"Unknown symbol {symbol}")

        limits = market.get("limits", {})
        precision = market.get("precision", {})
        lot_size = limits.get("amount", {})
        price_limits = limits.get("price", {})
        raw_filters = market.get("info", {}).get("filters") or []

        # Prefer native exchange filters when they exist so MARKET_LOT_SIZE and
        # MIN_NOTIONAL values align with Binance expectations.
        filters = [
            f
            for f in raw_filters
            if f.get("filterType") in {"LOT_SIZE", "MARKET_LOT_SIZE", "MIN_NOTIONAL"}
        ]

        # Provide a reasonable fallback derived from ccxt market metadata.
        if not filters:
            step = lot_size.get("step")
            if step is None and "amount" in precision:
                step = 10 ** (-precision.get("amount", 0))
            filters = [
                {
                    "filterType": "LOT_SIZE",
                    "stepSize": str(step or 0),
                    "minQty": str(lot_size.get("min", 0)),
                    "maxQty": str(lot_size.get("max", 0) or 0),
                },
                {
                    "filterType": "MIN_NOTIONAL",
                    "minNotional": str(
                        (lot_size.get("min", 0) or 0) * (price_limits.get("min", 0) or 0)
                    ),
                },
            ]

        return {
            "symbol": symbol,
            "baseAsset": market.get("base"),
            "quoteAsset": market.get("quote"),
            "filters": filters,
            "precision": precision,
            "limits": limits,
            "symbol": market.get("symbol"),
        }

    def fetch_balances(self) -> List[Dict]:
        try:
            bal = self._rest.fetch_balance()
        except Exception as exc:  # pragma: no cover - network / credentials
            raise ExchangeError(str(exc))
        balances: List[Dict] = []
        for asset, info in bal.get("total", {}).items():
            free = float(bal.get("free", {}).get(asset, 0) or 0)
            used = float(bal.get("used", {}).get(asset, 0) or 0)
            if free == 0 and used == 0:
                continue
            balances.append({"asset": asset, "free": free, "locked": used})
        return balances

    # =========================
    # Prices / tickers
    # =========================
    def _ensure_ws_thread(self):
        if self._ws_thread and self._ws_thread.is_alive():
            return

        self._ws_loop = asyncio.new_event_loop()

        def _runner():
            asyncio.set_event_loop(self._ws_loop)
            self._ws_loop.run_until_complete(self._watch_loop())

        self._ws_thread = threading.Thread(target=_runner, daemon=True)
        self._ws_thread.start()

    async def _watch_loop(self):
        while not self._ws_stop.is_set():
            try:
                for symbol in list(self._stream_symbols):
                    ticker = await self._ws.watch_ticker(symbol)
                    with self._cache_lock:
                        self._ticker_cache[symbol] = ticker
            except Exception:
                # back off a bit on failures
                await asyncio.sleep(1)

    def set_streaming_symbols(self, symbols: Iterable[str]):
        self._stream_symbols = list(dict.fromkeys(symbols))
        if self._stream_symbols:
            self._ensure_ws_thread()

    def get_cached_ticker(self, symbol: str) -> Optional[Dict]:
        with self._cache_lock:
            return self._ticker_cache.get(symbol)

    def fetch_symbol_ticker(self, symbol: str) -> Dict:
        symbol_fmt = self._format_symbol(symbol)
        cached = self.get_cached_ticker(symbol)
        if cached:
            return {
                "symbol": self._normalize_symbol(symbol),
                "price": float(cached.get("last", cached.get("close", 0)) or 0),
                "timestamp": cached.get("timestamp", int(time.time() * 1000)),
                "source": "websocket",
            }
        try:
            ticker = self._rest.fetch_ticker(symbol_fmt)
        except Exception as exc:  # pragma: no cover - network / credentials
            raise ExchangeError(str(exc))
        return {
            "symbol": self._normalize_symbol(symbol),
            "price": float(ticker.get("last") or ticker.get("close") or 0),
            "timestamp": ticker.get("timestamp") or int(time.time() * 1000),
            "source": "rest",
        }

    def fetch_all_tickers(self) -> List[Dict]:
        try:
            tickers = self._rest.fetch_tickers()
        except Exception as exc:  # pragma: no cover - network / credentials
            raise ExchangeError(str(exc))
        return [
            {
                "symbol": self._normalize_symbol(val.get("symbol") or sym),
                "price": float(val.get("last") or val.get("close") or 0),
                "timestamp": val.get("timestamp"),
                "source": "rest",
            }
            for sym, val in tickers.items()
        ]

    # Compatibility helpers for legacy call sites
    def get_account(self) -> Dict:
        return {"balances": self.fetch_balances()}

    def get_symbol_info(self, symbol: str) -> Dict:
        return self.fetch_symbol_info(symbol)

    def get_symbol_ticker(self, symbol: str) -> Dict:
        t = self.fetch_symbol_ticker(symbol)
        return {"symbol": t["symbol"], "price": str(t["price"]), "source": t["source"]}

    def get_all_tickers(self) -> List[Dict]:
        return self.fetch_all_tickers()

    # =========================
    # Orders
    # =========================
    def amount_to_precision(self, symbol: str, amount: float) -> float:
        try:
            sym = self._format_symbol(symbol)
            return float(self._rest.amount_to_precision(sym, amount))
        except Exception as exc:  # pragma: no cover - network / credentials
            raise ExchangeError(str(exc))

    def create_market_order(self, symbol: str, side: str, amount) -> Dict:
        """Create a market order with a precision-preserved amount."""

        try:
            sym = self._format_symbol(symbol)
            amount_precise = self._rest.amount_to_precision(sym, amount)
            # Keep the precise string/decimal instead of converting to float to avoid
            # reintroducing step-size rounding errors that trigger MARKET_LOT_SIZE.
            return self._rest.create_order(sym, "market", side.lower(), amount_precise)
        except Exception as exc:  # pragma: no cover - network / credentials
            raise ExchangeError(str(exc))

    def create_market_order_quote(self, symbol: str, side: str, quote_amount) -> Dict:
        """Create a market buy using quote quantity to let the exchange size fills."""

        if side.lower() != "buy":
            raise ExchangeError("quote-based market orders are only supported for buys")

        try:
            sym = self._format_symbol(symbol)
            cost_precise = self._rest.cost_to_precision(sym, quote_amount)
            return self._rest.create_order(
                sym, "market", "buy", None, {"quoteOrderQty": cost_precise}
            )
        except Exception as exc:  # pragma: no cover - network / credentials
            raise ExchangeError(str(exc))

    def order_market(self, symbol: str, side: str, quantity: float) -> Dict:
        return self.create_market_order(symbol, side, quantity)

    def create_order(self, symbol: str, side: str, type: str, quantity: float) -> Dict:
        try:
            sym = self._format_symbol(symbol)
            return self._rest.create_order(sym, type.lower(), side.lower(), quantity)
        except Exception as exc:  # pragma: no cover - network / credentials
            raise ExchangeError(str(exc))

    def get_klines(self, symbol: str, interval: str, limit: int = 500):
        try:
            sym = self._format_symbol(symbol)
            ohlcv = self._rest.fetch_ohlcv(sym, timeframe=interval, limit=limit)
        except Exception as exc:  # pragma: no cover - network / credentials
            raise ExchangeError(str(exc))
        return [
            [int(c[0]), float(c[1]), float(c[2]), float(c[3]), float(c[4])] for c in ohlcv
        ]

    # =========================
    # OHLCV
    # =========================
    def fetch_ohlcv(self, symbol: str, timeframe: str, since: Optional[int] = None, limit: int = 500):
        try:
            return self._rest.fetch_ohlcv(symbol, timeframe=timeframe, since=since, limit=limit)
        except Exception as exc:  # pragma: no cover - network / credentials
            raise ExchangeError(str(exc))

    # =========================
    # Cleanup
    # =========================
    def close(self):
        self._ws_stop.set()
        if self._ws_loop:
            self._ws_loop.call_soon_threadsafe(self._ws_loop.stop)
        if self._ws_thread:
            self._ws_thread.join(timeout=2)
        try:
            self._rest.close()
        finally:
            try:
                self._ws.close()
            except Exception:
                pass
