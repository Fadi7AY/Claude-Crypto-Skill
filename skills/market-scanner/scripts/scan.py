#!/usr/bin/env python3
"""Scan the most liquid spot pairs and rank them by confluence score."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_spotkit"))

import cli  # noqa: E402
from spotkit import config as C  # noqa: E402
from spotkit import signals as sig  # noqa: E402
from spotkit.exchange import closed_only  # noqa: E402

STABLES = {"USDC", "FDUSD", "TUSD", "DAI", "USDP", "BUSD", "USDE", "PYUSD", "EUR", "USD1", "XUSD"}


def main():
    p = cli.parser(__doc__)
    p.add_argument("--top", type=int, default=25, help="how many pairs (by 24h volume) to scan")
    p.add_argument("--symbols", help="comma list to scan instead of the top-volume universe")
    p.add_argument("--timeframe", default=None)
    p.add_argument("--min-score", type=float, default=-100)
    p.add_argument("--tv", action="store_true", help="add TradingView ratings for the entry timeframe")
    args = p.parse_args()
    cfg, feed = cli.setup(args)
    tf, htf = args.timeframe or cfg["timeframe"], cfg["higher_timeframe"]
    if args.symbols:
        universe = [{"symbol": s, "quote_volume_24h": None, "change_pct_24h": None} for s in args.symbols.split(",")]
    else:
        rows = [r for r in feed.tickers(cfg["quote"])
                if r["symbol"].split("/")[0] not in STABLES
                and (r["quote_volume_24h"] or 0) >= cfg["risk"]["min_quote_volume_24h"]]
        universe = sorted(rows, key=lambda r: -(r["quote_volume_24h"] or 0))[:args.top]
    results, errors = [], []
    for u in universe:
        s = u["symbol"]
        try:
            a = sig.analyze(closed_only(feed.ohlcv(s, tf, limit=300), tf), cfg["risk"]["stop_atr_mult"])
            h = sig.analyze(closed_only(feed.ohlcv(s, htf, limit=300), htf))
            if not a.get("ok"):
                continue
            hs = h.get("score") if h.get("ok") else None
            tradeable = a["score"] >= cfg["strategy"]["entry_score"] and (hs is None or hs >= 0)
            results.append({"symbol": s, "score": a["score"], "signal": a["signal"], "htf_score": hs,
                            "regime": a["regime"], "rsi": round(a["indicators"]["rsi"], 1),
                            "adx": round(a["indicators"]["adx"], 1), "atr_pct": round(a["indicators"]["atr_pct"], 2),
                            "price": a["price"], "stop": a["plan"]["stop"], "risk_pct": a["plan"]["risk_pct"],
                            "change_pct_24h": u["change_pct_24h"], "quote_volume_24h": u["quote_volume_24h"],
                            "meets_entry_rules": tradeable})
        except Exception as e:  # noqa: BLE001
            errors.append({"symbol": s, "error": f"{type(e).__name__}: {e}"})
    results = sorted([r for r in results if r["score"] >= args.min_score], key=lambda r: -r["score"])
    out = {"ok": True, "exchange": feed.id, "timeframe": tf, "higher_timeframe": htf, "scanned": len(universe),
           "candidates": [r["symbol"] for r in results if r["meets_entry_rules"]], "results": results, "errors": errors}
    if args.tv and results:
        from spotkit import tv
        r = tv.ratings_many([x["symbol"] for x in results], tf, cfg["tradingview"]["exchange"])
        if r["ok"]:
            for x in results:
                x["tv_rating"] = r["ratings"].get(x["symbol"])
        else:
            out["tv_error"] = r["error"]
    C.emit(out)


cli.run(main)
