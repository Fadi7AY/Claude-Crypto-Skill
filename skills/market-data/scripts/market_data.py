#!/usr/bin/env python3
"""Market data for spot pairs: candles, ticker, order book, market rules, top movers."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_spotkit"))

import cli  # noqa: E402
from spotkit import config as C  # noqa: E402
from spotkit.exchange import closed_only  # noqa: E402


def main():
    p = cli.parser(__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("ohlcv"); s.add_argument("symbol"); s.add_argument("--timeframe", default="1h")
    s.add_argument("--limit", type=int, default=200); s.add_argument("--csv", help="write candles to this CSV file")
    s.add_argument("--tail", type=int, default=10, help="candles to print")
    s = sub.add_parser("ticker"); s.add_argument("symbols", nargs="+")
    s = sub.add_parser("orderbook"); s.add_argument("symbol"); s.add_argument("--depth", type=int, default=10)
    s = sub.add_parser("market"); s.add_argument("symbol")
    s = sub.add_parser("top"); s.add_argument("--limit", type=int, default=15)
    s.add_argument("--sort", choices=["volume", "gainers", "losers"], default="volume")
    args = p.parse_args()
    cfg, feed = cli.setup(args)

    if args.cmd == "ohlcv":
        df = closed_only(feed.ohlcv(args.symbol, args.timeframe, limit=args.limit), args.timeframe)
        out = {"ok": True, "symbol": args.symbol, "timeframe": args.timeframe, "candles": len(df),
               "from": str(df.index[0]), "to": str(df.index[-1]),
               "change_pct": round((df["close"].iloc[-1] / df["close"].iloc[0] - 1) * 100, 2),
               "high": float(df["high"].max()), "low": float(df["low"].min())}
        if args.csv:
            df.to_csv(args.csv)
            out["csv"] = args.csv
        out["last_candles"] = [{"time": str(t), **{k: float(v) for k, v in r.items()}}
                               for t, r in df.tail(args.tail).iterrows()]
        C.emit(out)
    if args.cmd == "ticker":
        C.emit({"ok": True, "exchange": feed.id, "tickers": [feed.ticker(s) for s in args.symbols]})
    if args.cmd == "orderbook":
        ob = feed.orderbook(args.symbol, args.depth)
        bid, ask = ob["bids"][0][0], ob["asks"][0][0]
        bq, aq = sum(b[0] * b[1] for b in ob["bids"]), sum(a[0] * a[1] for a in ob["asks"])
        C.emit({"ok": True, "symbol": args.symbol, "best_bid": bid, "best_ask": ask,
                "spread_bps": round((ask - bid) / ((ask + bid) / 2) * 1e4, 2),
                "bid_depth_quote": round(bq, 2), "ask_depth_quote": round(aq, 2),
                "imbalance": round((bq - aq) / (bq + aq), 3), "bids": ob["bids"], "asks": ob["asks"]})
    if args.cmd == "market":
        C.emit({"ok": True, "exchange": feed.id, **feed.market(args.symbol)})
    if args.cmd == "top":
        rows = [r for r in feed.tickers(cfg["quote"]) if r["last"]]
        key = {"volume": lambda r: -(r["quote_volume_24h"] or 0),
               "gainers": lambda r: -(r["change_pct_24h"] or -1e9),
               "losers": lambda r: (r["change_pct_24h"] or 1e9)}[args.sort]
        C.emit({"ok": True, "exchange": feed.id, "sort": args.sort, "results": sorted(rows, key=key)[:args.limit]})


cli.run(main)
