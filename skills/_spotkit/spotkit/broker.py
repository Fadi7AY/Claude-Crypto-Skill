"""Order execution: a paper broker (default) and a live ccxt spot broker.

Both keep bot-managed positions (with their stops and targets) in the state
folder and write every fill to the journal. There is no withdrawal, margin,
futures or short-selling code path anywhere in this module.
"""
from __future__ import annotations

import os

from . import config as C
from . import journal as J
from . import risk as R


class Broker:
    mode = "paper"
    state_file = "paper_account.json"

    def __init__(self, cfg: dict, feed):
        self.cfg, self.feed = cfg, feed
        self.state = C.read_json(self.state_file, self._blank())

    def _blank(self) -> dict:
        return {"cash": float(self.cfg["paper"]["starting_balance"]), "positions": {}, "orders": [], "next_id": 1}

    def _save(self) -> None:
        C.write_json(self.state_file, self.state)

    # ---- account -----------------------------------------------------------
    def cash(self) -> float:
        return float(self.state["cash"])

    def account(self, prices: dict | None = None) -> dict:
        prices = prices or {}
        pos, exposure = {}, 0.0
        for sym, p in self.state["positions"].items():
            px = prices.get(sym) or self.feed.ticker(sym)["last"]
            value = p["qty"] * px
            exposure += value
            pos[sym] = {**p, "price": px, "value": round(value, 2),
                        "unrealized_pnl": round((px - p["avg_price"]) * p["qty"] - p["entry_fee"], 2),
                        "unrealized_pct": round((px / p["avg_price"] - 1) * 100, 2)}
        reserved = sum(o["qty"] * o["price"] for o in self.state["orders"] if o["side"] == "buy")
        cash = self.cash()
        equity = cash + reserved + exposure
        return {"mode": self.mode, "quote": self.cfg["quote"], "cash": round(cash, 2),
                "reserved_in_orders": round(reserved, 2), "exposure": round(exposure, 2),
                "equity": round(equity, 2),
                "exposure_pct": round(exposure / equity * 100, 2) if equity else 0.0,
                "positions": pos, "open_orders": self.state["orders"]}

    # ---- fills -------------------------------------------------------------
    def _open(self, symbol, qty, price, fee, meta) -> None:
        p = self.state["positions"].get(symbol)
        if p:
            tot = p["qty"] + qty
            p["avg_price"] = (p["avg_price"] * p["qty"] + price * qty) / tot
            p["qty"], p["entry_fee"] = tot, p["entry_fee"] + fee
            for k in ("stop", "take_profit"):
                if meta.get(k) is not None:
                    p[k] = meta[k]
        else:
            self.state["positions"][symbol] = {
                "qty": qty, "avg_price": price, "entry_fee": fee, "stop": meta.get("stop"),
                "initial_stop": meta.get("stop"), "take_profit": meta.get("take_profit"),
                "trail_atr_mult": meta.get("trail_atr_mult") or 0.0, "high_water": price,
                "opened_at": C.now(), "strategy": meta.get("strategy", "manual"),
                "reason": meta.get("reason", ""), "native_stop_id": None}
        self._save()
        J.event(self.mode, "buy_fill", symbol, qty=qty, price=price, fee=fee, **meta)

    def _close(self, symbol, qty, price, fee, reason) -> dict:
        p = self.state["positions"][symbol]
        frac = min(1.0, qty / p["qty"])
        entry_fee = p["entry_fee"] * frac
        tr = J.record_trade(self.mode, symbol, p["strategy"], p["opened_at"], qty, p["avg_price"], price,
                            entry_fee + fee, p["initial_stop"], p["reason"], reason)
        p["qty"] -= qty
        p["entry_fee"] -= entry_fee
        if p["qty"] <= p["qty"] * 1e-9 + 1e-12 or frac >= 0.999999:
            del self.state["positions"][symbol]
        self._save()
        J.event(self.mode, "sell_fill", symbol, qty=qty, price=price, fee=fee, reason=reason, pnl=tr["pnl"])
        if reason == "stop_loss":
            R.note_stop_out(symbol)
        return tr

    # ---- orders (paper implementation) --------------------------------------
    def buy(self, symbol, qty, order_type="market", price=None, **meta) -> dict:
        fees = self.cfg["fees"]
        t = self.feed.ticker(symbol)
        ask = t.get("ask") or t["last"]
        if order_type == "limit" and price is not None and price < ask:
            oid = self.state["next_id"]
            self.state["next_id"] += 1
            self.state["cash"] -= qty * price * (1 + fees["maker"])
            self.state["orders"].append({"id": oid, "symbol": symbol, "side": "buy", "qty": qty, "price": price,
                                         "created": C.now(), "meta": meta})
            self._save()
            J.event(self.mode, "limit_placed", symbol, id=oid, qty=qty, price=price)
            return {"ok": True, "status": "open", "order_id": oid, "symbol": symbol, "qty": qty, "limit": price}
        fill = ask * (1 + fees["slippage_bps"] / 1e4)
        fee = qty * fill * fees["taker"]
        if qty * fill + fee > self.cash() + 1e-9:
            return {"ok": False, "error": f"Insufficient {self.cfg['quote']}: need {qty * fill + fee:.2f}, have {self.cash():.2f}."}
        self.state["cash"] -= qty * fill + fee
        self._open(symbol, qty, fill, fee, meta)
        return {"ok": True, "status": "filled", "symbol": symbol, "side": "buy", "qty": qty,
                "price": fill, "fee": round(fee, 4), "cost": round(qty * fill + fee, 2)}

    def sell(self, symbol, qty=None, reason="manual", fill_price=None) -> dict:
        p = self.state["positions"].get(symbol)
        if not p:
            return {"ok": False, "error": f"No {symbol} position to sell."}
        qty = p["qty"] if qty is None else min(qty, p["qty"])
        fees = self.cfg["fees"]
        if fill_price is None:
            t = self.feed.ticker(symbol)
            fill_price = t.get("bid") or t["last"]
        fill = fill_price * (1 - fees["slippage_bps"] / 1e4)
        fee = qty * fill * fees["taker"]
        self.state["cash"] += qty * fill - fee
        tr = self._close(symbol, qty, fill, fee, reason)
        return {"ok": True, "status": "filled", "symbol": symbol, "side": "sell", "qty": qty, "price": fill,
                "fee": round(fee, 4), "pnl": round(tr["pnl"], 2), "pnl_pct": round(tr["pnl_pct"], 2),
                "r_multiple": None if tr["r_multiple"] is None else round(tr["r_multiple"], 2), "reason": reason}

    def cancel(self, order_id: int) -> dict:
        for o in self.state["orders"]:
            if o["id"] == order_id:
                self.state["orders"].remove(o)
                self.state["cash"] += o["qty"] * o["price"] * (1 + self.cfg["fees"]["maker"])
                self._save()
                J.event(self.mode, "limit_cancelled", o["symbol"], id=order_id)
                return {"ok": True, "cancelled": order_id}
        return {"ok": False, "error": f"No open order {order_id}."}

    def sync(self) -> list[dict]:
        """Fill resting paper limit orders that the market has traded through."""
        filled = []
        for o in list(self.state["orders"]):
            if self.feed.ticker(o["symbol"])["last"] <= o["price"]:
                self.state["orders"].remove(o)
                fee = o["qty"] * o["price"] * self.cfg["fees"]["maker"]
                self._open(o["symbol"], o["qty"], o["price"], fee, o["meta"])
                filled.append({"order_id": o["id"], "symbol": o["symbol"], "qty": o["qty"], "price": o["price"]})
        return filled

    # ---- protective exits ----------------------------------------------------
    def set_stop(self, symbol, stop=None, take_profit=None, trail_atr_mult=None) -> dict:
        p = self.state["positions"].get(symbol)
        if not p:
            return {"ok": False, "error": f"No {symbol} position."}
        if stop is not None:
            p["stop"] = stop
            p["initial_stop"] = p["initial_stop"] or stop
        if take_profit is not None:
            p["take_profit"] = take_profit
        if trail_atr_mult is not None:
            p["trail_atr_mult"] = trail_atr_mult
        self._save()
        J.event(self.mode, "stop_updated", symbol, stop=p["stop"], take_profit=p["take_profit"])
        return {"ok": True, "symbol": symbol, "stop": p["stop"], "take_profit": p["take_profit"],
                "trail_atr_mult": p["trail_atr_mult"]}

    def check_exits(self, bars: dict) -> list[dict]:
        """Enforce stops/targets. bars = {symbol: {open, high, low, close, atr}} for the
        candles since the last check. Stops are checked before targets (conservative)."""
        out = []
        for sym in list(self.state["positions"]):
            p, b = self.state["positions"][sym], bars.get(sym)
            if not b:
                continue
            if p["stop"] is not None and b["low"] <= p["stop"]:
                out.append(self._exit(sym, min(p["stop"], b["open"]), "stop_loss"))
                continue
            if p["take_profit"] is not None and b["high"] >= p["take_profit"]:
                out.append(self._exit(sym, max(p["take_profit"], b["open"]), "take_profit"))
                continue
            if p["trail_atr_mult"] and b.get("atr"):
                p["high_water"] = max(p["high_water"], b["high"])
                trail = p["high_water"] - p["trail_atr_mult"] * b["atr"]
                if p["stop"] is None or trail > p["stop"]:
                    p["stop"] = trail
                    self._save()
        return out

    def _exit(self, symbol, trigger_price, reason) -> dict:
        return self.sell(symbol, None, reason, fill_price=trigger_price)


class LiveBroker(Broker):
    """Real spot orders through ccxt. Requires SPOTKIT_LIVE=1 plus API keys in the environment."""

    mode = "live"
    state_file = "live_positions.json"

    def __init__(self, cfg: dict, feed):
        if os.environ.get("SPOTKIT_LIVE") != "1":
            raise RuntimeError("Live trading is locked. Set SPOTKIT_LIVE=1 in the environment to unlock it.")
        if cfg.get("mode") != "live":
            raise RuntimeError("config.json has mode='paper'. Set mode to 'live' to trade real funds.")
        if getattr(feed, "demo", False):
            raise RuntimeError("Live trading cannot use the demo feed.")
        super().__init__(cfg, feed)
        self.ex = feed.ex

    def _blank(self) -> dict:
        return {"cash": 0.0, "positions": {}, "orders": [], "next_id": 1}

    def cash(self) -> float:
        return float((self.ex.fetch_balance().get(self.cfg["quote"]) or {}).get("free") or 0.0)

    def _fill(self, order: dict, symbol: str) -> tuple[float, float, float, float]:
        """Return (filled_qty, avg_price, fee_in_quote, fee_in_base) for an order."""
        if order.get("average") is None or order.get("filled") is None:
            try:
                order = self.ex.fetch_order(order["id"], symbol)
            except Exception:
                pass
        filled = float(order.get("filled") or 0.0)
        avg = float(order.get("average") or order.get("price") or 0.0)
        base = symbol.split("/")[0]
        fq = fb = 0.0
        for f in order.get("fees") or ([order["fee"]] if order.get("fee") else []):
            if not f or f.get("cost") is None:
                continue
            if f.get("currency") == base:
                fb += float(f["cost"])
            elif f.get("currency") == self.cfg["quote"]:
                fq += float(f["cost"])
        if fq == 0 and fb == 0:
            fq = filled * avg * self.cfg["fees"]["taker"]  # exchange did not report the fee; estimate
        return filled, avg, fq, fb

    def buy(self, symbol, qty, order_type="market", price=None, **meta) -> dict:
        self.feed.market(symbol)  # validates spot listing
        qty = self.feed.round_amount(symbol, qty)
        if order_type == "limit":
            price = self.feed.round_price(symbol, price)
        o = self.ex.create_order(symbol, order_type, "buy", qty, price if order_type == "limit" else None)
        J.event("live", "order_sent", symbol, id=o.get("id"), side="buy", type=order_type, qty=qty, price=price)
        filled, avg, fq, fb = self._fill(o, symbol)
        if filled <= 0:
            self.state["orders"].append({"id": o.get("id"), "symbol": symbol, "side": "buy", "qty": qty,
                                         "price": price, "created": C.now(), "meta": meta})
            self._save()
            return {"ok": True, "status": "open", "order_id": o.get("id"), "symbol": symbol, "qty": qty, "limit": price}
        net = filled - fb  # fees charged in the base coin reduce what can later be sold
        self._open(symbol, net, avg, fq + fb * avg, meta)
        res = {"ok": True, "status": "filled", "order_id": o.get("id"), "symbol": symbol, "side": "buy",
               "qty": net, "price": avg, "fee": round(fq + fb * avg, 6), "cost": round(filled * avg + fq, 2)}
        if self.cfg["live"].get("exchange_stops") and meta.get("stop"):
            res["native_stop"] = self._native_stop(symbol, net, meta["stop"])
        return res

    def _native_stop(self, symbol, qty, stop) -> dict:
        """Best effort: exchanges differ on spot stop orders, so failure falls back to the soft stop."""
        try:
            o = self.ex.create_order(symbol, "market", "sell", self.feed.round_amount(symbol, qty), None,
                                     {"stopLossPrice": self.feed.round_price(symbol, stop)})
            self.state["positions"][symbol]["native_stop_id"] = o.get("id")
            self._save()
            return {"placed": True, "id": o.get("id")}
        except Exception as e:  # noqa: BLE001
            return {"placed": False, "warning": f"Native stop not placed ({type(e).__name__}: {e}). "
                                                 "The bot's soft stop still applies while the bot is running."}

    def _cancel_native(self, symbol) -> None:
        p = self.state["positions"].get(symbol) or {}
        if p.get("native_stop_id"):
            try:
                self.ex.cancel_order(p["native_stop_id"], symbol)
            except Exception:
                pass
            p["native_stop_id"] = None

    def sell(self, symbol, qty=None, reason="manual", fill_price=None) -> dict:
        p = self.state["positions"].get(symbol)
        if not p:
            return {"ok": False, "error": f"No bot-managed {symbol} position. This tool only sells what it bought."}
        self._cancel_native(symbol)
        base = symbol.split("/")[0]
        free = float((self.ex.fetch_balance().get(base) or {}).get("free") or 0.0)
        want = p["qty"] if qty is None else min(qty, p["qty"])
        amt = self.feed.round_amount(symbol, min(want, free))
        if amt <= 0:
            return {"ok": False, "error": f"No free {base} balance to sell (wanted {want}, free {free})."}
        o = self.ex.create_order(symbol, "market", "sell", amt)
        J.event("live", "order_sent", symbol, id=o.get("id"), side="sell", type="market", qty=amt, reason=reason)
        filled, avg, fq, fb = self._fill(o, symbol)
        if filled <= 0:
            return {"ok": False, "error": "Sell order sent but no fill was reported; check the exchange.", "order_id": o.get("id")}
        # if the whole intended amount was sold, close the position fully even when dust remains
        close_qty = p["qty"] if (qty is None or want >= p["qty"]) else filled
        tr = self._close(symbol, close_qty, avg * filled / close_qty, fq + fb * avg, reason)
        return {"ok": True, "status": "filled", "order_id": o.get("id"), "symbol": symbol, "side": "sell",
                "qty": filled, "price": avg, "fee": round(fq + fb * avg, 6), "pnl": round(tr["pnl"], 2),
                "pnl_pct": round(tr["pnl_pct"], 2), "reason": reason}

    def _exit(self, symbol, trigger_price, reason) -> dict:
        return self.sell(symbol, None, reason)  # live exits are market orders at the current price

    def cancel(self, order_id) -> dict:
        for o in self.state["orders"]:
            if str(o["id"]) == str(order_id):
                self.ex.cancel_order(o["id"], o["symbol"])
                self.state["orders"].remove(o)
                self._save()
                J.event("live", "limit_cancelled", o["symbol"], id=order_id)
                return {"ok": True, "cancelled": order_id}
        return {"ok": False, "error": f"No tracked open order {order_id}."}

    def sync(self) -> list[dict]:
        filled = []
        for o in list(self.state["orders"]):
            ro = self.ex.fetch_order(o["id"], o["symbol"])
            if ro.get("status") in ("closed", "canceled", "cancelled", "expired", "rejected"):
                self.state["orders"].remove(o)
                qty, avg, fq, fb = self._fill(ro, o["symbol"])
                if qty > 0:
                    self._open(o["symbol"], qty - fb, avg, fq + fb * avg, o["meta"])
                    filled.append({"order_id": o["id"], "symbol": o["symbol"], "qty": qty - fb, "price": avg})
                self._save()
        return filled


def get_broker(cfg: dict, feed, live: bool = False) -> Broker:
    return LiveBroker(cfg, feed) if live else Broker(cfg, feed)
