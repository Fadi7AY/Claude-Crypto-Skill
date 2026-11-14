"""Shared CLI helpers for the skill scripts."""
from __future__ import annotations

import argparse
import os
import traceback

from spotkit import config as C
from spotkit.exchange import get_feed


def parser(desc: str) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=desc)
    p.add_argument("--demo", action="store_true", help="offline synthetic market, no network or keys")
    p.add_argument("--exchange", help="override the ccxt exchange id from config.json")
    return p


def setup(args, auth: bool = False):
    cfg = C.load_config()
    if getattr(args, "exchange", None):
        cfg["exchange"] = args.exchange
    return cfg, get_feed(cfg, demo=args.demo, auth=auth)


def broker_for(args, cfg):
    """Paper broker unless --live is passed AND config/env unlock live trading."""
    from spotkit.broker import get_broker

    live = bool(getattr(args, "live", False))
    if live and args.demo:
        C.fail("--live cannot be combined with --demo.")
    if live and os.environ.get("SPOTKIT_LIVE") != "1":
        C.fail("Live trading is locked. Set SPOTKIT_LIVE=1 in the environment to unlock it.")
    if live and cfg.get("mode") != "live":
        C.fail("config.json has mode='paper'. Set mode to 'live' (run_bot.py set mode=live) to trade real funds.")
    feed = get_feed(cfg, demo=args.demo, auth=live)
    return feed, get_broker(cfg, feed, live=live)


def run(main) -> None:
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001 - scripts always answer in JSON
        hint = None
        name = type(e).__name__
        if name in ("NetworkError", "ExchangeNotAvailable", "RequestTimeout", "DDoSProtection"):
            hint = "Exchange unreachable. Check the connection, try --exchange with another venue, or use --demo."
        elif name == "AuthenticationError":
            hint = "API key rejected. Check EXCHANGE_API_KEY / EXCHANGE_API_SECRET and the key's spot-trading permission."
        elif name == "InsufficientFunds":
            hint = "The exchange reports insufficient balance for this order."
        extra = {"trace": traceback.format_exc()} if os.environ.get("SPOTKIT_DEBUG") else {}
        C.fail(f"{name}: {e}", **({"hint": hint} if hint else {}), **extra)
