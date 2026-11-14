#!/usr/bin/env python3
"""TradingView technical ratings for a pair across timeframes."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_spotkit"))

import cli  # noqa: E402
from spotkit import config as C  # noqa: E402
from spotkit import tv  # noqa: E402


def main():
    p = cli.parser(__doc__)
    p.add_argument("symbols", nargs="+", help="pairs such as BTC/USDT")
    p.add_argument("--timeframes", default="15m,1h,4h,1d")
    p.add_argument("--tv-exchange", help="TradingView exchange prefix, e.g. BINANCE, BYBIT, KRAKEN, COINBASE")
    args = p.parse_args()
    cfg = C.load_config()
    ex = args.tv_exchange or cfg["tradingview"]["exchange"]
    res = [tv.rating(s, args.timeframes.split(","), ex, cfg["tradingview"]["screener"]) for s in args.symbols]
    ok = any(r["ok"] for r in res)
    out = {"ok": ok, "source": "TradingView technical ratings (public scanner)", "results": res}
    if not ok:
        out["hint"] = ("TradingView was unreachable or the symbol/exchange pair does not exist there. "
                       "Check --tv-exchange, or fall back to the technical-analysis skill.")
    C.emit(out, 0 if ok else 1)


cli.run(main)
