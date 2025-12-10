# app.py
from fastapi import Depends, FastAPI
from fastapi.responses import HTMLResponse, FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

import config
import auth
from engines import mean_reversion as mr_engine
from engines import bollinger as boll_engine
from engines import trend_following as trend_engine
from engines import liquidation_hunt as liq_engine
from engines import relative_strength as rs_engine
from engines import listing_scout, listings_service
from routes import mean_reversion as mr_routes
from routes import bollinger as boll_routes
from routes import trend_following as trend_routes
from routes import relative_strength as rs_routes
from routes import trading as trading_routes
from routes import liquidation as liquidation_routes
from routes import listings as listings_routes

CURRENT_USER_OPTIONAL = auth.get_current_user_optional


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
app.include_router(rs_routes.router)
app.include_router(trading_routes.router)
app.include_router(liquidation_routes.router)
app.include_router(listings_routes.router)
app.include_router(auth.router)


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
        rs_engine.start_rs_thread()
    listings_service.start_scheduler()


@app.on_event("shutdown")
def stop_threads():
    if not config.BOT_DISABLE_THREADS:
        mr_engine.stop_bot_thread()
        boll_engine.stop_boll_thread()
        trend_engine.stop_trend_thread()
        liq_engine.stop_liquidation_thread()
        rs_engine.stop_rs_thread()
    listings_service.shutdown_scheduler()
    listing_scout.stop_scout()


# =========================
# Root HTML page
# =========================
@app.get("/", response_class=HTMLResponse)
def index(user=Depends(CURRENT_USER_OPTIONAL)):
    if not user:
        return RedirectResponse(url="/login")
    return FileResponse("static/index.html")


@app.get("/login", response_class=HTMLResponse)
def login_page():
    return FileResponse("static/login.html")
