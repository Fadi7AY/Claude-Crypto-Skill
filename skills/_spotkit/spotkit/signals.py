"""Confluence scoring: one function used by analysis, the scanner, the backtester and the bot.

Score runs from -100 (everything bearish) to +100 (everything bullish). On spot
there is no shorting, so a negative score means "stay out / exit", never "sell short".
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import indicators as ind

WEIGHTS = {"trend": 0.35, "momentum": 0.30, "structure": 0.25, "volume": 0.10}


def components(d: pd.DataFrame) -> pd.DataFrame:
    """Per-bar component scores in [-1, 1]. `d` must come from indicators.add_all."""
    c = d["close"]
    sgn = lambda s: np.sign(s).fillna(0)
    trend = 0.40 * sgn(c - d["ema200"]) + 0.35 * sgn(d["ema50"] - d["ema200"]) + 0.25 * sgn(d["ema20"] - d["ema50"])

    hist = d["macd_hist"]
    rsi_part = ((d["rsi"] - 50) / 25).clip(-1, 1).fillna(0)
    momentum = 0.5 * sgn(hist) + 0.2 * sgn(hist.diff()) + 0.3 * rsi_part
    momentum = momentum - 0.3 * (d["rsi"] > 78).astype(float)  # stretched: late entries get punished

    breakout = (c > d["dc_upper"].shift()).astype(float) - (c < d["dc_lower"].shift()).astype(float)
    structure = 0.6 * d["supertrend_dir"].where(d["supertrend"].notna(), 0) + 0.4 * breakout

    vz = d["vol_z"].fillna(0)
    volume = np.where(vz > 1, np.sign(c - d["open"]) * (vz / 3).clip(upper=1), 0.0)

    # weak trends (low ADX) make trend/structure readings unreliable, so scale them down
    strength = pd.Series(np.select([d["adx"] < 15, d["adx"] < 25], [0.6, 0.8], 1.0), index=d.index)
    return pd.DataFrame({"trend": (trend * strength).clip(-1, 1), "momentum": momentum.clip(-1, 1),
                         "structure": (structure * strength).clip(-1, 1), "volume": volume}, index=d.index)


def score_series(d: pd.DataFrame) -> pd.Series:
    comp = components(d)
    return 100 * sum(comp[k] * w for k, w in WEIGHTS.items())


def label(score: float) -> str:
    if score >= 60:
        return "strong_buy"
    if score >= 25:
        return "buy"
    if score <= -60:
        return "strong_avoid"
    if score <= -25:
        return "avoid"
    return "neutral"


def regime(row: pd.Series) -> str:
    if np.isnan(row.get("adx", np.nan)) or np.isnan(row.get("ema200", np.nan)):
        return "insufficient_history"
    if row["adx"] < 18:
        return "ranging"
    return "uptrend" if row["close"] > row["ema200"] and row["ema50"] > row["ema200"] else (
        "downtrend" if row["close"] < row["ema200"] and row["ema50"] < row["ema200"] else "transition")


def analyze(df: pd.DataFrame, stop_atr_mult: float = 2.0, tp_r: float = 2.0) -> dict:
    """Full read of one timeframe from closed candles."""
    if len(df) < 60:
        return {"ok": False, "error": f"Need at least 60 candles, got {len(df)}."}
    d = ind.add_all(df)
    comp = components(d)
    sc = score_series(d)
    r = d.iloc[-1]
    px, a = float(r["close"]), float(r["atr"])
    levels = ind.swing_levels(df)
    stop = px - stop_atr_mult * a
    # tuck the stop under nearby support when that is a tighter, more meaningful level
    sup = [s["price"] for s in levels["support"] if px - 3 * a < s["price"] < px - 0.8 * a]
    if sup:
        stop = max(sup) - 0.25 * a
    risk = px - stop
    f = lambda v: None if v is None or (isinstance(v, float) and np.isnan(v)) else round(float(v), 8)
    notes = []
    if len(df) < 210:
        notes.append("Under 210 candles: EMA200 is missing, so the trend component is understated.")
    if r["rsi"] > 75:
        notes.append("RSI above 75: overbought, pullback risk is elevated.")
    if r["rsi"] < 30:
        notes.append("RSI below 30: oversold; only meaningful as a buy inside an uptrend.")
    if r["bb_width"] <= d["bb_width"].tail(120).quantile(0.1):
        notes.append("Bollinger width in its lowest decile: volatility squeeze, expect expansion.")
    return {
        "ok": True, "price": f(px), "last_candle": str(d.index[-1]), "candles": len(df),
        "score": round(float(sc.iloc[-1]), 1), "signal": label(float(sc.iloc[-1])),
        "score_prev": [round(float(x), 1) for x in sc.tail(4).iloc[:-1]],
        "regime": regime(r),
        "components": {k: round(float(comp[k].iloc[-1]), 2) for k in WEIGHTS},
        "indicators": {k: f(r[k]) for k in ("ema20", "ema50", "ema200", "rsi", "macd", "macd_signal", "macd_hist",
                                             "adx", "plus_di", "minus_di", "atr", "atr_pct", "bb_upper", "bb_mid",
                                             "bb_lower", "bb_pctb", "stoch_k", "stoch_d", "supertrend",
                                             "supertrend_dir", "vwap24", "vol_z", "dc_upper", "dc_lower")},
        "levels": levels,
        "plan": {"entry": f(px), "stop": f(stop), "risk_per_unit": f(risk), "risk_pct": f(risk / px * 100),
                 "targets": {f"{m:g}R": f(px + m * risk) for m in sorted({1.0, float(tp_r), 3.0})}},
        "notes": notes,
    }


def multi_timeframe(feed, symbol: str, timeframes: list[str], limit: int = 400, risk_cfg: dict | None = None) -> dict:
    """Analyze several timeframes (first = entry timeframe) and combine them."""
    from .exchange import closed_only

    rc = risk_cfg or {}
    per = {}
    for tf in timeframes:
        df = closed_only(feed.ohlcv(symbol, tf, limit=limit), tf)
        per[tf] = analyze(df, rc.get("stop_atr_mult", 2.0), rc.get("take_profit_r", 2.0) or 2.0)
    ok = {tf: a for tf, a in per.items() if a.get("ok")}
    if not ok:
        return {"ok": False, "symbol": symbol, "timeframes": per}
    entry_tf = timeframes[0]
    entry = ok.get(entry_tf)
    higher = [a for tf, a in ok.items() if tf != entry_tf]
    htf_score = float(np.mean([a["score"] for a in higher])) if higher else None
    combined = entry["score"] if entry else float(np.mean([a["score"] for a in ok.values()]))
    verdict = label(combined)
    reason = f"{entry_tf} score {combined:.0f}"
    if htf_score is not None:
        reason += f", higher-timeframe score {htf_score:.0f}"
        if htf_score < 0 and combined > 0:
            verdict = "neutral"
            reason += " - higher timeframe is bearish, so the long signal is vetoed"
        elif htf_score >= 25 and combined >= 25:
            reason += " - timeframes aligned"
    return {"ok": True, "symbol": symbol, "entry_timeframe": entry_tf, "score": round(combined, 1),
            "htf_score": None if htf_score is None else round(htf_score, 1), "signal": verdict,
            "reason": reason, "plan": entry["plan"] if entry else None, "timeframes": per}
