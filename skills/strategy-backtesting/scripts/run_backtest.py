#!/usr/bin/env python3
"""Backtest, compare and walk-forward-validate spot strategies."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_spotkit"))

import json  # noqa: E402

import cli  # noqa: E402
from spotkit import backtest as B  # noqa: E402
from spotkit import config as C  # noqa: E402
from spotkit.exchange import closed_only, load_csv  # noqa: E402


def main():
    p = cli.parser(__doc__)
    p.add_argument("--csv", help="OHLCV CSV instead of exchange data")
    p.add_argument("--timeframe", default=None); p.add_argument("--limit", type=int, default=3000, help="candles of history")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("run"); s.add_argument("symbol"); s.add_argument("--strategy", default="confluence")
    s.add_argument("--params", default="{}", help='JSON, e.g. {"entry_score":50}')
    s.add_argument("--trades", type=int, default=5, help="recent trades to include")
    s.add_argument("--equity-csv", help="write the equity curve to this CSV")
    s = sub.add_parser("compare"); s.add_argument("symbol")
    s.add_argument("--strategies", default=",".join(B.STRATEGIES))
    s = sub.add_parser("walkforward"); s.add_argument("symbol"); s.add_argument("--strategy", default="confluence")
    s.add_argument("--folds", type=int, default=4)
    sub.add_parser("list")
    args = p.parse_args()
    if args.cmd == "list":
        C.emit({"ok": True, "strategies": {k: {"optimised_parameters": v[1]} for k, v in B.STRATEGIES.items()},
                "engine_defaults": B.ENGINE_DEFAULTS})
    cfg, feed = cli.setup(args)
    tf = args.timeframe or cfg["timeframe"]
    df = load_csv(args.csv) if args.csv else closed_only(feed.ohlcv(args.symbol, tf, limit=args.limit), tf)
    eng = B.engine_from_config(cfg)
    base = {"ok": True, "symbol": args.symbol, "timeframe": tf, "candles": len(df),
            "source": args.csv or feed.id, "costs": {"fee": eng["fee"], "slippage_bps": eng["slippage_bps"]}}
    if getattr(feed, "demo", False) and not args.csv:
        base["caution"] = "Synthetic demo data: this proves the pipeline works, it says nothing about real markets."
    if len(df) < 300:
        C.fail(f"Only {len(df)} candles; a backtest needs at least 300 (ideally 2000+).")
    if args.cmd == "run":
        r = B.run(df, args.strategy, json.loads(args.params), eng, tf)
        if args.equity_csv:
            r["_equity"].rename("equity").to_csv(args.equity_csv)
            base["equity_csv"] = args.equity_csv
        C.emit({**base, "result": B.public(r, args.trades), "warnings": B.assess(r)})
    if args.cmd == "compare":
        rows = []
        for name in args.strategies.split(","):
            r = B.run(df, name.strip(), None, eng, tf)
            rows.append({k: r.get(k) for k in ("strategy", "total_return_pct", "max_drawdown_pct", "sharpe", "trades",
                                               "win_rate_pct", "profit_factor", "expectancy_r", "exposure_pct")})
            bh = r.get("buy_and_hold_return_pct")
        rows.sort(key=lambda x: -(x["sharpe"] if x["sharpe"] is not None else -99))
        C.emit({**base, "buy_and_hold_return_pct": bh, "ranking_by_sharpe": rows,
                "note": "Default parameters, in-sample. Validate the winner with 'walkforward' before trusting it."})
    if args.cmd == "walkforward":
        C.emit({**base, **B.walk_forward(df, args.strategy, eng, tf, folds=args.folds)})


cli.run(main)
