---
name: market-scanner
description: Scan the most liquid spot pairs on the exchange and rank them by confluence score to find trade candidates. Use this when the user asks what to trade, which coins look strongest or weakest right now, wants setups, opportunities, a watchlist, a market overview or a daily/hourly scan - any question about the market as a whole rather than one named coin.
---

# Market scanner

```bash
S="${CLAUDE_SKILL_DIR}/scripts/scan.py"
python3 "$S"                                   # top 25 pairs by 24h volume, entry timeframe from config
python3 "$S" --top 50 --timeframe 4h --min-score 25
python3 "$S" --symbols BTC/USDT,ETH/USDT,SOL/USDT
python3 "$S" --tv                              # also attach TradingView's rating for each pair
```

The universe is the exchange's spot pairs in the configured quote currency, stablecoin pairs removed, filtered by `risk.min_quote_volume_24h`. Each pair is scored on the entry and higher timeframe with the same function the technical-analysis skill and the bot use.

## Output

- `candidates`: pairs where `meets_entry_rules` is true - score at or above `strategy.entry_score` **and** the higher timeframe is not bearish. These are exactly what the bot would consider buying.
- `results`: every scanned pair, best first, with score, higher-timeframe score, regime, RSI, ADX, ATR%, and the stop distance (`risk_pct`).

Present a short ranked table (symbol, score, HTF score, regime, stop distance). Call out when there are no candidates - "nothing qualifies right now" is a useful answer, and forcing a pick from a weak list is how accounts bleed. A broad sea of negative scores is itself information: the market is risk-off.

A scan of 25 pairs makes about 50 candle requests; larger scans take proportionally longer. For whole-market screens by rating, oversold pullbacks or volume breakouts, the tradingview-analysis skill's screener is one request.

Next steps: technical-analysis for a full read on a candidate, risk-management to size it.

## Conventions shared by all spot-trading skills

- Scripts live in this skill's `scripts/` folder (`${CLAUDE_SKILL_DIR}/scripts/`; if that variable is empty, use the folder this SKILL.md is in). They import the shared library from the sibling `_spotkit/` folder, so keep the skills folder intact.
- Every script prints JSON. `"ok": false` comes with an `error` (and often a `hint`) - read it and relay it rather than retrying blindly.
- Settings and state are in `./.spotkit/` of the project (`config.json`, paper account, journal). Create it once with the spot-bot-runner skill (`run_bot.py init`). Defaults apply if it is missing.
- Global flags (`--demo`, `--exchange`, `--live`, and any others shown before the subcommand in the examples) must come **before** the subcommand.
- Add `--demo` to run against a built-in synthetic market with no network or keys. Say so when you do: demo prices are not real.
- Dependencies: `pip install -r requirements.txt` (ccxt, pandas, numpy, tradingview-ta, tradingview-screener).
