---
name: trade-journal
description: Review trading performance from the journal that every paper and live fill is written to - closed trades, win rate, profit factor, expectancy, average R, fees, breakdowns by coin, strategy and exit reason, losing streaks, the order/event log, notes on trades and CSV export. Use this whenever the user asks how the bot or their trading is doing, about P&L, recent trades, what went wrong, why a trade was taken or rejected, wants a daily/weekly review, or needs a trade history export for records or taxes.
---

# Trade journal

Everything the brokers do is recorded automatically in `.spotkit/journal.db` (SQLite): one row per closed round trip, plus an event log of fills, stop changes, rejected entries, webhook calls and bot cycles.

```bash
S="${CLAUDE_SKILL_DIR}/scripts/journal_tool.py"
python3 "$S" stats                          # overall + by symbol / strategy / exit reason, streaks, flags
python3 "$S" --mode live stats              # paper and live are kept apart with --mode
python3 "$S" trades --limit 20              # recent closed trades with entry/exit reasons and R
python3 "$S" trades --symbol BTC/USDT
python3 "$S" events --limit 30              # raw log; --kind entry_rejected shows what risk blocked
python3 "$S" note 12 "entered late, chased the candle"
python3 "$S" export trades.csv              # full history
```

Global `--mode` goes before the subcommand. Open positions are not in the journal until closed; get those from the spot-order-execution skill (`trade.py account`).

## Running a review

Work from the numbers to a small set of observations the user can act on:

1. **Is there an edge?** `expectancy` and `avg_r` positive, `profit_factor` above 1 after fees. Under 30 trades, say plainly that it is too early to tell either way.
2. **Where does the P&L come from?** `by_symbol` and `by_strategy` - often one pair carries or sinks the result.
3. **How do trades end?** `by_exit_reason`. Mostly `stop_loss` with few `take_profit` suggests stops too tight or entries late. Many `signal_exit` losers suggest the exit threshold reacts too slowly. Manual exits that underperform the systematic ones are worth pointing out gently.
4. **Is risk behaving?** A full stop-out should be close to -1R. Losses well beyond -1R mean gaps or stops not being enforced (bot not running).
5. **Costs.** If `fees` rival net P&L, the system trades too often for its edge.
6. Relay the `flags` list; each is a specific, pre-computed warning.

Compare live or paper results with the walk-forward expectation from the strategy-backtesting skill. Paper results far worse than the backtest mean the backtest was optimistic.

Be straight about losses. A review that softens a losing record does the user no favours; pair the honest number with the most likely cause and one concrete change to test.

## Notes

Add a note when the user explains a trade or a lesson ("news spike", "moved stop too early"). Notes are appended, not overwritten, and appear in `trades` and the CSV.

## Conventions shared by all spot-trading skills

- Scripts live in this skill's `scripts/` folder (`${CLAUDE_SKILL_DIR}/scripts/`; if that variable is empty, use the folder this SKILL.md is in). They import the shared library from the sibling `_spotkit/` folder, so keep the skills folder intact.
- Every script prints JSON. `"ok": false` comes with an `error` (and often a `hint`) - read it and relay it rather than retrying blindly.
- Settings and state are in `./.spotkit/` of the project (`config.json`, paper account, journal). Create it once with the spot-bot-runner skill (`run_bot.py init`). Defaults apply if it is missing.
- Global flags (`--demo`, `--exchange`, `--live`, and any others shown before the subcommand in the examples) must come **before** the subcommand.
- Add `--demo` to run against a built-in synthetic market with no network or keys. Say so when you do: demo prices are not real.
- Dependencies: `pip install -r requirements.txt` (ccxt, pandas, numpy, tradingview-ta, tradingview-screener).
