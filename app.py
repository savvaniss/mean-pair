# app.py
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles

import config
from engines import mean_reversion as mr_engine
from engines import bollinger as boll_engine
from engines import trend_following as trend_engine
from engines import liquidation_hunt as liq_engine
from routes import mean_reversion as mr_routes
from routes import bollinger as boll_routes
from routes import trend_following as trend_routes
from routes import trading as trading_routes
from routes import liquidation as liquidation_routes


# =========================
# FastAPI application
# =========================

app = FastAPI(title="Mean Reversion & Bollinger Bots")

# Static frontend
app.mount("/static", StaticFiles(directory="static"), name="static")

# API routes
app.include_router(mr_routes.router)
app.include_router(boll_routes.router)
app.include_router(trend_routes.router)
app.include_router(trading_routes.router)
app.include_router(liquidation_routes.router)


# =========================
# Background bot threads (managed via lifespan)
# =========================


@app.on_event("startup")
def start_threads():
    if not config.BOT_DISABLE_THREADS:
        mr_engine.start_bot_thread()
        boll_engine.start_boll_thread()
        trend_engine.start_trend_thread()
        liq_engine.start_liquidation_thread()


@app.on_event("shutdown")
def stop_threads():
    if not config.BOT_DISABLE_THREADS:
        mr_engine.stop_bot_thread()
        boll_engine.stop_boll_thread()
        trend_engine.stop_trend_thread()
        liq_engine.stop_liquidation_thread()


# =========================
# Root HTML page
# =========================
@app.get("/", response_class=HTMLResponse)
def index():
    return FileResponse("static/index.html")
