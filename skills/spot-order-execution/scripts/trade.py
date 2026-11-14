#!/usr/bin/env python3
"""Spot order execution (paper by default): preview, buy, sell, stops, orders, balances."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_spotkit"))

import cli  # noqa: E402
from spotkit import config as C  # noqa: E402
from spotkit import risk as R  # noqa: E402
from spotkit import signals as sig  # noqa: E402
from spotkit.exchange import closed_only  # noqa: E402


def main():
    p = cli.parser(__doc__)
    p.add_argument("--live", action="store_true", help="real orders (needs mode=live, SPOTKIT_LIVE=1 and API keys)")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("account", help="cash, equity, positions, open orders")
    s = sub.add_parser("buy")
    s.add_argument("symbol")
    s.add_argument("--qty", type=float, help="base-coin amount; omit to size from risk rules")
    s.add_argument("--spend", type=float, help="quote amount to spend instead of --qty")
    s.add_argument("--stop", type=float, help="stop price; omit to use the ATR/support stop from analysis")
    s.add_argument("--target", type=float, help="take-profit price; omit to use take_profit_r from config")
    s.add_argument("--type", choices=["market", "limit"], default="market"); s.add_argument("--price", type=float)
    s.add_argument("--reason", default="manual"); s.add_argument("--strategy", default="manual")
    s.add_argument("--confirm", action="store_true", help="place the order; without it only a preview is shown")
    s = sub.add_parser("sell")
    s.add_argument("symbol"); s.add_argument("--qty", type=float, help="omit to sell the whole position")
    s.add_argument("--reason", default="manual"); s.add_argument("--confirm", action="store_true")
    s = sub.add_parser("set-stop"); s.add_argument("symbol"); s.add_argument("--stop", type=float)
    s.add_argument("--target", type=float); s.add_argument("--trail-atr", type=float)
    s = sub.add_parser("cancel"); s.add_argument("order_id")
    sub.add_parser("sync", help="update resting limit orders")
    sub.add_parser("reset-paper", help="wipe the paper account back to the starting balance").add_argument(
        "--confirm", action="store_true")
    args = p.parse_args()
    cfg = C.load_config()
    if args.cmd == "reset-paper":
        if not args.confirm:
            C.fail("Add --confirm to wipe the paper account.")
        (C.home() / "paper_account.json").unlink(missing_ok=True)
        C.emit({"ok": True, "paper_balance": cfg["paper"]["starting_balance"]})

    feed, broker = cli.broker_for(args, cfg)
    if args.cmd == "account":
        a = broker.account()
        C.emit({"ok": True, **a, "risk": R.status(a["equity"], cfg)})
    if args.cmd == "sync":
        C.emit({"ok": True, "filled": broker.sync(), "open_orders": broker.state["orders"]})
    if args.cmd == "cancel":
        oid = int(args.order_id) if broker.mode == "paper" else args.order_id
        res = broker.cancel(oid)
        C.emit(res, 0 if res["ok"] else 1)
    if args.cmd == "set-stop":
        res = broker.set_stop(args.symbol, args.stop, args.target, args.trail_atr)
        C.emit(res, 0 if res["ok"] else 1)

    t = feed.ticker(args.symbol)
    acct = broker.account()
    if args.cmd == "sell":
        pos = acct["positions"].get(args.symbol)
        if not pos:
            C.fail(f"No {args.symbol} position held in the {broker.mode} account.")
        qty = args.qty or pos["qty"]
        chk = R.pre_trade_check("sell", args.symbol, qty, t["last"], acct, cfg)
        preview = {"symbol": args.symbol, "side": "sell", "qty": qty, "est_price": t.get("bid") or t["last"],
                   "est_proceeds": round(qty * (t.get("bid") or t["last"]), 2), "avg_entry": pos["avg_price"],
                   "est_pnl_pct": pos["unrealized_pct"]}
        if not chk["approved"]:
            C.emit({"ok": False, "mode": broker.mode, "preview": preview, "risk_check": chk}, 1)
        if not args.confirm:
            C.emit({"ok": True, "mode": broker.mode, "status": "preview", "preview": preview,
                    "next": "Re-run with --confirm to place this order."})
        res = broker.sell(args.symbol, args.qty, args.reason)
        C.emit({"mode": broker.mode, **res}, 0 if res["ok"] else 1)

    # ---- buy ----
    if args.type == "limit" and not args.price:
        C.fail("--price is required for a limit order.")
    entry = args.price if args.type == "limit" else (t.get("ask") or t["last"])
    stop, stop_src = args.stop, "user"
    if stop is None:
        a = sig.analyze(closed_only(feed.ohlcv(args.symbol, cfg["timeframe"], limit=300), cfg["timeframe"]),
                        cfg["risk"]["stop_atr_mult"])
        if not a.get("ok"):
            C.fail("No --stop given and analysis could not derive one: " + a.get("error", ""))
        stop = a["plan"]["stop"] - (a["price"] - entry if args.type == "limit" else 0)
        stop_src = f"analysis ({cfg['risk']['stop_atr_mult']} ATR / nearest support)"
    tp_r = cfg["risk"]["take_profit_r"]
    target = args.target if args.target is not None else (entry + tp_r * (entry - stop) if tp_r and stop < entry else None)
    market = feed.market(args.symbol)
    if args.qty:
        qty = feed.round_amount(args.symbol, args.qty)
        sizing = "user qty"
    elif args.spend:
        qty = feed.round_amount(args.symbol, args.spend / entry)
        sizing = "user spend"
    else:
        sz = R.position_size(acct["equity"], entry, stop, cfg, cash=acct["cash"], current_exposure=acct["exposure"],
                             market=market, feed=feed, symbol=args.symbol)
        if not sz["ok"]:
            C.emit({"mode": broker.mode, "symbol": args.symbol, **sz}, 1)
        qty, sizing = sz["qty"], f"risk rules (limited by {sz['limited_by']})"
    chk = R.pre_trade_check("buy", args.symbol, qty, entry, acct, cfg, stop=stop, target=target, ticker=t, market=market)
    preview = {"symbol": args.symbol, "side": "buy", "type": args.type, "qty": qty, "entry": entry,
               "notional": round(qty * entry, 2), "stop": stop, "stop_source": stop_src, "target": target,
               "risk_amount": round(qty * (entry - stop), 2) if stop < entry else None,
               "risk_pct_of_equity": round(qty * (entry - stop) / acct["equity"] * 100, 2) if stop < entry else None,
               "reward_risk": round((target - entry) / (entry - stop), 2) if target and stop < entry else None,
               "sizing": sizing, "est_fee": round(qty * entry * cfg["fees"]["taker"], 4)}
    if not chk["approved"]:
        C.emit({"ok": False, "mode": broker.mode, "status": "rejected_by_risk", "preview": preview, "risk_check": chk}, 1)
    if not args.confirm:
        C.emit({"ok": True, "mode": broker.mode, "status": "preview", "preview": preview, "risk_check": chk,
                "next": "Re-run with --confirm to place this order."})
    res = broker.buy(args.symbol, qty, args.type, args.price, stop=stop, take_profit=target,
                     trail_atr_mult=cfg["risk"]["trail_atr_mult"], strategy=args.strategy, reason=args.reason)
    C.emit({"mode": broker.mode, **res, "stop": stop, "target": target}, 0 if res["ok"] else 1)


cli.run(main)
