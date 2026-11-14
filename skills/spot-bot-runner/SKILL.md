---
name: spot-bot-runner
description: Set up, configure, run and monitor the automated crypto spot trading bot - the loop that scans a watchlist, enters on confluence signals with risk-sized positions, and manages stops, targets, trailing stops and signal exits, on paper or live. Use this for anything about the bot as a whole - first-time setup or config, starting, stopping or scheduling it, changing its watchlist, timeframe, exchange or strategy thresholds, checking what it is doing or why it did or did not trade, and moving from paper to live.
---

# Spot bot runner

The bot ties the other skills together. One **cycle** does, in order:

1. fills any resting limit orders
2. checks every open position against its stop, target and trailing stop
3. for each symbol with a newly closed candle: exits holdings whose score fell to `strategy.exit_score`; for watchlist symbols not held, enters when score >= `strategy.entry_score`, the higher timeframe is not bearish, optional TradingView confirmation passes, and the risk engine approves the sized order
4. records everything in the journal and reports account and risk status

Signals are evaluated once per closed candle per symbol, so running cycles more often only makes stops react faster - it does not create extra trades.

```bash
S="${CLAUDE_SKILL_DIR}/scripts/run_bot.py"
python3 "$S" init --set exchange=binance --set 'watchlist=["BTC/USDT","ETH/USDT","SOL/USDT"]'
python3 "$S" config                         # effective settings
python3 "$S" set timeframe=1h higher_timeframe=4h strategy.entry_score=45
python3 "$S" cycle --dry-run                # what it would do now, no orders
python3 "$S" cycle                          # one real cycle on the paper account
python3 "$S" loop --interval 300            # run forever, one JSON line per cycle
python3 "$S" status                         # positions, equity, risk state, last evaluated bars
```

## First-time setup with a user

1. `pip install -r requirements.txt`, then `init`. Ask which exchange and quote currency they use and which pairs to watch; keep the other defaults unless they have a view.
2. `python3 "$S" --demo cycle --dry-run` proves the install works offline.
3. `cycle --dry-run` against the real exchange, and walk through the report: what it would buy, what it skipped and why.
4. Validate the strategy on their pairs with the strategy-backtesting skill (walk-forward).
5. Start the paper loop. Review with the trade-journal skill after a week or two.

Do not suggest live trading until steps 4 and 5 are done; if the user wants to skip them, say what they are skipping and let them decide.

## Reading a cycle report

- `entries`: orders placed (or `would_buy` in a dry run) with quantity, stop, target, risk amount and the reason string.
- `exits`: closed positions with reason (`stop_loss`, `take_profit`, `signal_exit`) and P&L.
- `skipped`: every symbol that was evaluated and not bought, with the reason. This answers "why didn't it trade?" - the usual causes are score below threshold, higher timeframe bearish, a risk violation, or a cooldown.
- `errors`: per-symbol failures; one bad symbol does not stop the cycle.
- `risk.can_open_trades: false`: a breaker or the kill switch is on (risk-management skill).

An empty report after the first cycle on a bar is normal: nothing is re-evaluated until the next candle closes.

## Keeping it running

The loop is a foreground process. For anything beyond a test it needs a supervisor, otherwise soft stops stop working the moment the terminal closes:

- quick: `tmux new -s bot 'python3 .../run_bot.py loop --interval 300'`
- cron alternative: `*/5 * * * * cd /path/to/project && python3 .../run_bot.py cycle >> .spotkit/bot.log 2>&1`
- permanent: a systemd service or container with restart-on-failure; a template is in the plugin README.

The loop re-reads `config.json` every cycle, so config changes and the kill switch take effect without a restart. Pick an interval well below the entry timeframe (5 minutes for 1h candles).

## Stopping

`risk_tool.py halt` (risk-management skill) blocks new buys while the loop keeps managing exits - usually what "stop the bot" should mean while positions are open. Killing the process stops stop-loss enforcement too; if the user wants that, close or protect the positions first and tell them why.

## Paper to live

Requires all locks described in the spot-order-execution skill's `references/live-trading.md`, plus `live.autonomous=true` for the loop. That flag is the user's explicit permission for unattended real orders - set it only on their clear instruction, after showing them the current risk limits. Then: `SPOTKIT_LIVE=1 python3 "$S" --live loop --interval 300`.

## Config reference

| Key | Default | Meaning |
|---|---|---|
| `exchange`, `quote` | binance, USDT | ccxt exchange id and quote currency |
| `mode` | paper | `paper` or `live` |
| `watchlist` | BTC, ETH, SOL | pairs the bot may buy |
| `timeframe`, `higher_timeframe` | 1h, 4h | entry timeframe and trend filter |
| `strategy.entry_score` / `exit_score` | 40 / -20 | confluence thresholds |
| `strategy.require_htf_trend` | true | skip entries when the higher timeframe score is negative |
| `strategy.tradingview_confirm` | false | also require a TradingView BUY rating |
| `fees.taker`, `fees.slippage_bps` | 0.001, 5 | used by paper fills, sizing and backtests |
| `paper.starting_balance` | 10000 | paper account size |
| `risk.*` | see risk-management | sizing and limits |
| `live.autonomous`, `live.exchange_stops` | false, false | unattended live orders; native stop orders |

## Conventions shared by all spot-trading skills

- Scripts live in this skill's `scripts/` folder (`${CLAUDE_SKILL_DIR}/scripts/`; if that variable is empty, use the folder this SKILL.md is in). They import the shared library from the sibling `_spotkit/` folder, so keep the skills folder intact.
- Every script prints JSON. `"ok": false` comes with an `error` (and often a `hint`) - read it and relay it rather than retrying blindly.
- Settings and state are in `./.spotkit/` of the project (`config.json`, paper account, journal). Create it once with the spot-bot-runner skill (`run_bot.py init`). Defaults apply if it is missing.
- Global flags (`--demo`, `--exchange`, `--live`, and any others shown before the subcommand in the examples) must come **before** the subcommand.
- Add `--demo` to run against a built-in synthetic market with no network or keys. Say so when you do: demo prices are not real.
- Dependencies: `pip install -r requirements.txt` (ccxt, pandas, numpy, tradingview-ta, tradingview-screener).
