---
name: strategy-backtesting
description: Backtest crypto spot strategies on historical candles with realistic fees, slippage, next-bar fills and risk-based sizing; compare strategies side by side; and run walk-forward validation to detect overfitting. Built-in strategies - confluence (the bot's own), EMA cross, RSI mean reversion, Donchian breakout, MACD trend. Use this whenever the user asks whether a strategy works or would have made money, wants to test, optimise or compare strategies or parameters, asks about win rate, Sharpe, drawdown or expectancy, or before changing what the live bot trades.
---

# Strategy backtesting

```bash
S="${CLAUDE_SKILL_DIR}/scripts/run_backtest.py"
python3 "$S" list                                                   # strategies and their parameter grids
python3 "$S" --limit 5000 run BTC/USDT --strategy confluence        # one run, metrics + warnings
python3 "$S" --limit 5000 run BTC/USDT --strategy ema_cross --params '{"fast":10,"slow":50}' --equity-csv eq.csv
python3 "$S" --timeframe 4h --limit 4000 compare ETH/USDT           # all strategies, default params
python3 "$S" --limit 8000 walkforward SOL/USDT --strategy confluence --folds 4
python3 "$S" --csv btc_1h.csv run BTC/USDT                          # your own OHLCV file
```

Global flags (`--limit`, `--timeframe`, `--csv`, `--demo`) go **before** the subcommand. Fees, slippage, risk per trade, stop and target settings are read from `.spotkit/config.json`, so a backtest reflects the bot as currently configured.

## What makes the numbers trustworthy

- A signal on bar *t* fills at the open of bar *t+1*. No look-ahead.
- Taker fee and slippage on every fill. Costs are what kill high-frequency ideas; never report results with costs removed.
- If one bar touches both stop and target, the stop is assumed to fill first.
- Long-only, one position per symbol, sized by the same risk rule the bot uses.

## The procedure

1. **Get enough data.** At least 2000 candles, ideally spanning both a rally and a decline. A strategy tested only in a bull run has only been shown to work in a bull run.
2. **`compare`** to see which approaches suit the pair and timeframe. Always state `buy_and_hold_return_pct` next to it.
3. **`walkforward`** on the winner. This optimises parameters on a training window and trades the following unseen window, repeated across folds. The `combined_out_of_sample` block is the realistic expectation; the in-sample numbers are not.
4. Repeat on two or three other pairs. An edge that exists on one coin only is probably noise.
5. Paper trade it (spot-bot-runner skill) before any live capital.

## Reporting results

Give the headline in one line (return, max drawdown, trades, vs buy-and-hold), then a small metrics table, then the caveats. Relay everything in `warnings` and the walk-forward `verdict` - they are there so a flattering number is not taken at face value. In particular:

- fewer than 30 trades: not evidence of anything
- out-of-sample Sharpe far below in-sample, or best parameters changing every fold: overfit
- beating buy-and-hold is not required; lower drawdown with reasonable return can be the point - but say which it is

`robust: true` means most out-of-sample windows were profitable and the combined out-of-sample return is positive. It is a minimum bar, not a promise. Past performance does not guarantee future results; say so once.

Metric definitions, the strategy rules and how to add a strategy are in `references/backtesting-guide.md`.

## Applying a result to the bot

Only the `confluence` strategy is wired into the bot. To adopt validated thresholds:

```bash
python3 "${CLAUDE_SKILL_DIR}/../spot-bot-runner/scripts/run_bot.py" set strategy.entry_score=50 strategy.exit_score=-20
```

## Conventions shared by all spot-trading skills

- Scripts live in this skill's `scripts/` folder (`${CLAUDE_SKILL_DIR}/scripts/`; if that variable is empty, use the folder this SKILL.md is in). They import the shared library from the sibling `_spotkit/` folder, so keep the skills folder intact.
- Every script prints JSON. `"ok": false` comes with an `error` (and often a `hint`) - read it and relay it rather than retrying blindly.
- Settings and state are in `./.spotkit/` of the project (`config.json`, paper account, journal). Create it once with the spot-bot-runner skill (`run_bot.py init`). Defaults apply if it is missing.
- Global flags (`--demo`, `--exchange`, `--live`, and any others shown before the subcommand in the examples) must come **before** the subcommand.
- Add `--demo` to run against a built-in synthetic market with no network or keys. Say so when you do: demo prices are not real.
- Dependencies: `pip install -r requirements.txt` (ccxt, pandas, numpy, tradingview-ta, tradingview-screener).
