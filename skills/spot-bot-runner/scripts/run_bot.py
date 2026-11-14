#!/usr/bin/env python3
"""Spot bot runner: init config, run one cycle, loop, or show status."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_spotkit"))

import json  # noqa: E402
import time  # noqa: E402

import cli  # noqa: E402
from spotkit import bot  # noqa: E402
from spotkit import config as C  # noqa: E402
from spotkit import risk as R  # noqa: E402


def main():
    p = cli.parser(__doc__)
    p.add_argument("--live", action="store_true", help="trade the live account (see the skill for the unlock steps)")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("init", help="create .spotkit/config.json")
    s.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                   help="override, dotted keys allowed: --set exchange=bybit --set risk.risk_per_trade_pct=0.5")
    s.add_argument("--force", action="store_true")
    s = sub.add_parser("set", help="change config values"); s.add_argument("pairs", nargs="+", metavar="KEY=VALUE")
    sub.add_parser("config", help="print the effective config")
    s = sub.add_parser("cycle", help="run one cycle"); s.add_argument("--dry-run", action="store_true")
    s = sub.add_parser("loop", help="run cycles forever"); s.add_argument("--interval", type=int, default=300)
    s.add_argument("--max-cycles", type=int, default=0)
    sub.add_parser("status")
    args = p.parse_args()

    def parse(pairs):
        out = {}
        for kv in pairs:
            k, _, v = kv.partition("=")
            try:
                v = json.loads(v)
            except json.JSONDecodeError:
                pass
            node = out
            parts = k.split(".")
            for part in parts[:-1]:
                node = node.setdefault(part, {})
            node[parts[-1]] = v
        return out

    if args.cmd == "init":
        existed = C.config_path().exists()
        path = C.init_config(parse(args.set), args.force)
        C.emit({"ok": True, "config": str(path), "created": args.force or not existed, "settings": C.load_config()})
    if args.cmd == "set":
        C.init_config()
        user = json.loads(C.config_path().read_text())
        C.config_path().write_text(json.dumps(C._merge(user, parse(args.pairs)), indent=2))
        C.emit({"ok": True, "settings": C.load_config()})
    cfg = C.load_config()
    if args.cmd == "config":
        C.emit({"ok": True, "path": str(C.config_path()), "exists": C.config_path().exists(), "settings": cfg})

    feed, broker = cli.broker_for(args, cfg)
    if args.cmd == "status":
        a = broker.account()
        C.emit({"ok": True, "mode": broker.mode, "exchange": feed.id, "watchlist": cfg["watchlist"],
                "timeframes": [cfg["timeframe"], cfg["higher_timeframe"]], "account": a,
                "risk": R.status(a["equity"], cfg), "bot_state": C.read_json(bot.STATE, {})})
    if args.cmd == "cycle":
        C.emit({"ok": True, **bot.cycle(cfg, feed, broker, dry_run=args.dry_run)})
    if args.cmd == "loop":
        if broker.mode == "live" and not cfg["live"]["autonomous"]:
            C.fail("Unattended live trading is off. Set live.autonomous=true in config.json to allow it.")
        n = 0
        while True:
            try:
                rep = bot.cycle(cfg, feed, broker)
                print(json.dumps({"time": rep["time"], "entries": rep["entries"], "exits": rep["exits"],
                                  "errors": rep["errors"], "equity": rep["account"]["equity"],
                                  "can_open_trades": rep["risk"]["can_open_trades"]}, default=str), flush=True)
            except Exception as e:  # noqa: BLE001 - keep the loop alive through transient exchange errors
                print(json.dumps({"error": f"{type(e).__name__}: {e}"}), flush=True)
            n += 1
            if args.max_cycles and n >= args.max_cycles:
                break
            time.sleep(args.interval)
            cfg = C.load_config()  # pick up config edits and the kill switch without a restart
            broker.cfg = cfg


cli.run(main)
