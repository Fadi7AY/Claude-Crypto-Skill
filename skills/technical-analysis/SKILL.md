---
name: technical-analysis
description: Multi-timeframe technical analysis for crypto spot pairs with a -100..+100 confluence score, market regime, support/resistance and a concrete trade plan (entry, ATR/structure stop, R-multiple targets). Computes EMA 20/50/200, RSI, MACD, ADX/DMI, ATR, Bollinger Bands, Stochastic, Supertrend, Donchian, rolling VWAP and volume z-score locally. Use this whenever the user asks whether a coin looks bullish or bearish, wants a chart read, indicators, trend, levels, a setup, "should I buy X", or where to put a stop or target - even if they do not say "technical analysis".
---

# Technical analysis

```bash
S="${CLAUDE_SKILL_DIR}/scripts/analyze.py"
python3 "$S" BTC/USDT                           # entry + higher timeframe from config (default 1h, 4h)
python3 "$S" ETH/USDT --timeframes 15m,1h,4h,1d  # first timeframe is the entry timeframe
python3 "$S" SOL/USDT --brief                    # scores, regime, levels, plan; no raw indicator dump
python3 "$S" BTC/USDT --csv btc_4h.csv           # analyze a CSV of candles instead
```

## How the score works

Four components, each -1..+1, weighted and scaled to -100..+100:

| Component | Weight | Bullish when |
|---|---|---|
| trend | 35% | price > EMA200, EMA50 > EMA200, EMA20 > EMA50 |
| momentum | 30% | MACD histogram positive and rising, RSI above 50 (penalised above 78) |
| structure | 25% | Supertrend up, close above the prior 20-bar high |
| volume | 10% | volume spike (z > 1) on an up candle |

Trend and structure are scaled down when ADX is under 25, because those readings are unreliable in chop.

Labels: `strong_buy` >= 60, `buy` >= 25, `neutral`, `avoid` <= -25, `strong_avoid` <= -60. This is spot, so a negative score means stay out or exit - never short.

The combined verdict uses the entry timeframe's score, **vetoed to neutral if the higher timeframes average below zero**. Buying a 1h bounce inside a 4h downtrend is the most common way these setups fail.

## Presenting a read

Lead with the verdict and the reason, then the levels. Keep it to what the numbers say:

1. Signal and score per timeframe, and whether they agree.
2. Regime (`uptrend`, `downtrend`, `ranging`, `transition`) - a high score in a `ranging` regime deserves less trust.
3. The plan: entry, stop (2 ATR by default, tucked under nearby support when one exists), 1R/2R/3R targets, and `risk_pct` (distance to the stop).
4. Anything in `notes` (overbought, squeeze, short history).

The score describes the chart; it does not predict it. State that this is analysis and not financial advice, once, without burying the answer in caveats. If the user wants to act on it, move to the risk-management skill for sizing and the spot-order-execution skill to place it. For a second opinion from TradingView's own ratings, use the tradingview-analysis skill.

Before trusting a setup type with money, check how it has performed with the strategy-backtesting skill - the same scoring function drives the `confluence` strategy there.

## Conventions shared by all spot-trading skills

- Scripts live in this skill's `scripts/` folder (`${CLAUDE_SKILL_DIR}/scripts/`; if that variable is empty, use the folder this SKILL.md is in). They import the shared library from the sibling `_spotkit/` folder, so keep the skills folder intact.
- Every script prints JSON. `"ok": false` comes with an `error` (and often a `hint`) - read it and relay it rather than retrying blindly.
- Settings and state are in `./.spotkit/` of the project (`config.json`, paper account, journal). Create it once with the spot-bot-runner skill (`run_bot.py init`). Defaults apply if it is missing.
- Global flags (`--demo`, `--exchange`, `--live`, and any others shown before the subcommand in the examples) must come **before** the subcommand.
- Add `--demo` to run against a built-in synthetic market with no network or keys. Say so when you do: demo prices are not real.
- Dependencies: `pip install -r requirements.txt` (ccxt, pandas, numpy, tradingview-ta, tradingview-screener).
