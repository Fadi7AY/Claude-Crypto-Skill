"""TradingView integration: technical ratings (tradingview-ta) and the crypto screener
(tradingview-screener). Both read TradingView's public scanner endpoint - no account,
no API key. Data is for analysis only; orders still go through the exchange.
"""
from __future__ import annotations

INTERVALS = {"1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m", "1h": "1h", "2h": "2h",
             "4h": "4h", "1d": "1d", "1w": "1W", "1M": "1M"}
BULLISH = {"BUY", "STRONG_BUY"}


def tv_symbol(symbol: str) -> str:
    return symbol.replace("/", "").replace("-", "").upper()


def _interval(tf: str) -> str:
    if tf not in INTERVALS:
        raise ValueError(f"TradingView ratings support {sorted(INTERVALS)}; got '{tf}'.")
    return INTERVALS[tf]


def _pack(a) -> dict:
    i = a.indicators
    pick = {k: i.get(k) for k in ("close", "RSI", "MACD.macd", "MACD.signal", "ADX", "EMA20", "EMA50", "EMA200",
                                  "Stoch.K", "Stoch.D", "BB.upper", "BB.lower", "volume", "change")}
    return {"recommendation": a.summary["RECOMMENDATION"],
            "counts": {k.lower(): a.summary[k] for k in ("BUY", "NEUTRAL", "SELL")},
            "oscillators": a.oscillators["RECOMMENDATION"], "moving_averages": a.moving_averages["RECOMMENDATION"],
            "indicators": pick}


def rating(symbol: str, timeframes: list[str], exchange: str = "BINANCE", screener: str = "crypto") -> dict:
    """TradingView's own Buy/Sell rating per timeframe plus an alignment summary."""
    from tradingview_ta import TA_Handler

    out, errors = {}, {}
    for tf in timeframes:
        try:
            h = TA_Handler(symbol=tv_symbol(symbol), screener=screener, exchange=exchange,
                           interval=_interval(tf), timeout=15)
            out[tf] = _pack(h.get_analysis())
        except Exception as e:  # noqa: BLE001 - network/symbol errors are reported, not raised
            errors[tf] = f"{type(e).__name__}: {e}"
    recs = [v["recommendation"] for v in out.values()]
    bull = sum(r in BULLISH for r in recs)
    bear = sum(r in {"SELL", "STRONG_SELL"} for r in recs)
    align = "no_data" if not recs else "bullish" if bull == len(recs) else "bearish" if bear == len(recs) else "mixed"
    return {"ok": bool(out), "symbol": symbol, "tv_symbol": f"{exchange}:{tv_symbol(symbol)}",
            "alignment": align, "timeframes": out, "errors": errors}


def ratings_many(symbols: list[str], tf: str, exchange: str = "BINANCE", screener: str = "crypto") -> dict:
    """One request for many symbols (used by the scanner)."""
    from tradingview_ta import get_multiple_analysis

    keys = {f"{exchange}:{tv_symbol(s)}": s for s in symbols}
    try:
        res = get_multiple_analysis(screener=screener, interval=_interval(tf), symbols=list(keys), timeout=20)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}", "ratings": {}}
    return {"ok": True, "ratings": {keys[k]: (v.summary["RECOMMENDATION"] if v else None)
                                    for k, v in res.items() if k in keys}}


def confirms_long(symbol: str, tf: str, exchange: str = "BINANCE") -> tuple[bool, str]:
    r = rating(symbol, [tf], exchange)
    if not r["ok"]:
        return False, f"TradingView rating unavailable ({r['errors'].get(tf)})"
    rec = r["timeframes"][tf]["recommendation"]
    return rec in BULLISH, f"TradingView {tf} rating {rec}"


SCREENS = {
    "momentum": "Uptrend with strong rating: above EMA50/EMA200, RSI 50-70, ADX > 20",
    "oversold_uptrend": "Pullback inside an uptrend: above EMA200 with RSI under 35",
    "breakout_volume": "Up more than 3% today on at least 2x normal volume",
    "top_volume": "Most traded pairs, no other filter",
}


def _tv_label(rec) -> str | None:
    if rec is None:
        return None
    return ("STRONG_BUY" if rec > 0.5 else "BUY" if rec > 0.1 else
            "STRONG_SELL" if rec < -0.5 else "SELL" if rec < -0.1 else "NEUTRAL")


def screen(name: str = "momentum", exchange: str = "BINANCE", quote: str = "USDT", limit: int = 20,
           min_volume_usd: float = 5_000_000) -> dict:
    """Screen a whole exchange with TradingView's crypto scanner."""
    from tradingview_screener import Query, col

    if name not in SCREENS:
        raise ValueError(f"Unknown screen '{name}'. Options: {sorted(SCREENS)}")
    f = [col("exchange") == exchange, col("name").like(f"%{quote}"), col("Value.Traded") > min_volume_usd]
    if name == "momentum":
        f += [col("close") > col("EMA50"), col("EMA50") > col("EMA200"), col("RSI").between(50, 70),
              col("ADX") > 20, col("Recommend.All") > 0.1]
    elif name == "oversold_uptrend":
        f += [col("close") > col("EMA200"), col("RSI") < 35]
    elif name == "breakout_volume":
        f += [col("change") > 3, col("relative_volume_10d_calc") > 2]
    try:
        total, df = (Query().set_markets("crypto")
                     .select("name", "close", "change", "Value.Traded", "RSI", "ADX", "EMA50", "EMA200",
                             "relative_volume_10d_calc", "Recommend.All")
                     .where(*f).order_by("Value.Traded", ascending=False).limit(limit).get_scanner_data())
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    rows = []
    for r in df.to_dict("records"):
        n = r["name"]
        rows.append({"symbol": f"{n[:-len(quote)]}/{quote}" if n.endswith(quote) else n, "close": r.get("close"),
                     "change_pct": r.get("change"), "value_traded": r.get("Value.Traded"), "rsi": r.get("RSI"),
                     "adx": r.get("ADX"), "rel_volume": r.get("relative_volume_10d_calc"),
                     "tv_rating": _tv_label(r.get("Recommend.All"))})
    return {"ok": True, "screen": name, "description": SCREENS[name], "matches": int(total), "results": rows}
