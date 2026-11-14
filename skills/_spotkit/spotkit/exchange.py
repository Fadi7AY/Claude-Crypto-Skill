"""Market data feeds: a ccxt-backed feed for real exchanges and an offline demo feed.

Both expose the same methods, so every skill can run with --demo (no network,
no keys) for testing and dry runs.
"""
from __future__ import annotations

import math
import os
import time
import zlib

import numpy as np
import pandas as pd

_UNITS = {"m": 60, "h": 3600, "d": 86400, "w": 604800}


def tf_seconds(tf: str) -> int:
    try:
        return int(tf[:-1]) * _UNITS[tf[-1].lower()]
    except (KeyError, ValueError):
        raise ValueError(f"Unsupported timeframe '{tf}'. Use forms like 5m, 15m, 1h, 4h, 1d, 1w.")


def _pandas_rule(tf: str) -> str:
    n, u = int(tf[:-1]), tf[-1].lower()
    return f"{n}{ {'m': 'min', 'h': 'h', 'd': 'D', 'w': 'W'}[u] }"


def to_df(rows) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
    df["time"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    df = df.drop_duplicates("ts").set_index("time").sort_index()
    return df[["open", "high", "low", "close", "volume"]].astype(float)


def closed_only(df: pd.DataFrame, tf: str) -> pd.DataFrame:
    """Drop the still-forming last candle. Signals on an unfinished bar repaint,
    which makes live behaviour differ from the backtest."""
    if df.empty:
        return df
    last_open = df.index[-1].timestamp()
    if last_open + tf_seconds(tf) > time.time():
        return df.iloc[:-1]
    return df


def check_spot_symbol(symbol: str) -> None:
    if ":" in symbol or "/" not in symbol:
        raise ValueError(
            f"'{symbol}' is not a spot symbol. Use BASE/QUOTE such as BTC/USDT "
            "(symbols containing ':' are derivatives and are refused)."
        )


class CcxtFeed:
    """Thin wrapper over ccxt restricted to spot."""

    demo = False

    def __init__(self, exchange_id: str, auth: bool = False):
        import ccxt

        if not hasattr(ccxt, exchange_id):
            raise ValueError(f"Unknown ccxt exchange '{exchange_id}'.")
        opts = {"enableRateLimit": True, "options": {"defaultType": "spot"}}
        if auth:
            key, secret = os.environ.get("EXCHANGE_API_KEY"), os.environ.get("EXCHANGE_API_SECRET")
            if not key or not secret:
                raise RuntimeError("Set EXCHANGE_API_KEY and EXCHANGE_API_SECRET in the environment for live trading.")
            opts.update({"apiKey": key, "secret": secret})
            if os.environ.get("EXCHANGE_API_PASSWORD"):
                opts["password"] = os.environ["EXCHANGE_API_PASSWORD"]
        self.id = exchange_id
        self.ex = getattr(ccxt, exchange_id)(opts)
        self._loaded = False

    def _markets(self):
        if not self._loaded:
            self.ex.load_markets()
            self._loaded = True
        return self.ex.markets

    def ohlcv(self, symbol: str, tf: str, limit: int = 500, since_ms: int | None = None) -> pd.DataFrame:
        check_spot_symbol(symbol)
        self._markets()
        step = tf_seconds(tf) * 1000
        since = since_ms if since_ms is not None else int(time.time() * 1000) - step * (limit + 1)
        rows: list = []
        while len(rows) < limit:
            batch = self.ex.fetch_ohlcv(symbol, tf, since=since, limit=min(1000, limit - len(rows) + 1))
            if not batch:
                break
            rows.extend(batch)
            nxt = batch[-1][0] + step
            if nxt <= since or len(batch) < 2:
                break
            since = nxt
            if since > time.time() * 1000:
                break
        return to_df(rows).tail(limit)

    def ticker(self, symbol: str) -> dict:
        check_spot_symbol(symbol)
        t = self.ex.fetch_ticker(symbol)
        bid, ask, last = t.get("bid"), t.get("ask"), t.get("last")
        spread = (ask - bid) / ((ask + bid) / 2) * 1e4 if bid and ask else None
        return {"symbol": symbol, "last": last, "bid": bid, "ask": ask, "spread_bps": spread,
                "change_pct_24h": t.get("percentage"), "quote_volume_24h": t.get("quoteVolume"),
                "high_24h": t.get("high"), "low_24h": t.get("low")}

    def tickers(self, quote: str) -> list[dict]:
        self._markets()
        out = []
        for sym, t in self.ex.fetch_tickers().items():
            m = self.ex.markets.get(sym)
            if not m or not m.get("spot") or m.get("quote") != quote or not m.get("active", True):
                continue
            out.append({"symbol": sym, "last": t.get("last"), "change_pct_24h": t.get("percentage"),
                        "quote_volume_24h": t.get("quoteVolume") or 0})
        return out

    def orderbook(self, symbol: str, depth: int = 20) -> dict:
        check_spot_symbol(symbol)
        ob = self.ex.fetch_order_book(symbol, depth)
        return {"bids": ob["bids"][:depth], "asks": ob["asks"][:depth]}

    def market(self, symbol: str) -> dict:
        check_spot_symbol(symbol)
        m = self._markets().get(symbol)
        if not m:
            raise ValueError(f"{symbol} is not listed on {self.id}.")
        if not m.get("spot"):
            raise ValueError(f"{symbol} on {self.id} is not a spot market.")
        lim = m.get("limits", {})
        return {"symbol": symbol, "base": m["base"], "quote": m["quote"],
                "min_amount": (lim.get("amount") or {}).get("min") or 0.0,
                "min_cost": (lim.get("cost") or {}).get("min") or 0.0,
                "taker": m.get("taker"), "maker": m.get("maker")}

    def round_amount(self, symbol: str, amount: float) -> float:
        self._markets()
        return float(self.ex.amount_to_precision(symbol, amount))

    def round_price(self, symbol: str, price: float) -> float:
        self._markets()
        return float(self.ex.price_to_precision(symbol, price))


class DemoFeed:
    """Deterministic synthetic market (seeded random walk with trending regimes).

    Prices are reproducible for a given symbol and consistent across timeframes
    because every timeframe is resampled from one 5-minute base series.
    """

    demo = True
    id = "demo"
    ANCHOR = 1704067200  # 2024-01-01 UTC
    BASE = 300
    SYMBOLS = {"BTC": 42000, "ETH": 2300, "SOL": 100, "BNB": 310, "XRP": 0.6, "ADA": 0.6,
               "AVAX": 40, "LINK": 15, "DOGE": 0.09, "DOT": 8}

    def __init__(self, quote: str = "USDT"):
        self.quote = quote
        self._cache: dict[str, pd.DataFrame] = {}

    def _base(self, symbol: str) -> pd.DataFrame:
        check_spot_symbol(symbol)
        base = symbol.split("/")[0]
        if base not in self.SYMBOLS:
            raise ValueError(f"Demo feed has {sorted(self.SYMBOLS)} only; {symbol} is unavailable.")
        if symbol in self._cache:
            return self._cache[symbol]
        n = int((time.time() - self.ANCHOR) // self.BASE) + 1
        rng = np.random.default_rng(zlib.crc32(symbol.encode()))
        i = np.arange(n)
        phase = rng.uniform(0, 2 * math.pi)
        # slow regime cycle (~45 days) plus a faster one (~6 days) gives trends and chop
        drift = 0.00006 * np.sin(2 * math.pi * i / 12960 + phase) + 0.00004 * np.sin(2 * math.pi * i / 1728 + phase / 2)
        vol = 0.0013 * (1 + 0.5 * np.sin(2 * math.pi * i / 4000 + phase))
        r = rng.normal(0, 1, n) * vol + drift
        close = self.SYMBOLS[base] * np.exp(np.cumsum(r))
        open_ = np.concatenate([[self.SYMBOLS[base]], close[:-1]])
        wick = np.abs(rng.normal(0, 1, n)) * vol * 0.5
        high = np.maximum(open_, close) * (1 + wick)
        low = np.minimum(open_, close) * (1 - wick)
        volume = rng.lognormal(0, 0.5, n) * (1 + 40 * np.abs(r) / 0.0013 / 10) * 1e6 / close
        idx = pd.to_datetime(self.ANCHOR + i * self.BASE, unit="s", utc=True)
        df = pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": volume}, index=idx)
        self._cache[symbol] = df
        return df

    def ohlcv(self, symbol: str, tf: str, limit: int = 500, since_ms: int | None = None) -> pd.DataFrame:
        secs = tf_seconds(tf)
        if secs < self.BASE:
            raise ValueError("Demo feed supports 5m and longer timeframes.")
        base = self._base(symbol)
        if since_ms is None:
            base = base.tail(int(secs / self.BASE) * (limit + 2))
        df = base if secs == self.BASE else base.resample(_pandas_rule(tf), label="left", closed="left").agg(
            {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()
        if since_ms is not None:
            df = df[df.index >= pd.to_datetime(since_ms, unit="ms", utc=True)].head(limit)
        return df.tail(limit)

    def ticker(self, symbol: str) -> dict:
        b = self._base(symbol)
        last = float(b["close"].iloc[-1])
        day = b.tail(288)
        return {"symbol": symbol, "last": last, "bid": last * 0.9999, "ask": last * 1.0001, "spread_bps": 2.0,
                "change_pct_24h": (last / float(day["open"].iloc[0]) - 1) * 100,
                "quote_volume_24h": float((day["volume"] * day["close"]).sum()),
                "high_24h": float(day["high"].max()), "low_24h": float(day["low"].min())}

    def tickers(self, quote: str) -> list[dict]:
        out = []
        for b in self.SYMBOLS:
            t = self.ticker(f"{b}/{quote}")
            out.append({k: t[k] for k in ("symbol", "last", "change_pct_24h", "quote_volume_24h")})
        return out

    def orderbook(self, symbol: str, depth: int = 20) -> dict:
        last = self.ticker(symbol)["last"]
        return {"bids": [[last * (1 - 0.0001 * (k + 1)), 1000 / last * (k + 1)] for k in range(depth)],
                "asks": [[last * (1 + 0.0001 * (k + 1)), 1000 / last * (k + 1)] for k in range(depth)]}

    def market(self, symbol: str) -> dict:
        self._base(symbol)
        b, q = symbol.split("/")
        return {"symbol": symbol, "base": b, "quote": q, "min_amount": 0.0, "min_cost": 5.0,
                "taker": 0.001, "maker": 0.001}

    def round_amount(self, symbol: str, amount: float) -> float:
        return math.floor(amount * 1e6) / 1e6

    def round_price(self, symbol: str, price: float) -> float:
        return float(f"{price:.8g}")


def get_feed(cfg: dict, demo: bool = False, auth: bool = False):
    if demo:
        return DemoFeed(cfg.get("quote", "USDT"))
    return CcxtFeed(cfg["exchange"], auth=auth)


def load_csv(path: str) -> pd.DataFrame:
    """Load OHLCV from CSV (columns: time/timestamp/date, open, high, low, close, volume)."""
    df = pd.read_csv(path)
    df.columns = [c.strip().lower() for c in df.columns]
    tcol = next((c for c in ("time", "timestamp", "date", "datetime", "ts") if c in df.columns), None)
    if tcol is None:
        raise ValueError("CSV needs a time/timestamp/date column.")
    t = df[tcol]
    if pd.api.types.is_numeric_dtype(t):
        df["time"] = pd.to_datetime(t, unit="ms" if t.iloc[0] > 1e11 else "s", utc=True)
    else:
        df["time"] = pd.to_datetime(t, utc=True)
    return df.set_index("time").sort_index()[["open", "high", "low", "close", "volume"]].astype(float)
