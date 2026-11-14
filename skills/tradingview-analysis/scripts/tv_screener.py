#!/usr/bin/env python3
"""TradingView crypto screener presets (momentum, oversold pullbacks, volume breakouts)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_spotkit"))

import cli  # noqa: E402
from spotkit import config as C  # noqa: E402
from spotkit import tv  # noqa: E402


def main():
    p = cli.parser(__doc__)
    p.add_argument("--screen", default="momentum", choices=sorted(tv.SCREENS))
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--tv-exchange"); p.add_argument("--min-volume", type=float)
    p.add_argument("--list", action="store_true", help="list the available screens")
    args = p.parse_args()
    if args.list:
        C.emit({"ok": True, "screens": tv.SCREENS})
    cfg = C.load_config()
    res = tv.screen(args.screen, args.tv_exchange or cfg["tradingview"]["exchange"], cfg["quote"], args.limit,
                    args.min_volume or cfg["risk"]["min_quote_volume_24h"])
    C.emit(res, 0 if res["ok"] else 1)


cli.run(main)
