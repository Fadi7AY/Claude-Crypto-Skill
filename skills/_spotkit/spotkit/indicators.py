"""Technical indicators (pandas/numpy only, no TA-Lib needed).

Formulas follow the common TradingView conventions (Wilder smoothing for
RSI/ATR/ADX) so values line up with what a chart shows.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n).mean()


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False, min_periods=n).mean()


def rma(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    up, dn = rma(d.clip(lower=0), n), rma(-d.clip(upper=0), n)
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def true_range(df: pd.DataFrame) -> pd.Series:
    pc = df["close"].shift()
    return pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    return rma(true_range(df), n)


def adx(df: pd.DataFrame, n: int = 14) -> pd.DataFrame:
    up, dn = df["high"].diff(), -df["low"].diff()
    plus = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=df.index)
    minus = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=df.index)
    a = atr(df, n)
    pdi, mdi = 100 * rma(plus, n) / a, 100 * rma(minus, n) / a
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return pd.DataFrame({"adx": rma(dx, n), "plus_di": pdi, "minus_di": mdi})


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    line = ema(close, fast) - ema(close, slow)
    sig = line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    return pd.DataFrame({"macd": line, "macd_signal": sig, "macd_hist": line - sig})


def bbands(close: pd.Series, n: int = 20, k: float = 2.0) -> pd.DataFrame:
    mid, sd = sma(close, n), close.rolling(n).std(ddof=0)
    up, lo = mid + k * sd, mid - k * sd
    return pd.DataFrame({"bb_mid": mid, "bb_upper": up, "bb_lower": lo,
                         "bb_width": (up - lo) / mid, "bb_pctb": (close - lo) / (up - lo)})


def stochastic(df: pd.DataFrame, k: int = 14, smooth: int = 3, d: int = 3) -> pd.DataFrame:
    ll, hh = df["low"].rolling(k).min(), df["high"].rolling(k).max()
    raw = 100 * (df["close"] - ll) / (hh - ll).replace(0, np.nan)
    kk = raw.rolling(smooth).mean()
    return pd.DataFrame({"stoch_k": kk, "stoch_d": kk.rolling(d).mean()})


def obv(df: pd.DataFrame) -> pd.Series:
    return (np.sign(df["close"].diff()).fillna(0) * df["volume"]).cumsum()


def donchian(df: pd.DataFrame, n: int = 20) -> pd.DataFrame:
    return pd.DataFrame({"dc_upper": df["high"].rolling(n).max(), "dc_lower": df["low"].rolling(n).min()})


def rolling_vwap(df: pd.DataFrame, n: int = 24) -> pd.Series:
    tp = (df["high"] + df["low"] + df["close"]) / 3
    return (tp * df["volume"]).rolling(n).sum() / df["volume"].rolling(n).sum()


def volume_z(volume: pd.Series, n: int = 20) -> pd.Series:
    return (volume - volume.rolling(n).mean()) / volume.rolling(n).std(ddof=0).replace(0, np.nan)


def supertrend(df: pd.DataFrame, n: int = 10, mult: float = 3.0) -> pd.DataFrame:
    a = atr(df, n).to_numpy()
    hl2 = ((df["high"] + df["low"]) / 2).to_numpy()
    close = df["close"].to_numpy()
    ub, lb = hl2 + mult * a, hl2 - mult * a
    fub, flb = ub.copy(), lb.copy()
    direction = np.ones(len(df))
    line = np.full(len(df), np.nan)
    for i in range(1, len(df)):
        if np.isnan(a[i]):
            continue
        if not np.isnan(fub[i - 1]):
            fub[i] = ub[i] if (ub[i] < fub[i - 1] or close[i - 1] > fub[i - 1]) else fub[i - 1]
            flb[i] = lb[i] if (lb[i] > flb[i - 1] or close[i - 1] < flb[i - 1]) else flb[i - 1]
        if close[i] > fub[i - 1]:
            direction[i] = 1
        elif close[i] < flb[i - 1]:
            direction[i] = -1
        else:
            direction[i] = direction[i - 1]
        line[i] = flb[i] if direction[i] == 1 else fub[i]
    return pd.DataFrame({"supertrend": line, "supertrend_dir": direction}, index=df.index)


def swing_levels(df: pd.DataFrame, left: int = 3, right: int = 3, lookback: int = 200, tol_atr: float = 0.5) -> dict:
    """Support/resistance from clustered swing highs and lows."""
    d = df.tail(lookback)
    h, l = d["high"].to_numpy(), d["low"].to_numpy()
    a = float(atr(df).iloc[-1]) if len(df) > 15 else float((d["high"] - d["low"]).mean())
    pts = []
    for i in range(left, len(d) - right):
        if h[i] == h[i - left:i + right + 1].max():
            pts.append(h[i])
        if l[i] == l[i - left:i + right + 1].min():
            pts.append(l[i])
    pts.sort()
    clusters: list[list[float]] = []
    for p in pts:
        if clusters and p - clusters[-1][-1] <= tol_atr * a:
            clusters[-1].append(p)
        else:
            clusters.append([p])
    levels = [(float(np.mean(c)), len(c)) for c in clusters]
    px = float(d["close"].iloc[-1])
    sup = sorted([lv for lv in levels if lv[0] < px], key=lambda x: -x[0])[:3]
    res = sorted([lv for lv in levels if lv[0] > px], key=lambda x: x[0])[:3]
    return {"support": [{"price": p, "touches": t} for p, t in sup],
            "resistance": [{"price": p, "touches": t} for p, t in res]}


def add_all(df: pd.DataFrame) -> pd.DataFrame:
    """Return df with the full indicator set used by the signal engine."""
    out = df.copy()
    c = out["close"]
    for n in (20, 50, 200):
        out[f"ema{n}"] = ema(c, n)
    out["rsi"] = rsi(c)
    out["atr"] = atr(out)
    out["atr_pct"] = out["atr"] / c * 100
    out = out.join(macd(c)).join(bbands(c)).join(adx(out)).join(stochastic(out)).join(donchian(out)).join(supertrend(out))
    out["obv"] = obv(out)
    out["vwap24"] = rolling_vwap(out)
    out["vol_z"] = volume_z(out["volume"])
    return out
