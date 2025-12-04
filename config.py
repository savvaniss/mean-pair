# config.py
import os
from typing import Optional

from dotenv import load_dotenv
from binance.client import Client

load_dotenv()

# =========================
# ENV / CONFIG
# =========================

# --- Mean-reversion (MR) credentials ---
MR_TESTNET_API_KEY = os.getenv("BINANCE_TESTNET_API_KEY")
MR_TESTNET_API_SECRET = os.getenv("BINANCE_TESTNET_API_SECRET")

MR_MAINNET_API_KEY = os.getenv("BINANCE_MAINNET_API_KEY")
MR_MAINNET_API_SECRET = os.getenv("BINANCE_MAINNET_API_SECRET")

# --- Bollinger credentials (separate account / sub-account) ---
# If not set, they fall back to the MR keys.
BOLL_TESTNET_API_KEY = os.getenv("BINANCE_BOLL_TESTNET_API_KEY", MR_TESTNET_API_KEY)
BOLL_TESTNET_API_SECRET = os.getenv(
    "BINANCE_BOLL_TESTNET_API_SECRET", MR_TESTNET_API_SECRET
)

BOLL_MAINNET_API_KEY = os.getenv("BINANCE_BOLL_MAINNET_API_KEY")
BOLL_MAINNET_API_SECRET = os.getenv("BINANCE_BOLL_MAINNET_API_SECRET")

# default env when the app starts: "testnet" or "mainnet"
DEFAULT_ENV = os.getenv("BINANCE_DEFAULT_ENV", "testnet").lower()
if DEFAULT_ENV not in ("testnet", "mainnet"):
    raise RuntimeError("BINANCE_DEFAULT_ENV must be 'testnet' or 'mainnet'")

# Base asset you conceptually hold when "neutral"
BASE_ASSET = os.getenv("BASE_ASSET", "USDC").upper()

AUTO_START = os.getenv("BOT_AUTO_START", "false").lower() == "true"

# Which quote assets to use for MR prices / symbols
TESTNET_QUOTE = os.getenv("BINANCE_TESTNET_QUOTE", "USDT").upper()
MAINNET_QUOTE = os.getenv("BINANCE_MAINNET_QUOTE", "USDC").upper()

# CI / tests: disable real Binance client creation
DISABLE_BINANCE_CLIENT = os.getenv("DISABLE_BINANCE_CLIENT", "0") == "1"
BOT_DISABLE_THREADS = os.getenv("BOT_DISABLE_THREADS", "0") == "1"

# Global env flags (tracked per bot)
USE_TESTNET: bool = DEFAULT_ENV == "testnet"  # legacy default / manual trading
MR_USE_TESTNET: bool = DEFAULT_ENV == "testnet"
BOLL_USE_TESTNET: bool = DEFAULT_ENV == "testnet"

# Global clients (will be initialised below)
mr_client: Optional[Client] = None
boll_client: Optional[Client] = None


def create_mr_client(use_testnet: bool) -> Optional[Client]:
    """Client for mean-reversion bot."""
    if DISABLE_BINANCE_CLIENT:
        print("[MR] Binance client disabled (DISABLE_BINANCE_CLIENT=1)")
        return None

    if use_testnet:
        if not MR_TESTNET_API_KEY or not MR_TESTNET_API_SECRET:
            raise RuntimeError(
                "BINANCE_TESTNET_API_KEY / BINANCE_TESTNET_API_SECRET must be set for MR bot"
            )
        return Client(MR_TESTNET_API_KEY, MR_TESTNET_API_SECRET, testnet=True)
    else:
        if not MR_MAINNET_API_KEY or not MR_MAINNET_API_SECRET:
            raise RuntimeError(
                "BINANCE_MAINNET_API_KEY / BINANCE_MAINNET_API_SECRET must be set for MR bot"
            )
        return Client(MR_MAINNET_API_KEY, MR_MAINNET_API_SECRET)


def create_boll_client(use_testnet: bool) -> Optional[Client]:
    """
    Client for Bollinger bot (separate sub-account).

    Preference:
      - if Bollinger keys are set -> use them
      - else -> fall back to MR keys (so you can still run with one account)
    """
    if DISABLE_BINANCE_CLIENT:
        print("[BOLL] Binance client disabled (DISABLE_BINANCE_CLIENT=1)")
        return None

    if use_testnet:
        key = BOLL_TESTNET_API_KEY or MR_TESTNET_API_KEY
        sec = BOLL_TESTNET_API_SECRET or MR_TESTNET_API_SECRET
        if not key or not sec:
            raise RuntimeError(
                "No testnet keys for Bollinger bot "
                "(BINANCE_BOLL_TESTNET_API_KEY or BINANCE_TESTNET_API_KEY)"
            )
        return Client(key, sec, testnet=True)
    else:
        key = BOLL_MAINNET_API_KEY or MR_MAINNET_API_KEY
        sec = BOLL_MAINNET_API_SECRET or MR_MAINNET_API_SECRET
        if not key or not sec:
            raise RuntimeError(
                "No mainnet keys for Bollinger bot "
                "(BINANCE_BOLL_MAINNET_API_KEY or BINANCE_MAINNET_API_KEY)"
            )
        return Client(key, sec)


def init_clients() -> None:
    """Initialise global clients according to per-bot env flags."""
    init_mr_client(MR_USE_TESTNET)
    init_boll_client(BOLL_USE_TESTNET)


def init_mr_client(use_testnet: bool) -> None:
    global mr_client, MR_USE_TESTNET
    MR_USE_TESTNET = use_testnet
    mr_client = create_mr_client(use_testnet)


def init_boll_client(use_testnet: bool) -> None:
    global boll_client, BOLL_USE_TESTNET
    BOLL_USE_TESTNET = use_testnet
    boll_client = create_boll_client(use_testnet)


def switch_env(use_testnet: bool) -> None:
    """Deprecated: switch both MR and Bollinger/trend bots to the same env."""
    init_mr_client(use_testnet)
    init_boll_client(use_testnet)


def switch_mr_env(use_testnet: bool) -> None:
    """Switch environment for the mean-reversion bot only."""
    init_mr_client(use_testnet)


def switch_boll_env(use_testnet: bool) -> None:
    """Switch environment for the Bollinger / trend bots only."""
    init_boll_client(use_testnet)


def get_mr_quote() -> str:
    """Quote asset the MR bot should use (e.g. USDT on testnet, USDC on mainnet)."""
    return TESTNET_QUOTE if MR_USE_TESTNET else MAINNET_QUOTE


def mr_symbol(base: str) -> str:
    """Build MR symbol like HBARUSDC / DOGEUSDT based on environment."""
    return f"{base}{get_mr_quote()}"


# Initialise on import
init_clients()
