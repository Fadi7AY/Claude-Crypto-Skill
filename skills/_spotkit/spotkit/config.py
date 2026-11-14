"""Configuration and state-folder handling.

State lives in ./.spotkit (override with SPOTKIT_HOME): config.json, the paper
account, bot-managed positions, risk state, the journal database and the KILL
switch file. API keys are never stored there - they come from the environment.
"""
from __future__ import annotations

import copy
import json
import os
import sys
import time
from pathlib import Path

DEFAULTS = {
    "exchange": "binance",          # any ccxt exchange id (binance, bybit, kraken, okx, cryptocom, ...)
    "quote": "USDT",
    "mode": "paper",                # "paper" or "live"
    "watchlist": ["BTC/USDT", "ETH/USDT", "SOL/USDT"],
    "timeframe": "1h",              # entry timeframe
    "higher_timeframe": "4h",       # trend filter timeframe
    "strategy": {
        "name": "confluence",
        "params": {},
        "entry_score": 40,          # confluence score needed to open
        "exit_score": -20,          # score at/below which an open position is closed
        "require_htf_trend": True,  # higher timeframe must not be bearish
        "tradingview_confirm": False  # also require a TradingView BUY/STRONG_BUY rating
    },
    "fees": {"taker": 0.001, "maker": 0.001, "slippage_bps": 5},
    "paper": {"starting_balance": 10000.0},
    "risk": {
        "risk_per_trade_pct": 1.0,      # % of equity lost if the stop is hit
        "max_position_pct": 20.0,       # max % of equity in one coin
        "max_total_exposure_pct": 60.0, # max % of equity in coins overall
        "max_open_positions": 4,
        "daily_loss_limit_pct": 3.0,    # stop opening trades for the day after this loss
        "max_drawdown_pct": 15.0,       # halt until manually resumed
        "min_reward_risk": 1.5,
        "stop_atr_mult": 2.0,
        "take_profit_r": 2.0,           # 0 disables the fixed target
        "trail_atr_mult": 0.0,          # 0 disables trailing; e.g. 3.0 trails 3 ATR under the high
        "max_spread_bps": 30,
        "min_quote_volume_24h": 5_000_000,
        "cooldown_minutes_after_stop": 240,
        "allow_adding_to_position": False
    },
    "live": {
        "autonomous": False,        # must be true for the bot loop to place live orders unattended
        "order_type": "market",
        "exchange_stops": False     # best-effort native stop orders; soft stops are always tracked
    },
    "tradingview": {"exchange": "BINANCE", "screener": "crypto", "webhook_port": 8787}
}


def home() -> Path:
    p = Path(os.environ.get("SPOTKIT_HOME") or Path.cwd() / ".spotkit")
    p.mkdir(parents=True, exist_ok=True)
    return p


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def config_path() -> Path:
    return home() / "config.json"


def load_config() -> dict:
    p = config_path()
    user = json.loads(p.read_text()) if p.exists() else {}
    return _merge(DEFAULTS, user)


def init_config(overrides: dict | None = None, force: bool = False) -> Path:
    p = config_path()
    if p.exists() and not force:
        return p
    p.write_text(json.dumps(_merge(DEFAULTS, overrides or {}), indent=2))
    return p


def read_json(name: str, default):
    p = home() / name
    if not p.exists():
        return copy.deepcopy(default)
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError:
        return copy.deepcopy(default)


def write_json(name: str, data) -> None:
    p = home() / name
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, default=str))
    tmp.replace(p)  # atomic, so a crash mid-write cannot corrupt account state


def now() -> float:
    return time.time()


def emit(obj, code: int = 0) -> None:
    """Print a JSON result and exit. Every script speaks JSON so Claude can parse it."""
    print(json.dumps(obj, indent=2, default=str))
    sys.exit(code)


def fail(message: str, **extra) -> None:
    emit({"ok": False, "error": message, **extra}, code=1)
