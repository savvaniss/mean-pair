from datetime import datetime
from decimal import Decimal, ROUND_DOWN, InvalidOperation
from typing import List

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import config
from database import SessionLocal, Trade
from services.exchange import ExchangeError

router = APIRouter()


class BalanceItem(BaseModel):
    asset: str
    free: float
    locked: float


class AccountSummary(BaseModel):
    account: str
    use_testnet: bool
    balances: List[BalanceItem]
    error: str | None = None


class ManualOrderRequest(BaseModel):
    account: str  # "mr" or "boll"
    use_testnet: bool
    symbol: str
    side: str  # BUY / SELL
    qty_base: float | None = None
    qty_quote: float | None = None


class ManualOrderResponse(BaseModel):
    status: str
    account: str
    symbol: str
    side: str
    qty_executed: float
    price_used: float
    notional: float
    quote_asset: str
    is_testnet: bool


def _client_for_account(account: str, use_testnet: bool):
    if account == "mr":
        return config.create_mr_client(use_testnet)
    if account == "boll":
        return config.create_boll_client(use_testnet)
    raise HTTPException(status_code=400, detail="account must be 'mr' or 'boll'")


def _adjust_quantity(info: dict, qty: float, *, client=None, symbol: str | None = None) -> float:
    symbol_for_precision = info.get("symbol") or symbol

    # First align with ccxt precision to reduce floating drift before step rounding
    if client and symbol_for_precision:
        try:
            qty = float(client.amount_to_precision(symbol_for_precision, qty))
        except Exception:
            pass

    lot = next(
        (
            f
            for f in info.get("filters", [])
            if f.get("filterType") in ("MARKET_LOT_SIZE", "LOT_SIZE")
        ),
        None,
    )
    if not lot:
        return qty

    step = lot.get("stepSize") or lot.get("step_size") or 0
    min_qty = lot.get("minQty") or lot.get("min_qty") or 0
    max_qty = lot.get("maxQty") or lot.get("max_qty") or 0

    try:
        step_dec = Decimal(str(step))
        qty_dec = Decimal(str(qty))
        min_dec = Decimal(str(min_qty))
        max_dec = Decimal(str(max_qty)) if float(max_qty) > 0 else None
    except InvalidOperation:
        return float(qty)

    if step_dec <= 0:
        adjusted = qty_dec
    else:
        # floor to the nearest step to avoid violating MARKET_LOT_SIZE
        adjusted = (qty_dec // step_dec) * step_dec
        adjusted = adjusted.quantize(step_dec, rounding=ROUND_DOWN)

    if adjusted < min_dec:
        return 0.0
    if max_dec is not None and adjusted > max_dec:
        adjusted = max_dec

    # Honor explicit amount precision if provided by the market info
    amount_precision = info.get("precision", {}).get("amount")
    if amount_precision is not None and amount_precision >= 0:
        try:
            precision_dec = Decimal("1") / (Decimal("10") ** int(amount_precision))
            adjusted = adjusted.quantize(precision_dec, rounding=ROUND_DOWN)
        except Exception:
            pass

    if client and symbol_for_precision:
        try:
            adjusted = Decimal(str(client.amount_to_precision(symbol_for_precision, float(adjusted))))
        except Exception:
            pass

    return float(adjusted)


def _min_notional(info: dict) -> float:
    min_notional_filter = next(
        (f for f in info.get("filters", []) if f.get("filterType") == "MIN_NOTIONAL"),
        None,
    )
    return (
        float(
            min_notional_filter.get("minNotional")
            or min_notional_filter.get("notional")
            or 0.0
        )
        if min_notional_filter
        else 0.0
    )


def _balances_for_account(account: str, use_testnet: bool) -> AccountSummary:
    try:
        client = _client_for_account(account, use_testnet)
        if not client:
            return AccountSummary(
                account=account,
                use_testnet=use_testnet,
                balances=[],
                error="Binance client unavailable",
            )

        acc = client.get_account()
        balances = [
            BalanceItem(asset=b["asset"], free=float(b["free"]), locked=float(b["locked"]))
            for b in acc.get("balances", [])
            if float(b.get("free", 0)) > 0 or float(b.get("locked", 0)) > 0
        ]
        return AccountSummary(account=account, use_testnet=use_testnet, balances=balances)
    except ExchangeError as e:
        return AccountSummary(
            account=account,
            use_testnet=use_testnet,
            balances=[],
            error=f"Exchange error: {e}",
        )
    except Exception as e:
        return AccountSummary(
            account=account, use_testnet=use_testnet, balances=[], error=str(e)
        )


@router.get("/trading/balances", response_model=List[AccountSummary])
def trading_balances(use_testnet: bool | None = None):
    env = config.USE_TESTNET if use_testnet is None else use_testnet
    return [
        _balances_for_account("mr", env),
        _balances_for_account("boll", env),
    ]


@router.post("/trading/order", response_model=ManualOrderResponse)
def trading_order(req: ManualOrderRequest):
    if (req.qty_base or 0) <= 0 and (req.qty_quote or 0) <= 0:
        raise HTTPException(status_code=400, detail="qty_base or qty_quote must be > 0")

    try:
        client = _client_for_account(req.account, req.use_testnet)
        if not client:
            raise HTTPException(status_code=503, detail="Binance client unavailable")

        side = req.side.upper()
        if side not in ("BUY", "SELL"):
            raise HTTPException(status_code=400, detail="side must be BUY or SELL")

        info = client.get_symbol_info(req.symbol)
        base_asset = info.get("baseAsset", req.symbol.rstrip("USDT"))
        quote_asset = info.get("quoteAsset", "")
        if not quote_asset and base_asset:
            quote_asset = req.symbol.replace(base_asset, "", 1)

        ticker = client.get_symbol_ticker(symbol=req.symbol)
        price = float(ticker["price"])

        qty_requested = req.qty_base if (req.qty_base or 0) > 0 else (req.qty_quote or 0) / price
        qty_adj = _adjust_quantity(info, qty_requested, client=client, symbol=req.symbol)
        if qty_adj <= 0:
            raise HTTPException(
                status_code=400,
                detail="Quantity too small after LOT_SIZE adjustment",
            )

        # Final precision pass to avoid floating remainders slipping through
        try:
            qty_adj = float(client.amount_to_precision(req.symbol, qty_adj))
        except Exception:
            pass

        min_notional = _min_notional(info)
        if qty_adj * price < min_notional:
            raise HTTPException(status_code=400, detail="Order below MIN_NOTIONAL")
        order = client.order_market(symbol=req.symbol, side=side, quantity=qty_adj)
        if not order:
            raise HTTPException(status_code=500, detail="Order failed")

        session = SessionLocal()
        try:
            executed_qty = float(order.get("executedQty", order.get("filled", qty_adj)))
            quote_used = float(
                order.get("cummulativeQuoteQty", order.get("cost", executed_qty * price))
            )
            if quote_used < min_notional:
                raise HTTPException(status_code=400, detail="Fill below MIN_NOTIONAL")

            fills = order.get("fills", []) or order.get("trades", []) or []
            fee_quote = 0.0
            for f in fills:
                try:
                    if f.get("commissionAsset") == quote_asset:
                        fee_quote += float(f.get("commission", 0))
                    elif f.get("fee", {}).get("currency") == quote_asset:
                        fee_quote += float(f.get("fee", {}).get("cost", 0))
                except Exception:
                    continue

            avg_price = quote_used / executed_qty if executed_qty > 0 else price
            tr = Trade(
                ts=datetime.utcnow(),
                side=f"{side} {req.symbol} (manual trading desk)",
                from_asset=base_asset,
                to_asset=quote_asset,
                qty_from=executed_qty,
                qty_to=quote_used,
                price=avg_price,
                fee=fee_quote,
                pnl_usd=0.0,
                is_testnet=int(req.use_testnet),
            )
            session.add(tr)
            session.commit()
        finally:
            session.close()

        return ManualOrderResponse(
            status="ok",
            account=req.account,
            symbol=req.symbol,
            side=side,
            qty_executed=executed_qty,
            price_used=avg_price,
            notional=quote_used,
            quote_asset=quote_asset,
            is_testnet=req.use_testnet,
        )

    except HTTPException:
        raise
    except ExchangeError as e:
        raise HTTPException(status_code=400, detail=f"Exchange error: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
