"""Offline test suite: runs with no network and no API keys.

    python3 tests/test_offline.py

Covers indicators, look-ahead safety, backtest accounting, risk rules, the paper
broker, the live broker against a fake exchange, the bot cycle, the webhook
handler and the TradingView wrappers (with the network call stubbed).
"""
import os
import sys
import tempfile
import types
from pathlib import Path

os.environ["SPOTKIT_HOME"] = tempfile.mkdtemp(prefix="spotkit_test_")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills" / "_spotkit"))
sys.path.insert(0, str(ROOT / "skills" / "tradingview-analysis" / "scripts"))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from spotkit import backtest as B, bot, config as C, indicators as ind, journal as J, risk as R, signals as sig, tv  # noqa: E402
from spotkit.broker import Broker, LiveBroker  # noqa: E402
from spotkit.exchange import DemoFeed, check_spot_symbol, closed_only  # noqa: E402

PASSED = []


def test(fn):
    fn()
    PASSED.append(fn.__name__)
    print(f"  ok  {fn.__name__}")
    return fn


def fresh():
    for f in C.home().glob("*"):
        f.unlink()
    return C.load_config()


feed = DemoFeed()
DF = closed_only(feed.ohlcv("BTC/USDT", "1h", limit=3000), "1h")


@test
def indicators_match_reference_formulas():
    c = DF["close"]
    e = ind.ema(c, 20)
    k = 2 / 21
    ref = [c.iloc[0]]
    for x in c.iloc[1:]:
        ref.append(ref[-1] + k * (x - ref[-1]))
    assert abs(e.iloc[-1] - ref[-1]) < 1e-6
    r = ind.rsi(c)
    assert r.dropna().between(0, 100).all()
    # Wilder RSI by hand on the last value
    d = c.diff().dropna()
    up, dn = d.clip(lower=0), -d.clip(upper=0)
    au, ad = up.iloc[0], dn.iloc[0]
    for u, v in zip(up.iloc[1:], dn.iloc[1:]):
        au, ad = (au * 13 + u) / 14, (ad * 13 + v) / 14
    assert abs(r.iloc[-1] - (100 - 100 / (1 + au / ad))) < 1e-3
    a = ind.atr(DF)
    assert (a.dropna() > 0).all()
    bb = ind.bbands(c).dropna()
    assert (bb["bb_upper"] >= bb["bb_mid"]).all() and (bb["bb_mid"] >= bb["bb_lower"]).all()
    st = ind.supertrend(DF).dropna()
    up_ok = (st["supertrend"] <= DF["close"].loc[st.index] + 1e-9) | (st["supertrend_dir"] == -1)
    assert up_ok.mean() > 0.95


@test
def score_is_bounded_and_has_no_lookahead():
    d = ind.add_all(DF)
    s = sig.score_series(d)
    assert s.between(-100, 100).all()
    cut = DF.iloc[:2000]
    s2 = sig.score_series(ind.add_all(cut))
    # the score at bar t must not change when later candles are appended
    assert np.allclose(s.iloc[1500:2000], s2.iloc[1500:2000], atol=1e-6)


@test
def analyze_gives_consistent_plan():
    a = sig.analyze(DF)
    assert a["ok"] and a["plan"]["stop"] < a["plan"]["entry"] < a["plan"]["targets"]["1R"]
    assert sig.analyze(DF.tail(30))["ok"] is False
    m = sig.multi_timeframe(feed, "ETH/USDT", ["1h", "4h"])
    assert m["ok"] and m["signal"] in {"strong_buy", "buy", "neutral", "avoid", "strong_avoid"}


@test
def backtest_accounting_is_exact():
    eng = {"initial_cash": 10000.0}
    for name in B.STRATEGIES:
        r = B.run(DF, name, None, eng, "1h")
        if not r["open_position_at_end"]:
            assert abs(sum(t["pnl"] for t in r["_trades"]) - (r["final_equity"] - 10000)) < 0.02, name
        for t in r["_trades"]:
            assert t["exit_time"] >= t["entry_time"]
    # zero-cost run must beat (or equal) the same run with costs
    free = B.run(DF, "confluence", None, {"fee": 0, "slippage_bps": 0}, "1h")
    paid = B.run(DF, "confluence", None, {"fee": 0.002, "slippage_bps": 20}, "1h")
    assert free["total_return_pct"] > paid["total_return_pct"]


@test
def backtest_loss_per_stop_matches_risk_setting():
    r = B.run(DF, "donchian_breakout", None, {"risk_per_trade_pct": 1.0, "max_position_pct": 100, "take_profit_r": 0}, "1h")
    stops = [t for t in r["_trades"] if t["reason"] == "stop_loss"]
    assert stops, "expected some stop-outs"
    # a clean stop-out should cost about 1R (gaps can make it slightly worse)
    assert -1.6 < np.median([t["r"] for t in stops]) <= -0.9


@test
def walk_forward_runs_and_reports():
    w = B.walk_forward(DF, "ema_cross", None, "1h", folds=3)
    assert len(w["folds"]) == 3 and isinstance(w["robust"], bool)
    for f in w["folds"]:
        assert f["train"][1] < f["test"][0]  # test window strictly after training window


@test
def position_sizing_respects_every_cap():
    cfg = fresh()
    s = R.position_size(10000, 100, 98, cfg)                       # 2% stop -> risk cap would be 50% of equity
    assert s["limited_by"] == "max_position" and abs(s["notional"] - 2000) < 1
    s = R.position_size(10000, 100, 60, cfg)                       # wide stop -> risk binds
    assert s["limited_by"] == "risk" and abs(s["risk_pct_of_equity"] - 1.0) < 0.01
    s = R.position_size(10000, 100, 98, cfg, cash=500)
    assert s["limited_by"] == "cash" and s["notional"] < 500
    s = R.position_size(10000, 100, 98, cfg, current_exposure=5900)
    assert s["limited_by"] == "exposure_room" and s["notional"] <= 100.01
    assert R.position_size(10000, 100, 101, cfg)["ok"] is False


@test
def risk_checks_block_bad_trades():
    cfg = fresh()
    acct = {"equity": 10000, "cash": 10000, "exposure": 0, "positions": {}}
    ok = R.pre_trade_check("buy", "BTC/USDT", 0.05, 30000, acct, cfg, stop=29000, target=32500)
    assert ok["approved"], ok
    bad = lambda **k: R.pre_trade_check("buy", "BTC/USDT", **{"qty": 0.05, "entry": 30000, "account": acct,
                                                                "cfg": cfg, "stop": 29000, "target": 32500, **k})
    assert not bad(stop=None)["approved"]
    assert not bad(target=30500)["approved"]                      # reward:risk 0.5
    assert not bad(qty=0.2)["approved"]                           # 60% position
    assert not bad(ticker={"spread_bps": 80, "quote_volume_24h": 1e9})["approved"]
    assert not bad(ticker={"spread_bps": 2, "quote_volume_24h": 1000})["approved"]
    assert not R.pre_trade_check("buy", "BTC/USDT:USDT", 0.05, 30000, acct, cfg, stop=29000)["approved"]
    assert not R.pre_trade_check("buy", "BTC/EUR", 0.05, 30000, acct, cfg, stop=29000)["approved"]
    assert not R.pre_trade_check("sell", "BTC/USDT", 1, 30000, acct, cfg)["approved"]   # no shorting
    R.set_kill(True)
    assert not bad()["approved"]
    R.set_kill(False)
    R.note_stop_out("BTC/USDT")
    assert any("cooldown" in v for v in bad()["violations"])


@test
def drawdown_and_daily_limits_halt_trading():
    cfg = fresh()
    R.update_equity(10000, cfg)
    st = R.status(9650, cfg)                                       # -3.5% on the day
    assert st["daily_limit_hit"] and not st["can_open_trades"] and not st["halted"]
    st = R.status(8400, cfg)                                       # -16% from peak
    assert st["halted"]
    assert R.status(9900, cfg)["halted"], "halt must persist until resumed"
    R.resume()
    assert R.status(9900, cfg)["can_open_trades"]


@test
def paper_broker_round_trip_and_journal():
    cfg = fresh()
    b = Broker(cfg, feed)
    px = feed.ticker("ETH/USDT")["last"]
    r = b.buy("ETH/USDT", 1000 / px, stop=px * 0.97, take_profit=px * 1.06, strategy="t", reason="unit")
    assert r["ok"] and abs(b.cash() - (10000 - r["cost"])) < 0.01
    a = b.account()
    assert abs(a["equity"] - 10000) < 5 and "ETH/USDT" in a["positions"]
    assert Broker(cfg, feed).state["positions"]["ETH/USDT"]["stop"] == px * 0.97   # persisted
    # stop-out via check_exits fills at the stop (gap-aware) and starts the cooldown
    ex = b.check_exits({"ETH/USDT": {"open": px, "high": px, "low": px * 0.96, "close": px * 0.965, "atr": px * 0.01}})
    assert ex[0]["reason"] == "stop_loss" and ex[0]["pnl"] < 0 and not b.state["positions"]
    t = J.trades()[0]
    assert t["exit_reason"] == "stop_loss" and -1.3 < t["r_multiple"] < -0.9
    assert "ETH/USDT" in R.load_state()["last_stop"]
    assert b.sell("ETH/USDT")["ok"] is False
    assert b.buy("BTC/USDT", 10)["ok"] is False                    # cannot afford
    # take-profit and trailing stop
    px = feed.ticker("SOL/USDT")["last"]
    b.buy("SOL/USDT", 500 / px, stop=px * 0.95, take_profit=px * 1.05, trail_atr_mult=2.0)
    assert b.check_exits({"SOL/USDT": {"open": px, "high": px * 1.02, "low": px, "close": px * 1.02, "atr": px * 0.01}}) == []
    assert abs(b.state["positions"]["SOL/USDT"]["stop"] - px * 1.0) < px * 1e-6     # trailed up to hw - 2 ATR
    ex = b.check_exits({"SOL/USDT": {"open": px * 1.02, "high": px * 1.06, "low": px * 1.02, "close": px * 1.05, "atr": px * 0.01}})
    assert ex[0]["reason"] == "take_profit" and ex[0]["pnl"] > 0
    # limit order lifecycle
    o = b.buy("XRP/USDT", 100, order_type="limit", price=feed.ticker("XRP/USDT")["last"] * 0.9, stop=0.0001)
    cash_after = b.cash()
    assert o["status"] == "open" and b.cancel(o["order_id"])["ok"] and b.cash() > cash_after
    assert J.stats()["overall"]["trades"] == 2


class FakeEx:
    """Minimal ccxt stand-in; charges the buy fee in the base coin like Binance does."""

    def __init__(self):
        self.bal = {"USDT": {"free": 5000.0}, "BTC": {"free": 0.0}}
        self.orders, self.px, self.n = {}, 50000.0, 0
        self.markets = {"BTC/USDT": {"spot": True, "base": "BTC", "quote": "USDT",
                                     "limits": {"amount": {"min": 0.0001}, "cost": {"min": 10}}}}

    def load_markets(self): return self.markets
    def fetch_balance(self): return self.bal
    def amount_to_precision(self, s, a): return f"{int(a * 1e5) / 1e5:.5f}"
    def price_to_precision(self, s, p): return f"{p:.2f}"
    def fetch_ticker(self, s): return {"last": self.px, "bid": self.px - 1, "ask": self.px + 1, "quoteVolume": 1e9}

    def create_order(self, symbol, type_, side, amount, price=None, params=None):
        self.n += 1
        o = {"id": str(self.n), "symbol": symbol, "side": side, "type": type_, "amount": amount, "params": params}
        if params and "stopLossPrice" in params:
            raise RuntimeError("stop orders unsupported on fake exchange")
        if type_ == "limit":
            o.update({"status": "open", "filled": 0.0, "average": None})
        elif side == "buy":
            self.bal["USDT"]["free"] -= amount * self.px
            self.bal["BTC"]["free"] += amount * 0.999
            o.update({"status": "closed", "filled": amount, "average": self.px,
                      "fees": [{"currency": "BTC", "cost": amount * 0.001}]})
        else:
            assert amount <= self.bal["BTC"]["free"] + 1e-12, "exchange would reject: insufficient balance"
            self.bal["BTC"]["free"] -= amount
            self.bal["USDT"]["free"] += amount * self.px * 0.999
            o.update({"status": "closed", "filled": amount, "average": self.px,
                      "fees": [{"currency": "USDT", "cost": amount * self.px * 0.001}]})
        self.orders[o["id"]] = o
        return o

    def fetch_order(self, id_, symbol=None): return self.orders[id_]
    def cancel_order(self, id_, symbol=None): self.orders[id_]["status"] = "canceled"


@test
def live_broker_is_locked_then_trades_against_fake_exchange():
    from spotkit.exchange import CcxtFeed
    cfg = fresh()
    lf = CcxtFeed.__new__(CcxtFeed)
    lf.id, lf.ex, lf._loaded = "fake", FakeEx(), True
    os.environ.pop("SPOTKIT_LIVE", None)
    for setup in ({}, {"SPOTKIT_LIVE": "1"}):
        os.environ.update(setup)
        try:
            LiveBroker(cfg, lf)
            raise AssertionError("live broker must refuse without both locks")
        except RuntimeError:
            pass
    cfg["mode"] = "live"
    try:
        LiveBroker(cfg, feed)
        raise AssertionError("demo feed must be refused for live")
    except RuntimeError:
        pass
    cfg["live"]["exchange_stops"] = True
    b = LiveBroker(cfg, lf)
    assert b.cash() == 5000.0
    r = b.buy("BTC/USDT", 0.02, stop=48000, take_profit=54000, strategy="t", reason="unit")
    assert r["ok"] and abs(r["qty"] - 0.02 * 0.999) < 1e-9          # base-coin fee deducted from position
    assert r["native_stop"]["placed"] is False                      # falls back to the soft stop
    assert b.state["positions"]["BTC/USDT"]["stop"] == 48000
    lf.ex.px = 47000.0
    ex = b.check_exits({"BTC/USDT": {"open": 49000, "high": 49000, "low": 47000, "close": 47000, "atr": 500}})
    assert ex[0]["ok"] and ex[0]["reason"] == "stop_loss" and ex[0]["pnl"] < 0
    assert not b.state["positions"] and J.trades("live")[0]["exit_reason"] == "stop_loss"
    o = b.buy("BTC/USDT", 0.01, order_type="limit", price=40000.0, stop=39000)
    assert o["status"] == "open" and b.cancel(o["order_id"])["ok"]
    assert b.sell("BTC/USDT")["ok"] is False                        # only sells what it bought
    os.environ.pop("SPOTKIT_LIVE", None)
    try:
        check_spot_symbol("BTC/USDT:USDT")
        raise AssertionError("derivative symbol accepted")
    except ValueError:
        pass


@test
def bot_cycle_trades_once_per_bar_and_obeys_limits():
    cfg = fresh()
    cfg["watchlist"] = [f"{b}/USDT" for b in DemoFeed.SYMBOLS]
    cfg["strategy"]["entry_score"] = -100                           # force entries everywhere
    cfg["strategy"]["require_htf_trend"] = False
    b = Broker(cfg, feed)
    dry = bot.cycle(cfg, feed, b, dry_run=True)
    assert dry["entries"] and not b.state["positions"], "dry run must not trade"
    rep = bot.cycle(cfg, feed, b)
    assert not rep["errors"], rep["errors"]
    acct = b.account()
    assert 1 <= len(acct["positions"]) <= cfg["risk"]["max_open_positions"]
    assert acct["exposure_pct"] <= cfg["risk"]["max_total_exposure_pct"] + 0.5
    for p in acct["positions"].values():
        assert p["stop"] is not None and p["stop"] < p["avg_price"]
        assert p["value"] / acct["equity"] * 100 <= cfg["risk"]["max_position_pct"] + 0.5
    n = len(acct["positions"])
    rep2 = bot.cycle(cfg, feed, b)
    assert not rep2["entries"] and len(b.state["positions"]) == n   # same bar -> no new trades
    # kill switch blocks entries even on a new bar
    R.set_kill(True)
    C.write_json(bot.STATE, {"last_check": 0.0, "last_bar": {}})
    for s in list(b.state["positions"]):
        b.sell(s)
    rep3 = bot.cycle(cfg, feed, b)
    assert not rep3["entries"] and any("Kill switch" in s["why"] for s in rep3["skipped"])
    R.set_kill(False)
    # signal exit: with an impossible exit threshold every held position is closed on the next new bar
    cfg["risk"]["cooldown_minutes_after_stop"] = 0
    C.write_json(bot.STATE, {"last_check": 0.0, "last_bar": {}})
    bot.cycle(cfg, feed, b)
    assert b.state["positions"]
    cfg["strategy"]["exit_score"], cfg["strategy"]["entry_score"] = 101, 1000
    for p in b.state["positions"].values():
        p["stop"], p["take_profit"] = 1e-9, None
    C.write_json(bot.STATE, {"last_check": C.now(), "last_bar": {}})
    rep4 = bot.cycle(cfg, feed, b)
    assert not rep4["errors"], rep4["errors"]
    assert not b.state["positions"], (rep4["exits"], rep4["skipped"])
    assert all("signal_exit" in e["reason"] for e in rep4["exits"])


@test
def webhook_validates_and_routes_through_risk():
    from tv_webhook import handle, normalize
    assert normalize("BINANCE:BTCUSDT", "USDT") == "BTC/USDT" and normalize("eth-usdt", "USDT") == "ETH/USDT"
    cfg = fresh()
    cfg["risk"]["cooldown_minutes_after_stop"] = 0
    b = Broker(cfg, feed)
    S = "s3cret-s3cret"
    code, _ = handle({"secret": "nope", "action": "buy", "symbol": "BTCUSDT"}, cfg, feed, b, S)
    assert code == 403 and not b.state["positions"]
    code, body = handle({"secret": S, "action": "buy", "symbol": "BINANCE:BTCUSDT"}, cfg, feed, b, S)
    assert code == 200 and body["ok"] and "BTC/USDT" in b.state["positions"]
    code, body = handle({"secret": S, "action": "buy", "symbol": "BTCUSDT"}, cfg, feed, b, S)
    assert body["status"] == "rejected"                             # already holding
    code, body = handle({"secret": S, "action": "sell", "symbol": "BTCUSDT"}, cfg, feed, b, S)
    assert body["ok"] and body["reason"] == "tradingview_alert" and not b.state["positions"]
    assert handle({"secret": S, "action": "short", "symbol": "BTCUSDT"}, cfg, feed, b, S)[0] == 400
    assert handle({"secret": "", "action": "buy", "symbol": "BTCUSDT"}, cfg, feed, b, "")[0] == 403


@test
def tradingview_wrappers_parse_library_output():
    import tradingview_screener as tvs
    import tradingview_ta as tta

    class A:
        summary = {"RECOMMENDATION": "STRONG_BUY", "BUY": 17, "SELL": 1, "NEUTRAL": 8}
        oscillators = {"RECOMMENDATION": "BUY"}
        moving_averages = {"RECOMMENDATION": "STRONG_BUY"}
        indicators = {"close": 100.0, "RSI": 61.2, "ADX": 30.1}

    seen = {}
    orig = tta.TA_Handler.get_analysis
    tta.TA_Handler.get_analysis = lambda self: (seen.update(sym=self.symbol, ex=self.exchange, iv=self.interval) or A())
    try:
        r = tv.rating("BTC/USDT", ["1h", "1d"], "BINANCE")
        assert r["ok"] and r["alignment"] == "bullish" and r["tv_symbol"] == "BINANCE:BTCUSDT"
        assert seen == {"sym": "BTCUSDT", "ex": "BINANCE", "iv": "1d"}
        assert r["timeframes"]["1h"]["counts"] == {"buy": 17, "neutral": 8, "sell": 1}
        assert tv.confirms_long("BTC/USDT", "1h")[0] is True
        assert tv.rating("BTC/USDT", ["3h"])["ok"] is False        # unsupported interval reported, not raised
    finally:
        tta.TA_Handler.get_analysis = orig
    # screener: build the real query objects, stub only the HTTP call
    captured = {}
    orig = tvs.Query.get_scanner_data

    def fake(self, **kw):
        captured["q"] = self.query
        return 2, pd.DataFrame([{"ticker": "BINANCE:SOLUSDT", "name": "SOLUSDT", "close": 150.0, "change": 4.2,
                                 "Value.Traded": 9e8, "RSI": 63, "ADX": 28, "EMA50": 140, "EMA200": 120,
                                 "relative_volume_10d_calc": 1.4, "Recommend.All": 0.62}])
    tvs.Query.get_scanner_data = fake
    try:
        for name in tv.SCREENS:
            s = tv.screen(name, "BINANCE", "USDT", 5)
            assert s["ok"] and s["results"][0]["symbol"] == "SOL/USDT" and s["results"][0]["tv_rating"] == "STRONG_BUY"
        assert captured["q"]["markets"] == ["crypto"] and captured["q"]["range"] == [0, 5]
    finally:
        tvs.Query.get_scanner_data = orig


print(f"\n{len(PASSED)} tests passed")
