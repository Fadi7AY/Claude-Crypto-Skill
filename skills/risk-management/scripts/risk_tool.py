#!/usr/bin/env python3
"""Risk tools: position sizing, pre-trade check, account risk status, halt/resume."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_spotkit"))

import cli  # noqa: E402
from spotkit import config as C  # noqa: E402
from spotkit import risk as R  # noqa: E402


def main():
    p = cli.parser(__doc__)
    p.add_argument("--live", action="store_true", help="use the live account instead of the paper account")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("size", help="how much to buy for a given entry and stop")
    s.add_argument("symbol"); s.add_argument("--entry", type=float); s.add_argument("--stop", type=float, required=True)
    s.add_argument("--equity", type=float, help="size against this equity instead of the account")
    s = sub.add_parser("check", help="run the pre-trade checklist on a proposed order")
    s.add_argument("symbol"); s.add_argument("--side", choices=["buy", "sell"], default="buy")
    s.add_argument("--qty", type=float, required=True); s.add_argument("--entry", type=float)
    s.add_argument("--stop", type=float); s.add_argument("--target", type=float)
    sub.add_parser("status", help="equity, day P&L, drawdown, halts")
    s = sub.add_parser("halt", help="turn the kill switch on (blocks all new buys)"); s.add_argument("--reason", default="manual halt")
    sub.add_parser("resume", help="clear the kill switch and any drawdown halt")
    sub.add_parser("limits", help="show the configured risk limits")
    args = p.parse_args()
    cfg = C.load_config()
    if args.cmd == "halt":
        R.set_kill(True, args.reason)
        C.emit({"ok": True, "kill_switch": True, "reason": args.reason})
    if args.cmd == "resume":
        R.resume()
        C.emit({"ok": True, "kill_switch": False, "halted": False,
                "note": "Peak equity was reset so the drawdown breaker measures from here."})
    if args.cmd == "limits":
        C.emit({"ok": True, "mode": cfg["mode"], "risk": cfg["risk"], "fees": cfg["fees"]})

    feed, broker = cli.broker_for(args, cfg)
    acct = broker.account()
    if args.cmd == "status":
        C.emit({"ok": True, "mode": broker.mode, **R.status(acct["equity"], cfg), "cash": acct["cash"],
                "exposure_pct": acct["exposure_pct"], "open_positions": len(acct["positions"]),
                "limits": {k: cfg["risk"][k] for k in ("risk_per_trade_pct", "max_position_pct",
                           "max_total_exposure_pct", "max_open_positions", "daily_loss_limit_pct", "max_drawdown_pct")}})
    t = feed.ticker(args.symbol)
    entry = args.entry or t.get("ask") or t["last"]
    market = feed.market(args.symbol)
    if args.cmd == "size":
        eq = args.equity or acct["equity"]
        res = R.position_size(eq, entry, args.stop, cfg, cash=None if args.equity else acct["cash"],
                              current_exposure=0.0 if args.equity else acct["exposure"], market=market,
                              feed=feed, symbol=args.symbol)
        C.emit({"symbol": args.symbol, "equity_used": eq, **res}, 0 if res["ok"] else 1)
    if args.cmd == "check":
        res = R.pre_trade_check(args.side, args.symbol, args.qty, entry, acct, cfg, stop=args.stop,
                                target=args.target, ticker=t, market=market)
        C.emit({"ok": True, "symbol": args.symbol, "side": args.side, "qty": args.qty, "entry": entry, **res})


cli.run(main)
