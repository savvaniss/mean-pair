# app.py
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles

import config
from engines import mean_reversion as mr_engine
from engines import bollinger as boll_engine
from routes import mean_reversion as mr_routes
from routes import bollinger as boll_routes

# =========================
# FastAPI app wiring
# =========================

app = FastAPI(title="Mean Reversion Bot")

# Static UI
app.mount("/static", StaticFiles(directory="static"), name="static")

# Routers (all endpoints)
app.include_router(mr_routes.router)
app.include_router(boll_routes.router)

# Start background threads unless disabled (e.g. CI)
if not config.BOT_DISABLE_THREADS:
    mr_engine.start_bot_thread()
    boll_engine.start_boll_thread()


@app.get("/", response_class=HTMLResponse)
def index():
    return FileResponse("static/index.html")


# =========================
# Backwards-compat exports
# (so tests & other code that import from `app` still work)
# =========================

# ---- Mean Reversion (MR) re-exports ----
# =========================
# Minimal safe re-exports
# (used by tests & backwards compatibility)
# =========================

# ---- Mean Reversion exports ----
from engines.mean_reversion import (
    bot_config,
    ratio_history,
    required_history_len,
    has_enough_history,
    compute_stats,
    decide_signal,
    get_state,
    get_prices,
    get_free_balance_mr,
    adjust_quantity,
    init_state_from_balances,
)

# ---- Bollinger exports ----
from engines.bollinger import (
    boll_config,
    boll_ts_history,
    boll_price_history,
    compute_ma_std_window,
)

# ---- Config exports ----
from config import (
    USE_TESTNET,
    mr_client,
    boll_client,
    mr_symbol,
    BASE_ASSET,
)
