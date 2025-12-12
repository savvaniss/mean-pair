"""WebSocket endpoints for streaming dashboard data."""

from __future__ import annotations

import asyncio
from typing import Any, Dict

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

import auth
from routes import mean_reversion as mr_routes

router = APIRouter()


async def _gather_mean_reversion_snapshot() -> Dict[str, Any]:
    """Collect a lightweight snapshot for the mean reversion board."""

    status = mr_routes.get_status()
    history = mr_routes.get_history(limit=200)
    trades = mr_routes.list_trades(limit=50)

    return {
        "status": status.dict(),
        "history": history,
        "trades": trades,
    }


@router.websocket("/ws/dashboard")
async def dashboard_stream(ws: WebSocket) -> None:
    """Continuously stream dashboard data to authenticated clients."""

    token = ws.cookies.get(auth.SESSION_COOKIE_NAME)
    username = auth._get_username_for_token(token)
    if not username:
        await ws.close(code=4401)
        return

    await ws.accept()

    try:
        while True:
            payload: Dict[str, Any] = {"type": "dashboard_snapshot"}

            try:
                payload["mean_reversion"] = await _gather_mean_reversion_snapshot()
            except Exception as exc:  # pragma: no cover - defensive
                payload["mean_reversion_error"] = str(exc)

            await ws.send_json(payload)
            await asyncio.sleep(3)
    except WebSocketDisconnect:
        return
