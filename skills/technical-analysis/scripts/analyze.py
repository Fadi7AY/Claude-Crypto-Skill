#!/usr/bin/env python3
"""Multi-timeframe technical analysis with a confluence score and a trade plan."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_spotkit"))

import cli  # noqa: E402
from spotkit import config as C  # noqa: E402
from spotkit import signals as sig  # noqa: E402
from spotkit.exchange import load_csv  # noqa: E402


def main():
    p = cli.parser(__doc__)
    p.add_argument("symbol")
    p.add_argument("--timeframes", default=None, help="comma list, entry timeframe first (default from config)")
    p.add_argument("--limit", type=int, default=400)
    p.add_argument("--csv", help="analyze candles from a CSV instead of the exchange (single timeframe)")
    p.add_argument("--brief", action="store_true", help="omit the raw indicator values")
    args = p.parse_args()
    cfg, feed = cli.setup(args)
    if args.csv:
        res = {"ok": True, "symbol": args.symbol, "source": args.csv,
               **sig.analyze(load_csv(args.csv), cfg["risk"]["stop_atr_mult"], cfg["risk"]["take_profit_r"] or 2.0)}
    else:
        tfs = args.timeframes.split(",") if args.timeframes else [cfg["timeframe"], cfg["higher_timeframe"]]
        res = sig.multi_timeframe(feed, args.symbol, tfs, args.limit, cfg["risk"])
        res["exchange"] = feed.id
    if args.brief:
        for a in res.get("timeframes", {}).values():
            a.pop("indicators", None)
        res.pop("indicators", None)
    res["disclaimer"] = "Analysis, not financial advice. Scores describe the chart; they do not predict it."
    C.emit(res, 0 if res.get("ok") else 1)


cli.run(main)
