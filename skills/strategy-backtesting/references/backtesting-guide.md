# Backtesting guide

## Metrics

| Metric | Meaning | Rough guide |
|---|---|---|
| `total_return_pct` | net return over the test, after costs | compare with `buy_and_hold_return_pct` |
| `cagr_pct` | annualised return | unreliable on tests shorter than about 6 months |
| `max_drawdown_pct` | worst peak-to-trough equity drop | beyond -25% is hard to live with |
| `sharpe` | annualised return / volatility of bar returns | above 1 decent, above 2 on a small sample is suspicious |
| `sortino` | like Sharpe, but only downside volatility | |
| `win_rate_pct` | share of winning trades | meaningless without average win/loss |
| `profit_factor` | gross profit / gross loss | 1.3-2 is realistic; above 3 usually overfit |
| `expectancy_r` | average trade result in R (multiples of planned risk) | must be positive after costs |
| `exposure_pct` | share of time in a position | low exposure with similar return to buy-and-hold is a real benefit |

## Built-in strategies

| Name | Entry | Exit | Suits |
|---|---|---|---|
| `confluence` | score >= `entry_score` | score <= `exit_score`, stop, target | trending markets |
| `ema_cross` | fast EMA crosses above slow EMA, price above trend EMA | fast below slow | sustained trends |
| `rsi_reversion` | RSI crosses back above `buy` while above trend EMA | RSI >= `sell` | pullbacks inside uptrends |
| `donchian_breakout` | close above prior `entry`-bar high | close below prior `exit`-bar low | breakouts, volatile alts |
| `macd_trend` | MACD histogram turns positive above trend EMA | histogram negative | momentum swings |

All exits also include the ATR stop (`risk.stop_atr_mult`), optional fixed target (`risk.take_profit_r`) and optional trailing stop (`risk.trail_atr_mult`).

## Walk-forward

History after a 210-bar warm-up is split into `folds` consecutive test windows, each preceded by a training window (70/30 by default). For each fold the parameter grid is searched on the training window (objective: Sharpe, minimum 5 trades) and the best set is traded on the test window. Indicators are computed once on the full history, so test windows start with warmed-up values without seeing future prices.

Read it as:
- `folds[].out_of_sample` profitable in most folds -> the idea generalises
- out-of-sample Sharpe at least half of in-sample -> acceptable decay
- best parameters stable across folds -> a broad optimum rather than a lucky spike

## Common ways backtests lie

- **Overfitting**: many parameters, few trades. Prefer fewer parameters and round values.
- **Regime dependence**: every trend strategy looks brilliant in a bull market.
- **Survivorship**: testing only coins that are large today ignores those that died.
- **Costs**: real slippage on small-cap pairs is several times the 5 bps default; raise `fees.slippage_bps` for them.
- **Execution gap**: the live bot checks stops once per cycle; the backtest fills exactly at the stop.

## Adding a strategy

In `skills/_spotkit/spotkit/backtest.py`, write a function that takes the indicator frame (`indicators.add_all`) plus parameters and returns two boolean Series, `(entry, exit)`, computed only from data up to each bar. Register it in `STRATEGIES` with a small parameter grid. Then add a check to `tests/test_offline.py` and run the suite. The live bot trades only `confluence`; wiring another strategy into `bot.py` means replacing the score thresholds there with the new entry/exit series.
