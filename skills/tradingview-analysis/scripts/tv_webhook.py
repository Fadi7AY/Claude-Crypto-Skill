#!/usr/bin/env python3
"""Receive TradingView alert webhooks and route them through the risk engine to the broker.

Alert message (JSON) expected from TradingView:
  {"secret": "...", "action": "buy" | "sell", "symbol": "BTCUSDT", "stop": 61200.5, "target": 66000}
`stop` and `target` are optional for buys (derived from analysis / config when missing).
"""
import hmac
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_spotkit"))

import cli  # noqa: E402
from spotkit import config as C  # noqa: E402
from spotkit import journal as J  # noqa: E402
from spotkit import risk as R  # noqa: E402
from spotkit import signals as sig  # noqa: E402
from spotkit.exchange import closed_only  # noqa: E402


def normalize(symbol: str, quote: str) -> str:
    s = symbol.upper().split(":")[-1].replace("-", "/")  # "BINANCE:BTCUSDT" -> "BTCUSDT"
    if "/" in s:
        return s
    if not s.endswith(quote):
        raise ValueError(f"Symbol {symbol} does not end with the configured quote {quote}.")
    return f"{s[:-len(quote)]}/{quote}"


def handle(payload: dict, cfg: dict, feed, broker, secret: str) -> tuple[int, dict]:
    if not secret or not hmac.compare_digest(str(payload.get("secret", "")), secret):
        return 403, {"ok": False, "error": "bad secret"}
    try:
        sym = normalize(str(payload["symbol"]), cfg["quote"])
        action = str(payload["action"]).lower()
    except (KeyError, ValueError) as e:
        return 400, {"ok": False, "error": f"bad payload: {e}"}
    J.event(broker.mode, "webhook", sym, action=action, stop=payload.get("stop"), target=payload.get("target"))
    if action == "sell":
        if sym not in broker.state["positions"]:
            return 200, {"ok": True, "status": "ignored", "why": "no position"}
        return 200, broker.sell(sym, None, "tradingview_alert")
    if action != "buy":
        return 400, {"ok": False, "error": "action must be buy or sell"}
    t = feed.ticker(sym)
    entry = t.get("ask") or t["last"]
    stop = payload.get("stop")
    if stop is None:
        a = sig.analyze(closed_only(feed.ohlcv(sym, cfg["timeframe"], limit=300), cfg["timeframe"]),
                        cfg["risk"]["stop_atr_mult"])
        if not a.get("ok"):
            return 200, {"ok": False, "status": "rejected", "why": "no stop available"}
        stop = a["plan"]["stop"]
    stop = float(stop)
    tp_r = cfg["risk"]["take_profit_r"]
    target = payload.get("target") or (entry + tp_r * (entry - stop) if tp_r and stop < entry else None)
    acct, market = broker.account(), feed.market(sym)
    size = R.position_size(acct["equity"], entry, stop, cfg, cash=acct["cash"], current_exposure=acct["exposure"],
                           market=market, feed=feed, symbol=sym)
    if not size["ok"]:
        return 200, {"ok": False, "status": "rejected", "why": size["error"]}
    chk = R.pre_trade_check("buy", sym, size["qty"], entry, acct, cfg, stop=stop,
                            target=float(target) if target else None, ticker=t, market=market)
    if not chk["approved"]:
        J.event(broker.mode, "entry_rejected", sym, violations=chk["violations"], source="webhook")
        return 200, {"ok": False, "status": "rejected", "why": chk["violations"]}
    res = broker.buy(sym, size["qty"], "market", stop=stop, take_profit=float(target) if target else None,
                     trail_atr_mult=cfg["risk"]["trail_atr_mult"], strategy="tradingview_alert",
                     reason=str(payload.get("reason", "TradingView alert")))
    return 200, res


def main():
    p = cli.parser(__doc__)
    p.add_argument("--live", action="store_true")
    p.add_argument("--host", default="127.0.0.1", help="bind address (keep 127.0.0.1 behind a tunnel/reverse proxy)")
    p.add_argument("--port", type=int)
    p.add_argument("--test", help="process one JSON payload and exit instead of starting the server")
    args = p.parse_args()
    cfg = C.load_config()
    secret = os.environ.get("TV_WEBHOOK_SECRET", "")
    if len(secret) < 12:
        C.fail("Set TV_WEBHOOK_SECRET (12+ characters) in the environment; alerts must carry the same value.")
    feed, broker = cli.broker_for(args, cfg)
    if broker.mode == "live" and not cfg["live"]["autonomous"]:
        C.fail("Webhook-driven live orders are unattended trading. Set live.autonomous=true in config.json to allow it.")
    if args.test:
        code, body = handle(json.loads(args.test), cfg, feed, broker, secret)
        C.emit({"http_status": code, **body}, 0 if code == 200 else 1)

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            try:
                n = int(self.headers.get("Content-Length") or 0)
                if n > 10_000:
                    raise ValueError("payload too large")
                code, body = handle(json.loads(self.rfile.read(n) or b"{}"), C.load_config(), feed, broker, secret)
            except Exception as e:  # noqa: BLE001
                code, body = 400, {"ok": False, "error": f"{type(e).__name__}: {e}"}
            data = json.dumps(body, default=str).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            print(json.dumps({"status": code, **body}, default=str), flush=True)

        def log_message(self, *a):
            pass

    port = args.port or cfg["tradingview"]["webhook_port"]
    print(json.dumps({"ok": True, "listening": f"http://{args.host}:{port}", "mode": broker.mode}), flush=True)
    HTTPServer((args.host, port), H).serve_forever()


if __name__ == "__main__":
    cli.run(main)
