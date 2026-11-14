---
name: market-data
description: Fetch live crypto spot market data from any ccxt exchange (Binance, Bybit, Kraken, OKX, Crypto.com and others) - candles/OHLCV, current price and 24h stats, order book depth and spread, exchange trading rules, and top movers by volume or change. Use this whenever the user asks for a coin's price, candles, volume, spread, liquidity, what is pumping or dumping, minimum order sizes, or needs a CSV of price history, and before any analysis or trade that depends on current prices.
---

# Market data

Raw market facts for spot pairs. Symbols are always `BASE/QUOTE` (`BTC/USDT`); anything with a `:` is a derivative and is refused.

## Commands

```bash
S="${CLAUDE_SKILL_DIR}/scripts/market_data.py"
python3 "$S" ticker BTC/USDT ETH/USDT                 # last, bid/ask, spread, 24h change and volume
python3 "$S" ohlcv BTC/USDT --timeframe 4h --limit 500 --csv btc_4h.csv   # closed candles; optional CSV
python3 "$S" orderbook SOL/USDT --depth 20            # spread, depth on each side, imbalance
python3 "$S" market BTC/USDT                          # min order size/cost and fee tier
python3 "$S" top --sort gainers --limit 15            # volume | gainers | losers, quote from config
python3 "$S" --exchange kraken ticker BTC/USD         # one-off exchange override
```

## Reading the output

- `ohlcv` returns only **closed** candles. The forming candle is dropped on purpose: indicators computed on it change until it closes.
- `spread_bps` above ~30 or thin `bid_depth_quote` means market orders will slip; mention it before the user trades that pair.
- `imbalance` runs from -1 (asks dominate) to +1 (bids dominate). It is a weak, short-lived hint, not a signal.
- `top` is a starting list, not a recommendation. Hand interesting names to the market-scanner or technical-analysis skill.

Report prices with the exchange name and note that they are a snapshot. If the exchange is unreachable, say so and offer `--exchange` with another venue rather than guessing a price.

## Conventions shared by all spot-trading skills

- Scripts live in this skill's `scripts/` folder (`${CLAUDE_SKILL_DIR}/scripts/`; if that variable is empty, use the folder this SKILL.md is in). They import the shared library from the sibling `_spotkit/` folder, so keep the skills folder intact.
- Every script prints JSON. `"ok": false` comes with an `error` (and often a `hint`) - read it and relay it rather than retrying blindly.
- Settings and state are in `./.spotkit/` of the project (`config.json`, paper account, journal). Create it once with the spot-bot-runner skill (`run_bot.py init`). Defaults apply if it is missing.
- Global flags (`--demo`, `--exchange`, `--live`, and any others shown before the subcommand in the examples) must come **before** the subcommand.
- Add `--demo` to run against a built-in synthetic market with no network or keys. Say so when you do: demo prices are not real.
- Dependencies: `pip install -r requirements.txt` (ccxt, pandas, numpy, tradingview-ta, tradingview-screener).
