# Risk rules: defaults and reasoning

All values live under `risk` in `.spotkit/config.json`.

| Setting | Default | Why it exists |
|---|---|---|
| `risk_per_trade_pct` | 1.0 | Loss if the stop is hit, as % of equity. At 1%, ten straight losses cost about 9.6%; at 5% they cost 40%. |
| `max_position_pct` | 20 | Caps a single coin. Stops can gap (exchange outage, flash crash); the cap bounds the damage when the stop fails. |
| `max_total_exposure_pct` | 60 | Crypto pairs are highly correlated - five alt positions behave like one big position in a sell-off. Keeps cash for that day. |
| `max_open_positions` | 4 | Same correlation argument, and keeps the book reviewable. |
| `daily_loss_limit_pct` | 3 | Stops revenge trading and limits damage from a broken strategy or bad market day. |
| `max_drawdown_pct` | 15 | Hard stop requiring a human decision. Recovering from -15% needs +17.6%; from -50% needs +100%. |
| `min_reward_risk` | 1.5 | With a 40% win rate you need better than 1.5R winners to be profitable after fees. |
| `stop_atr_mult` | 2.0 | Stop distance in ATRs. Under about 1.5 ATR, normal noise takes the stop out. |
| `take_profit_r` | 2.0 | Fixed target in R multiples; 0 disables it (use with a trailing stop). |
| `trail_atr_mult` | 0 | If set (3 is typical), the stop ratchets up to `highest high - mult x ATR`. Never moves down. |
| `max_spread_bps` | 30 | Wide spreads are an immediate, certain loss on a market order. |
| `min_quote_volume_24h` | 5,000,000 | Illiquid pairs slip badly and are easy to manipulate. |
| `cooldown_minutes_after_stop` | 240 | No re-entry into a coin right after being stopped out of it. |
| `allow_adding_to_position` | false | No averaging down. |

## Profiles

- Conservative: `risk_per_trade_pct` 0.5, `max_total_exposure_pct` 40, `max_open_positions` 3.
- Default: as above.
- Aggressive: `risk_per_trade_pct` 2, `max_total_exposure_pct` 80, `max_open_positions` 6. Expect drawdowns beyond 20%; raise `max_drawdown_pct` deliberately or the breaker will trip.

## Losing streak arithmetic

Probability of at least one streak of N losses in 100 trades at a 45% win rate: N=5 about 92%, N=8 about 31%, N=10 about 10%. Size so that the streak you will certainly meet is survivable.

## What the engine does not cover

- Exchange failure, delisting, stablecoin de-peg, account compromise. Keep only trading capital on the exchange, use an API key without withdrawal permission, and whitelist IPs.
- Soft stops only work while the bot is running. If the process or machine is down, nothing sells. `live.exchange_stops=true` attempts native stop orders, but support differs per exchange; verify on yours with a tiny order.
- Tax. Every sell is a taxable event in many jurisdictions; the journal's CSV export is the record.
