---
name: tradingview-analysis
description: TradingView analysis for crypto - TradingView's own technical ratings (Strong Buy to Strong Sell, with oscillator and moving-average breakdown) across timeframes, whole-exchange screeners (momentum, oversold pullbacks, volume breakouts), a Pine Script strategy that mirrors the bot's signals on a TradingView chart, and a webhook receiver that turns TradingView alerts into risk-checked spot orders. Use this whenever the user mentions TradingView, TV ratings, a screener, Pine Script, chart alerts or webhooks, or wants a second opinion on a signal from an independent source.
---

# TradingView analysis

Uses TradingView's public scanner data - no TradingView account or API key. It is a read-only analysis source: orders still go to the exchange through the spot-order-execution skill.

## Ratings

```bash
D="${CLAUDE_SKILL_DIR}/scripts"
python3 "$D/tv_analysis.py" BTC/USDT ETH/USDT --timeframes 15m,1h,4h,1d
python3 "$D/tv_analysis.py" BTC/USD --tv-exchange COINBASE
```

Per timeframe you get `recommendation` (STRONG_BUY, BUY, NEUTRAL, SELL, STRONG_SELL), the buy/neutral/sell indicator counts behind it, separate `oscillators` and `moving_averages` verdicts, and key values. `alignment` is `bullish`/`bearish` only when every requested timeframe agrees, otherwise `mixed`.

How to use it well:
- Treat it as **confirmation**, not a trigger. The rating is a vote count over ~26 lagging indicators; it is late at turns and noisy on short timeframes.
- When moving averages say BUY and oscillators say SELL, price is extended inside an uptrend - a pullback entry is better than chasing.
- Compare with the technical-analysis skill's score. Agreement raises confidence; disagreement is a reason to wait, and worth telling the user about.
- The TradingView exchange prefix (`tradingview.exchange` in config, default BINANCE) must list the pair. A symbol error usually means the wrong prefix.

To make the bot require this confirmation before every entry: `run_bot.py set strategy.tradingview_confirm=true`.

## Screener

```bash
python3 "$D/tv_screener.py" --list
python3 "$D/tv_screener.py" --screen momentum --limit 20
python3 "$D/tv_screener.py" --screen oversold_uptrend
python3 "$D/tv_screener.py" --screen breakout_volume --min-volume 20000000
```

One request covers the whole exchange, so this is the fast way to build a shortlist. Screens: `momentum` (above EMA50/200, RSI 50-70, ADX > 20, positive rating), `oversold_uptrend` (above EMA200 with RSI < 35), `breakout_volume` (+3% on 2x volume), `top_volume`. Pass the shortlist to the market-scanner skill (`scan.py --symbols ...`) to score it with the bot's own rules.

## Seeing the bot's signals on a TradingView chart

`references/spotkit_confluence.pine` is a Pine v6 strategy implementing the same confluence score, stop and target logic as the bot. When the user wants to see or tune signals visually, tell them to paste it into TradingView's Pine Editor and add it to the chart. Its Strategy Tester is a useful cross-check of the strategy-backtesting skill, though the two will not match trade-for-trade (different data feed and fill model).

## Alerts to orders (webhook)

Read `references/webhooks.md` before setting this up - it covers the alert JSON, exposing the port safely, and the live-trading locks. In short:

```bash
export TV_WEBHOOK_SECRET='a-long-random-string'
python3 "$D/tv_webhook.py"                                   # paper account, listens on 127.0.0.1:8787
python3 "$D/tv_webhook.py" --test '{"secret":"...","action":"buy","symbol":"BTCUSDT"}'   # process one payload and exit
```

Every alert-driven buy is sized and checked by the risk engine exactly like a bot entry; an alert cannot bypass limits, and a wrong secret gets a 403.

## Conventions shared by all spot-trading skills

- Scripts live in this skill's `scripts/` folder (`${CLAUDE_SKILL_DIR}/scripts/`; if that variable is empty, use the folder this SKILL.md is in). They import the shared library from the sibling `_spotkit/` folder, so keep the skills folder intact.
- Every script prints JSON. `"ok": false` comes with an `error` (and often a `hint`) - read it and relay it rather than retrying blindly.
- Settings and state are in `./.spotkit/` of the project (`config.json`, paper account, journal). Create it once with the spot-bot-runner skill (`run_bot.py init`). Defaults apply if it is missing.
- Global flags (`--demo`, `--exchange`, `--live`, and any others shown before the subcommand in the examples) must come **before** the subcommand.
- Add `--demo` to run against a built-in synthetic market with no network or keys. Say so when you do: demo prices are not real.
- Dependencies: `pip install -r requirements.txt` (ccxt, pandas, numpy, tradingview-ta, tradingview-screener).
