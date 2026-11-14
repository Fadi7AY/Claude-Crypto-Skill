---
name: risk-management
description: Position sizing and risk control for crypto spot trading - how much to buy for a given stop, a pre-trade checklist (risk per trade, position and exposure caps, reward:risk, liquidity, spread, cooldowns), daily-loss and drawdown circuit breakers, and a kill switch. Use this before any buy, whenever the user asks how much to buy, what size, how much they could lose, whether a trade is too big or too risky, about stops, drawdown or exposure, or wants to pause/halt/resume trading or change risk limits.
---

# Risk management

Sizing decides outcomes more than entries do. A strategy with an edge still goes broke when single losses are large, so every buy in this toolkit - manual, bot or webhook - passes the same checks.

```bash
S="${CLAUDE_SKILL_DIR}/scripts/risk_tool.py"
python3 "$S" status                                        # equity, day P&L, drawdown, halts, limits
python3 "$S" size BTC/USDT --stop 61200                    # quantity for the account at current price
python3 "$S" size BTC/USDT --entry 63000 --stop 61200 --equity 5000   # what-if on a given equity
python3 "$S" check BTC/USDT --qty 0.03 --stop 61200 --target 67000    # full pre-trade checklist
python3 "$S" halt --reason "news event"                    # kill switch: blocks every new buy
python3 "$S" resume                                        # clears kill switch and drawdown halt
python3 "$S" limits                                        # configured limits and fees
```

Add `--live` to size against the live account instead of the paper account.

## The sizing rule

`quantity = (equity x risk_per_trade_pct) / (entry - stop + round-trip fees and slippage)`

then capped by, in order: `max_position_pct`, remaining room under `max_total_exposure_pct`, and available cash. `limited_by` says which cap bound. With a tight stop the position cap usually binds, which means the trade risks *less* than the configured percentage - that is fine and expected.

A stop is mandatory. No stop means no defined loss, so the size cannot be computed and the order is refused. Never suggest sizing by "how much do you want to spend" without also stating what is lost at the stop.

## The pre-trade checklist

`check` returns `approved` plus `violations` (blocking) and `warnings`. When something is violated, tell the user which rule and the actual number, and suggest the fix (smaller size, wider target, wait for cooldown). Do not look for a way around a violation; if the user wants different limits, that is a deliberate config change (below), made once, not per trade.

Defaults and what they protect against are in `references/risk-rules.md` - read it when the user asks why a rule exists or wants to tune limits.

## Circuit breakers

- **Daily loss limit** (default 3%): no new buys for the rest of the UTC day. Resets automatically.
- **Max drawdown** (default 15% from peak equity): trading halts until `resume`. Before resuming, look at the trade-journal stats with the user; a drawdown that deep means the strategy or the market changed.
- **Kill switch**: `halt` creates `.spotkit/KILL`. The bot loop sees it on its next cycle. Exits still work while halted - halting never traps a position.

## Changing limits

```bash
python3 "${CLAUDE_SKILL_DIR}/../spot-bot-runner/scripts/run_bot.py" set risk.risk_per_trade_pct=0.5 risk.max_open_positions=3
```

Confirm the new values with the user before writing them. Raising `risk_per_trade_pct` above 2 or `max_total_exposure_pct` above 80 deserves an explicit warning about what a five-trade losing streak would cost.

## Conventions shared by all spot-trading skills

- Scripts live in this skill's `scripts/` folder (`${CLAUDE_SKILL_DIR}/scripts/`; if that variable is empty, use the folder this SKILL.md is in). They import the shared library from the sibling `_spotkit/` folder, so keep the skills folder intact.
- Every script prints JSON. `"ok": false` comes with an `error` (and often a `hint`) - read it and relay it rather than retrying blindly.
- Settings and state are in `./.spotkit/` of the project (`config.json`, paper account, journal). Create it once with the spot-bot-runner skill (`run_bot.py init`). Defaults apply if it is missing.
- Global flags (`--demo`, `--exchange`, `--live`, and any others shown before the subcommand in the examples) must come **before** the subcommand.
- Add `--demo` to run against a built-in synthetic market with no network or keys. Say so when you do: demo prices are not real.
- Dependencies: `pip install -r requirements.txt` (ccxt, pandas, numpy, tradingview-ta, tradingview-screener).
