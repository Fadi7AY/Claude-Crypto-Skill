#!/usr/bin/env python3
"""Trade journal: closed trades, performance stats, event log, notes, CSV export."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_spotkit"))

import datetime as dt  # noqa: E402

import cli  # noqa: E402
from spotkit import config as C  # noqa: E402
from spotkit import journal as J  # noqa: E402


def _t(ts):
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%d %H:%M") if ts else None


def main():
    p = cli.parser(__doc__)
    p.add_argument("--mode", choices=["paper", "live"], help="filter by account type")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("trades"); s.add_argument("--symbol"); s.add_argument("--limit", type=int, default=20)
    sub.add_parser("stats")
    s = sub.add_parser("events"); s.add_argument("--limit", type=int, default=30); s.add_argument("--kind")
    s = sub.add_parser("note"); s.add_argument("trade_id", type=int); s.add_argument("text")
    s = sub.add_parser("export"); s.add_argument("path")
    args = p.parse_args()
    if args.cmd == "trades":
        rows = J.trades(args.mode, args.symbol, args.limit)
        for r in rows:
            r["ts_open"], r["ts_close"] = _t(r["ts_open"]), _t(r["ts_close"])
            for k in ("pnl", "pnl_pct", "r_multiple", "fees"):
                r[k] = None if r[k] is None else round(r[k], 3)
        C.emit({"ok": True, "count": len(rows), "trades": rows})
    if args.cmd == "stats":
        C.emit({"ok": True, **J.stats(args.mode)})
    if args.cmd == "events":
        rows = J.events(args.limit, args.kind)
        for r in rows:
            r["ts"] = _t(r["ts"])
        C.emit({"ok": True, "events": rows})
    if args.cmd == "note":
        ok = J.add_note(args.trade_id, args.text)
        C.emit({"ok": ok, **({} if ok else {"error": f"No trade with id {args.trade_id}."})}, 0 if ok else 1)
    if args.cmd == "export":
        C.emit({"ok": True, "rows": J.export_csv(args.path, args.mode), "path": args.path})


cli.run(main)
