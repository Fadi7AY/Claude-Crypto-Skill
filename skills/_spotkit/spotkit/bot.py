"""One trading cycle of the spot bot: manage exits first, then look for entries.

The cycle is idempotent per candle: each symbol's signal is evaluated once per
closed bar of the entry timeframe, so running it every minute or every hour
produces the same trades.
"""
from __future__ import annotations

import datetime as dt

from . import config as C
from . import indicators as ind
from . import journal as J
from . import risk as R
from . import signals as sig
from .exchange import closed_only

STATE = "bot_state.json"


def _iso(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")


def cycle(cfg: dict, feed, broker, dry_run: bool = False) -> dict:
    st = C.read_json(STATE, {"last_check": 0.0, "last_bar": {}})
    tf, htf, strat, rk = cfg["timeframe"], cfg["higher_timeframe"], cfg["strategy"], cfg["risk"]
    now = C.now()
    report = {"time": _iso(now), "mode": broker.mode, "dry_run": dry_run, "exits": [], "entries": [],
              "skipped": [], "errors": []}

    filled = [] if dry_run else broker.sync()
    if filled:
        report["limit_fills"] = filled

    # ---- 1. protective exits on everything we hold -------------------------
    frames = {}
    for sym, p in list(broker.state["positions"].items()):
        try:
            df = feed.ohlcv(sym, tf, limit=300)
            frames[sym] = df
            since = max(st["last_check"], p["opened_at"])
            recent = df[df.index.map(lambda t: t.timestamp()) >= since]
            last = feed.ticker(sym)["last"]
            bar = {"open": float(recent["open"].iloc[0]) if len(recent) else last,
                   "high": max(float(recent["high"].max()) if len(recent) else last, last),
                   "low": min(float(recent["low"].min()) if len(recent) else last, last),
                   "close": last, "atr": float(ind.atr(df).iloc[-1])}
            if dry_run:
                if p["stop"] is not None and bar["low"] <= p["stop"]:
                    report["exits"].append({"symbol": sym, "would": "stop_loss", "stop": p["stop"]})
                elif p["take_profit"] is not None and bar["high"] >= p["take_profit"]:
                    report["exits"].append({"symbol": sym, "would": "take_profit", "target": p["take_profit"]})
            else:
                report["exits"] += broker.check_exits({sym: bar})
        except Exception as e:  # noqa: BLE001 - one bad symbol must not stop the cycle
            report["errors"].append({"symbol": sym, "stage": "exits", "error": f"{type(e).__name__}: {e}"})

    # ---- 2. signals: once per newly closed bar ------------------------------
    symbols = list(dict.fromkeys(list(broker.state["positions"]) + cfg["watchlist"]))
    for sym in symbols:
        try:
            df = closed_only(frames[sym] if sym in frames else feed.ohlcv(sym, tf, limit=300), tf)
            if len(df) < 60:
                report["skipped"].append({"symbol": sym, "why": "not enough history"})
                continue
            bar_id = str(df.index[-1])
            if st["last_bar"].get(sym) == bar_id:
                continue
            a = sig.analyze(df, rk["stop_atr_mult"], rk["take_profit_r"] or 2.0)
            held = sym in broker.state["positions"]
            if held:
                if a["score"] <= strat["exit_score"]:
                    if dry_run:
                        report["exits"].append({"symbol": sym, "would": "signal_exit", "score": a["score"]})
                    else:
                        report["exits"].append(broker.sell(sym, None, f"signal_exit(score {a['score']})"))
                st["last_bar"][sym] = bar_id
                continue
            if sym not in cfg["watchlist"]:
                continue
            st["last_bar"][sym] = bar_id
            if a["score"] < strat["entry_score"]:
                report["skipped"].append({"symbol": sym, "why": f"score {a['score']} < {strat['entry_score']}"})
                continue
            reason = f"{tf} confluence {a['score']} ({a['regime']})"
            if strat.get("require_htf_trend", True):
                h = sig.analyze(closed_only(feed.ohlcv(sym, htf, limit=300), htf))
                if not h.get("ok") or h["score"] < 0:
                    report["skipped"].append({"symbol": sym, "why": f"{htf} trend not supportive "
                                                                    f"(score {h.get('score')})"})
                    continue
                reason += f", {htf} {h['score']}"
            if strat.get("tradingview_confirm"):
                from . import tv
                ok, why = tv.confirms_long(sym, tf, cfg["tradingview"]["exchange"])
                if not ok:
                    report["skipped"].append({"symbol": sym, "why": why})
                    continue
                reason += f", {why}"

            t = feed.ticker(sym)
            entry = t.get("ask") or t["last"]
            stop = a["plan"]["stop"]
            if stop >= entry:
                report["skipped"].append({"symbol": sym, "why": "stop is not below the current price"})
                continue
            target = entry + rk["take_profit_r"] * (entry - stop) if rk["take_profit_r"] else None
            acct = broker.account()
            market = feed.market(sym)
            size = R.position_size(acct["equity"], entry, stop, cfg, cash=acct["cash"],
                                   current_exposure=acct["exposure"], market=market, feed=feed, symbol=sym)
            if not size["ok"]:
                report["skipped"].append({"symbol": sym, "why": size["error"]})
                continue
            chk = R.pre_trade_check("buy", sym, size["qty"], entry, acct, cfg, stop=stop, target=target,
                                    ticker=t, market=market)
            if not chk["approved"]:
                report["skipped"].append({"symbol": sym, "why": "risk check: " + "; ".join(chk["violations"])})
                J.event(broker.mode, "entry_rejected", sym, violations=chk["violations"])
                continue
            plan = {"symbol": sym, "qty": size["qty"], "entry": entry, "stop": stop, "target": target,
                    "risk_amount": size["risk_amount"], "notional": size["notional"], "reason": reason}
            if dry_run:
                report["entries"].append({"would_buy": plan})
                continue
            otype = cfg["live"]["order_type"] if broker.mode == "live" else "market"
            res = broker.buy(sym, size["qty"], otype, entry if otype == "limit" else None, stop=stop, take_profit=target, trail_atr_mult=rk["trail_atr_mult"],
                             strategy=strat["name"], reason=reason)
            report["entries"].append({**plan, "result": res})
        except Exception as e:  # noqa: BLE001
            report["errors"].append({"symbol": sym, "stage": "signals", "error": f"{type(e).__name__}: {e}"})

    if not dry_run:
        st["last_check"] = now
        C.write_json(STATE, st)
    acct = broker.account()
    report["account"] = {k: acct[k] for k in ("equity", "cash", "exposure_pct")}
    report["account"]["open_positions"] = {s: {"qty": p["qty"], "avg_price": p["avg_price"], "stop": p["stop"],
                                               "take_profit": p["take_profit"], "unrealized_pct": p["unrealized_pct"]}
                                           for s, p in acct["positions"].items()}
    report["risk"] = R.status(acct["equity"], cfg)
    if not dry_run:
        J.event(broker.mode, "cycle", "", entries=len(report["entries"]), exits=len(report["exits"]),
                errors=len(report["errors"]), equity=acct["equity"])
    return report
