"""Long-only spot backtester with realistic fills, plus walk-forward validation.

Rules that keep results honest:
- signals are computed on a closed bar and filled at the NEXT bar's open (no look-ahead)
- fees and slippage on every fill
- if a bar touches both stop and target, the stop is assumed to fill first
- position size comes from the same fixed-fractional risk rule the live bot uses
"""
from __future__ import annotations

import itertools

import numpy as np
import pandas as pd

from . import indicators as ind
from . import signals as sig
from .exchange import tf_seconds


# ---- strategies: each returns (entry, exit) boolean Series on an add_all frame ----
def s_confluence(d, entry_score=40, exit_score=-20):
    sc = sig.score_series(d)
    return sc >= entry_score, sc <= exit_score


def s_ema_cross(d, fast=20, slow=50, trend=200):
    f, s, t = ind.ema(d["close"], fast), ind.ema(d["close"], slow), ind.ema(d["close"], trend)
    return (f > s) & (f.shift() <= s.shift()) & (d["close"] > t), f < s


def s_rsi_reversion(d, rsi_len=14, buy=30, sell=55, trend=200):
    r, t = ind.rsi(d["close"], rsi_len), ind.ema(d["close"], trend)
    return (r > buy) & (r.shift() <= buy) & (d["close"] > t), r >= sell


def s_donchian_breakout(d, entry=20, exit=10):
    hi, lo = d["high"].rolling(entry).max().shift(), d["low"].rolling(exit).min().shift()
    return d["close"] > hi, d["close"] < lo


def s_macd_trend(d, fast=12, slow=26, signal=9, trend=200):
    m = ind.macd(d["close"], fast, slow, signal)
    t = ind.ema(d["close"], trend)
    up = (m["macd_hist"] > 0) & (m["macd_hist"].shift() <= 0)
    return up & (d["close"] > t), m["macd_hist"] < 0


STRATEGIES = {
    "confluence": (s_confluence, {"entry_score": [30, 40, 50], "exit_score": [-30, -20, 0]}),
    "ema_cross": (s_ema_cross, {"fast": [10, 20], "slow": [50, 100]}),
    "rsi_reversion": (s_rsi_reversion, {"buy": [25, 30, 35], "sell": [50, 55, 65]}),
    "donchian_breakout": (s_donchian_breakout, {"entry": [20, 40, 55], "exit": [10, 20]}),
    "macd_trend": (s_macd_trend, {"trend": [100, 200]}),
}

ENGINE_DEFAULTS = {"initial_cash": 10000.0, "fee": 0.001, "slippage_bps": 5, "risk_per_trade_pct": 1.0,
                   "max_position_pct": 20.0, "stop_atr_mult": 2.0, "take_profit_r": 2.0, "trail_atr_mult": 0.0}


def engine_from_config(cfg: dict) -> dict:
    r = cfg["risk"]
    return {"initial_cash": cfg["paper"]["starting_balance"], "fee": cfg["fees"]["taker"],
            "slippage_bps": cfg["fees"]["slippage_bps"], "risk_per_trade_pct": r["risk_per_trade_pct"],
            "max_position_pct": r["max_position_pct"], "stop_atr_mult": r["stop_atr_mult"],
            "take_profit_r": r["take_profit_r"], "trail_atr_mult": r["trail_atr_mult"]}


def run(df: pd.DataFrame, strategy: str, params: dict | None = None, engine: dict | None = None,
        timeframe: str = "1h", prepared: pd.DataFrame | None = None, start=None, end=None) -> dict:
    """Run one backtest. `prepared` lets walk-forward reuse indicators computed on the
    full history (so a test window still has warmed-up EMAs) while trading only [start, end)."""
    if strategy not in STRATEGIES:
        raise ValueError(f"Unknown strategy '{strategy}'. Options: {sorted(STRATEGIES)}")
    e = {**ENGINE_DEFAULTS, **(engine or {})}
    d = prepared if prepared is not None else ind.add_all(df)
    entry, exit_ = STRATEGIES[strategy][0](d, **(params or {}))
    entry, exit_ = entry.fillna(False).to_numpy(), exit_.fillna(False).to_numpy()
    o, h, l, c, a = (d[k].to_numpy() for k in ("open", "high", "low", "close", "atr"))
    idx = d.index
    i0 = 1 if start is None else max(1, int(idx.searchsorted(start)))
    i1 = len(d) if end is None else int(idx.searchsorted(end))
    slip, fee = e["slippage_bps"] / 1e4, e["fee"]

    cash, qty = e["initial_cash"], 0.0
    pos = None
    trades, equity = [], []

    def close(i, px, reason):
        nonlocal cash, qty, pos
        fill = px * (1 - slip)
        f = qty * fill * fee
        cash += qty * fill - f
        pnl = (fill - pos["entry"]) * qty - f - pos["fee"]
        trades.append({"entry_time": str(pos["time"]), "exit_time": str(idx[i]), "entry": pos["entry"],
                       "exit": fill, "qty": qty, "pnl": pnl, "r": pnl / pos["risk"], "bars": i - pos["i"],
                       "reason": reason})
        qty, pos = 0.0, None

    for i in range(i0, i1):
        if pos is not None:
            if exit_[i - 1]:
                close(i, o[i], "signal")
            elif l[i] <= pos["stop"]:
                close(i, min(pos["stop"], o[i]), "stop_loss")
            elif pos["tp"] and h[i] >= pos["tp"]:
                close(i, max(pos["tp"], o[i]), "take_profit")
            elif e["trail_atr_mult"] and not np.isnan(a[i]):
                pos["hw"] = max(pos["hw"], h[i])
                pos["stop"] = max(pos["stop"], pos["hw"] - e["trail_atr_mult"] * a[i])
        elif entry[i - 1] and not np.isnan(a[i - 1]) and a[i - 1] > 0:
            fill = o[i] * (1 + slip)
            stop = fill - e["stop_atr_mult"] * a[i - 1]
            per_unit = (fill - stop) + fill * 2 * (fee + slip)
            q = min(cash * e["risk_per_trade_pct"] / 100 / per_unit, cash * e["max_position_pct"] / 100 / fill,
                    cash / (fill * (1 + fee)))
            if q > 0 and stop > 0:
                f = q * fill * fee
                cash -= q * fill + f
                qty = q
                pos = {"entry": fill, "stop": stop, "fee": f, "risk": q * per_unit, "i": i, "time": idx[i], "hw": fill,
                       "tp": fill + e["take_profit_r"] * (fill - stop) if e["take_profit_r"] else None}
                if l[i] <= stop:  # the entry bar itself can hit the stop
                    close(i, stop, "stop_loss")
        equity.append(cash + qty * c[i])
    eq = pd.Series(equity, index=idx[i0:i1], dtype=float)
    res = metrics(eq, trades, e["initial_cash"], timeframe)
    if len(eq):
        res["buy_and_hold_return_pct"] = round((c[i1 - 1] / o[i0] - 1) * 100, 2)
    res.update({"strategy": strategy, "params": params or {}, "bars": int(i1 - i0),
                "period": [str(idx[i0]), str(idx[i1 - 1])] if i1 > i0 else None,
                "open_position_at_end": pos is not None})
    res["_trades"], res["_equity"] = trades, eq
    return res


def metrics(eq: pd.Series, trades: list, initial: float, timeframe: str) -> dict:
    if eq.empty:
        return {"trades": 0, "total_return_pct": 0.0, "sharpe": None, "max_drawdown_pct": 0.0,
                "win_rate_pct": None, "profit_factor": None}
    per_year = 365 * 86400 / tf_seconds(timeframe)
    rets = eq.pct_change().dropna()
    years = len(eq) / per_year
    total = eq.iloc[-1] / initial - 1
    dd = (eq / eq.cummax() - 1).min()
    sd = rets.std(ddof=0) if len(rets) else 0.0
    neg = rets[rets < 0]
    downside = neg.std(ddof=0) if len(neg) > 1 else 0.0
    pnl = [t["pnl"] for t in trades]
    wins, losses = [p for p in pnl if p > 0], [p for p in pnl if p <= 0]
    gl = -sum(losses)
    r2 = lambda x: None if x is None or not np.isfinite(x) else round(float(x), 2)
    return {
        "total_return_pct": r2(total * 100),
        "cagr_pct": r2(((1 + total) ** (1 / years) - 1) * 100) if years > 0 and total > -1 else None,
        "max_drawdown_pct": r2(dd * 100),
        "sharpe": r2(rets.mean() / sd * np.sqrt(per_year)) if sd > 0 else None,
        "sortino": r2(rets.mean() / downside * np.sqrt(per_year)) if downside > 0 else None,
        "trades": len(trades),
        "win_rate_pct": r2(len(wins) / len(trades) * 100) if trades else None,
        "profit_factor": r2(sum(wins) / gl) if gl > 0 else None,
        "expectancy_r": r2(np.mean([t["r"] for t in trades])) if trades else None,
        "avg_bars_held": r2(np.mean([t["bars"] for t in trades])) if trades else None,
        "exposure_pct": r2(sum(t["bars"] for t in trades) / len(eq) * 100),
        "final_equity": r2(eq.iloc[-1]),
    }


def public(res: dict, with_trades: int = 0) -> dict:
    out = {k: v for k, v in res.items() if not k.startswith("_")}
    if with_trades:
        out["last_trades"] = [{k: (round(v, 6) if isinstance(v, float) else v) for k, v in t.items()}
                              for t in res["_trades"][-with_trades:]]
    return out


def assess(res: dict) -> list[str]:
    """Plain-language warnings so a good-looking number is not trusted blindly."""
    f = []
    n = res.get("trades", 0)
    if n < 30:
        f.append(f"Only {n} trades - statistically weak; use more history or a shorter timeframe.")
    if (res.get("profit_factor") or 0) > 3 and n < 100:
        f.append("Profit factor above 3 on a small sample usually means overfitting or luck.")
    if res.get("max_drawdown_pct") is not None and res["max_drawdown_pct"] < -25:
        f.append("Drawdown beyond 25%: too deep for most accounts at this risk setting.")
    bh = res.get("buy_and_hold_return_pct")
    if bh is not None and res.get("total_return_pct") is not None and res["total_return_pct"] < bh:
        f.append(f"Underperformed buy-and-hold ({res['total_return_pct']}% vs {bh}%); the value, if any, is lower drawdown.")
    if (res.get("expectancy_r") or 0) <= 0 and n:
        f.append("Expectancy is not positive: no edge after costs.")
    return f


def _grid(space: dict) -> list[dict]:
    keys = list(space)
    return [dict(zip(keys, vals)) for vals in itertools.product(*(space[k] for k in keys))]


def walk_forward(df: pd.DataFrame, strategy: str, engine: dict | None = None, timeframe: str = "1h",
                 folds: int = 4, train_frac: float = 0.7, min_trades: int = 5, warmup: int = 210) -> dict:
    """Optimise parameters on each training window, then trade the following unseen window.
    The out-of-sample results are what to believe; in-sample numbers are always flattering."""
    space = _grid(STRATEGIES[strategy][1])
    d = ind.add_all(df)
    usable = len(d) - warmup
    ratio = train_frac / (1 - train_frac)
    test_len = int(usable / (folds + ratio))
    train_len = int(test_len * ratio)
    if test_len < 100:
        raise ValueError(f"Need about {int(warmup + 100 * (folds + ratio))} candles for {folds} folds; have {len(d)}.")
    out, oos_trades, oos_rets = [], [], []
    for k in range(folds):
        tr0 = warmup + k * test_len
        tr1 = tr0 + train_len
        te1 = min(tr1 + test_len, len(d))
        best = best_is = None
        best_obj = -1e18
        for p in space:
            r = run(df, strategy, p, engine, timeframe, prepared=d, start=d.index[tr0], end=d.index[tr1])
            ob = r["sharpe"] if (r["trades"] >= min_trades and r["sharpe"] is not None) else -1e9
            if ob > best_obj:
                best, best_obj, best_is = p, ob, r
        r = run(df, strategy, best, engine, timeframe, prepared=d, start=d.index[tr1],
                end=d.index[te1] if te1 < len(d) else None)
        oos_trades += r["_trades"]
        oos_rets.append(r["_equity"].pct_change().dropna())
        out.append({"fold": k + 1, "train": [str(d.index[tr0]), str(d.index[tr1 - 1])],
                    "test": [str(d.index[tr1]), str(d.index[te1 - 1])], "best_params": best,
                    "in_sample": {x: best_is[x] for x in ("total_return_pct", "sharpe", "trades")},
                    "out_of_sample": {x: r[x] for x in ("total_return_pct", "sharpe", "max_drawdown_pct", "trades",
                                                        "win_rate_pct", "profit_factor")}})
    rets = pd.concat(oos_rets) if oos_rets else pd.Series(dtype=float)
    init = {**ENGINE_DEFAULTS, **(engine or {})}["initial_cash"]
    combined = metrics(init * (1 + rets).cumprod(), oos_trades, init, timeframe) if len(rets) else {}
    is_sh = [f["in_sample"]["sharpe"] for f in out if f["in_sample"]["sharpe"] is not None]
    oos_sh = [f["out_of_sample"]["sharpe"] or 0.0 for f in out]
    profitable = sum(1 for f in out if (f["out_of_sample"]["total_return_pct"] or 0) > 0)
    verdict = []
    if is_sh and np.mean(is_sh) > 0:
        decay = float(np.mean(oos_sh) / np.mean(is_sh))
        verdict.append(f"Out-of-sample Sharpe is {decay:.0%} of in-sample "
                       + ("(holds up)." if decay >= 0.5 else "(large decay: likely overfit)."))
    verdict.append(f"{profitable}/{len(out)} test windows were profitable.")
    if len(out) > 2 and len({str(f["best_params"]) for f in out}) == len(out):
        verdict.append("Best parameters changed in every fold: the optimum is unstable.")
    robust = bool(out) and profitable >= int(np.ceil(len(out) * 0.6)) and (combined.get("total_return_pct") or 0) > 0
    return {"strategy": strategy, "folds": out, "combined_out_of_sample": combined,
            "robust": robust, "verdict": verdict}
