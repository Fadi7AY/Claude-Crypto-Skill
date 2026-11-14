"""Risk engine: position sizing, pre-trade checks, loss limits and the kill switch.

Every buy - manual, webhook or bot - goes through pre_trade_check. Sells are
always allowed because they reduce risk.
"""
from __future__ import annotations

import datetime as dt

from . import config as C

STATE = "risk_state.json"
_DEFAULT = {"peak_equity": 0.0, "day": "", "day_start_equity": 0.0, "halted": False,
            "halt_reason": "", "last_stop": {}}


def kill_file():
    return C.home() / "KILL"


def kill_switch_on() -> bool:
    return kill_file().exists()


def set_kill(on: bool, reason: str = "") -> None:
    if on:
        kill_file().write_text(reason or "manual halt")
    elif kill_file().exists():
        kill_file().unlink()


def load_state() -> dict:
    return {**_DEFAULT, **C.read_json(STATE, _DEFAULT)}


def update_equity(equity: float, cfg: dict) -> dict:
    """Roll the trading day, track peak equity and trip the drawdown breaker."""
    st = load_state()
    today = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    if st["day"] != today or st["day_start_equity"] <= 0:
        st["day"], st["day_start_equity"] = today, equity
    st["peak_equity"] = max(st["peak_equity"], equity)
    dd = (1 - equity / st["peak_equity"]) * 100 if st["peak_equity"] > 0 else 0.0
    if dd >= cfg["risk"]["max_drawdown_pct"] and not st["halted"]:
        st["halted"] = True
        st["halt_reason"] = f"Drawdown {dd:.1f}% reached the {cfg['risk']['max_drawdown_pct']}% limit."
    C.write_json(STATE, st)
    return st


def resume() -> dict:
    """Clear a drawdown halt. Peak equity resets so the breaker does not re-trip instantly."""
    st = load_state()
    st.update({"halted": False, "halt_reason": "", "peak_equity": 0.0})
    C.write_json(STATE, st)
    set_kill(False)
    return st


def note_stop_out(symbol: str) -> None:
    st = load_state()
    st["last_stop"][symbol] = C.now()
    C.write_json(STATE, st)


def status(equity: float, cfg: dict) -> dict:
    st = update_equity(equity, cfg)
    day_pnl = (equity / st["day_start_equity"] - 1) * 100 if st["day_start_equity"] else 0.0
    dd = (1 - equity / st["peak_equity"]) * 100 if st["peak_equity"] else 0.0
    return {"equity": round(equity, 2), "day_pnl_pct": round(day_pnl, 2), "drawdown_pct": round(dd, 2),
            "peak_equity": round(st["peak_equity"], 2), "halted": st["halted"], "halt_reason": st["halt_reason"],
            "kill_switch": kill_switch_on(),
            "daily_limit_hit": day_pnl <= -cfg["risk"]["daily_loss_limit_pct"],
            "can_open_trades": not (st["halted"] or kill_switch_on()
                                    or day_pnl <= -cfg["risk"]["daily_loss_limit_pct"])}


def position_size(equity: float, entry: float, stop: float, cfg: dict, cash: float | None = None,
                  current_exposure: float = 0.0, market: dict | None = None, feed=None,
                  symbol: str | None = None) -> dict:
    """Fixed-fractional sizing: lose risk_per_trade_pct of equity if the stop is hit,
    then cap by per-position, total-exposure and available-cash limits."""
    r = cfg["risk"]
    if stop is None or stop >= entry:
        return {"ok": False, "error": "A stop below the entry price is required to size a long position."}
    fee = cfg["fees"]["taker"]
    slip = cfg["fees"]["slippage_bps"] / 1e4
    risk_amount = equity * r["risk_per_trade_pct"] / 100
    # loss per unit includes round-trip fees and slippage, not just the price distance
    per_unit = (entry - stop) + entry * (2 * fee + 2 * slip)
    qty = risk_amount / per_unit
    caps = {"risk": qty * entry,
            "max_position": equity * r["max_position_pct"] / 100,
            "exposure_room": max(0.0, equity * r["max_total_exposure_pct"] / 100 - current_exposure)}
    if cash is not None:
        caps["cash"] = max(0.0, cash / (1 + fee + slip))
    binding = min(caps, key=caps.get)
    notional = caps[binding]
    qty = notional / entry
    if feed is not None and symbol:
        qty = feed.round_amount(symbol, qty)
        notional = qty * entry
    out = {"ok": True, "qty": qty, "notional": round(notional, 2), "entry": entry, "stop": stop,
           "stop_distance_pct": round((entry - stop) / entry * 100, 2),
           "risk_amount": round(qty * per_unit, 2), "risk_pct_of_equity": round(qty * per_unit / equity * 100, 3),
           "position_pct_of_equity": round(notional / equity * 100, 2), "limited_by": binding,
           "caps": {k: round(v, 2) for k, v in caps.items()}}
    m = market or {}
    if qty <= 0 or notional < (m.get("min_cost") or 0) or qty < (m.get("min_amount") or 0):
        out.update({"ok": False, "error": f"Sized order ({notional:.2f}) is below the exchange minimum "
                                          f"or no room is left under the '{binding}' cap."})
    return out


def pre_trade_check(side: str, symbol: str, qty: float, entry: float, account: dict, cfg: dict,
                    stop: float | None = None, target: float | None = None, ticker: dict | None = None,
                    market: dict | None = None) -> dict:
    """Return {approved, violations, warnings}. `account` comes from Broker.account()."""
    r = cfg["risk"]
    v, w = [], []
    pos = account["positions"]
    equity = account["equity"]
    if "/" not in symbol or ":" in symbol:
        v.append("Not a spot symbol (derivatives are refused).")
    elif symbol.split("/")[1] != cfg["quote"]:
        v.append(f"Quote currency must be {cfg['quote']} (config 'quote').")
    if qty <= 0 or entry <= 0:
        v.append("Quantity and price must be positive.")

    if side == "sell":
        held = pos.get(symbol, {}).get("qty", 0.0)
        if qty > held * 1.000001:
            v.append(f"Cannot sell {qty}: only {held} held. Spot trading never sells short.")
        return {"approved": not v, "violations": v, "warnings": w}

    st = status(equity, cfg)
    notional = qty * entry
    if st["kill_switch"]:
        v.append("Kill switch is on (.spotkit/KILL exists).")
    if st["halted"]:
        v.append(f"Trading halted: {st['halt_reason']} Resume explicitly after review.")
    if st["daily_limit_hit"]:
        v.append(f"Daily loss limit hit ({st['day_pnl_pct']}% today, limit -{r['daily_loss_limit_pct']}%).")
    if stop is None:
        v.append("No stop-loss given. Every entry needs a predefined exit.")
    elif stop >= entry:
        v.append("Stop must be below the entry for a long position.")
    else:
        fee, slip = cfg["fees"]["taker"], cfg["fees"]["slippage_bps"] / 1e4
        risk_pct = qty * ((entry - stop) + entry * 2 * (fee + slip)) / equity * 100
        if risk_pct > r["risk_per_trade_pct"] * 1.1:
            v.append(f"Trade risks {risk_pct:.2f}% of equity; limit is {r['risk_per_trade_pct']}%.")
        if target is not None:
            rr = (target - entry) / (entry - stop)
            if rr < r["min_reward_risk"]:
                v.append(f"Reward:risk {rr:.2f} is below the minimum {r['min_reward_risk']}.")
        else:
            w.append("No take-profit target; reward:risk was not checked.")
    held = symbol in pos
    if held and not r["allow_adding_to_position"]:
        v.append(f"Already holding {symbol}; adding to positions is disabled.")
    if not held and len(pos) >= r["max_open_positions"]:
        v.append(f"Max open positions reached ({r['max_open_positions']}).")
    existing = pos.get(symbol, {}).get("value", 0.0)
    if (existing + notional) / equity * 100 > r["max_position_pct"] * 1.02:
        v.append(f"Position would be {(existing + notional) / equity * 100:.1f}% of equity; cap is {r['max_position_pct']}%.")
    if (account["exposure"] + notional) / equity * 100 > r["max_total_exposure_pct"] * 1.02:
        v.append(f"Total exposure would exceed {r['max_total_exposure_pct']}% of equity.")
    if notional * (1 + cfg["fees"]["taker"]) > account["cash"] * 1.0001:
        v.append(f"Insufficient {cfg['quote']}: need {notional:.2f}, have {account['cash']:.2f}.")
    m = market or {}
    if notional < (m.get("min_cost") or 0) or qty < (m.get("min_amount") or 0):
        v.append("Order is below the exchange minimum size.")
    last = load_state()["last_stop"].get(symbol)
    if last and C.now() - last < r["cooldown_minutes_after_stop"] * 60:
        mins = int((r["cooldown_minutes_after_stop"] * 60 - (C.now() - last)) / 60)
        v.append(f"{symbol} was stopped out recently; cooldown has {mins} min left.")
    if ticker:
        if ticker.get("spread_bps") is not None and ticker["spread_bps"] > r["max_spread_bps"]:
            v.append(f"Spread {ticker['spread_bps']:.0f} bps exceeds {r['max_spread_bps']} bps.")
        qv = ticker.get("quote_volume_24h")
        if qv is not None and qv < r["min_quote_volume_24h"]:
            v.append(f"24h volume {qv:,.0f} is under the {r['min_quote_volume_24h']:,} liquidity floor.")
    return {"approved": not v, "violations": v, "warnings": w, "risk_status": st}
