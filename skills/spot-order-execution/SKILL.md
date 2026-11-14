---
name: spot-order-execution
description: Place and manage crypto spot orders - buy, sell, limit orders, stop-loss and take-profit, trailing stops, balances and open positions - on a paper account by default or a real exchange through ccxt when live trading is explicitly unlocked. Use this whenever the user wants to buy or sell a coin, open or close a position, place or cancel an order, move a stop, take profit, or see their balance, holdings or P&L. Spot only - no margin, futures, shorting or withdrawals.
---

# Spot order execution

Two accounts, same commands:

- **Paper** (default): simulated fills against live prices with fees and slippage. State in `.spotkit/paper_account.json`.
- **Live**: real orders. Only reachable with `--live` and every lock in "Going live" below.

## The order workflow

Orders are two-step by design: a command without `--confirm` returns a **preview** and places nothing.

1. Run the command without `--confirm`.
2. Show the user the preview: quantity, entry, notional, stop, target, the amount at risk and reward:risk, plus the risk check result.
3. Place it with `--confirm` only after the user has agreed to that specific order in this conversation. For live orders this is not optional, even if the user earlier said "just trade for me" - real money moves on their explicit yes to concrete numbers. (Unattended trading is the bot's job and has its own switch.)
4. Report the fill: price, fee, stop and target now attached.

```bash
S="${CLAUDE_SKILL_DIR}/scripts/trade.py"
python3 "$S" account                                   # cash, equity, positions with unrealized P&L, open orders
python3 "$S" buy BTC/USDT                              # preview; size from risk rules, stop from analysis
python3 "$S" buy BTC/USDT --stop 61200 --target 67000 --reason "4h breakout" --confirm
python3 "$S" buy ETH/USDT --spend 500 --stop 2900     # fixed quote amount instead of risk sizing
python3 "$S" buy SOL/USDT --type limit --price 140 --stop 133 --confirm
python3 "$S" sell BTC/USDT                             # preview closing the whole position
python3 "$S" sell BTC/USDT --qty 0.01 --reason "partial at 1R" --confirm
python3 "$S" set-stop BTC/USDT --stop 63000            # move stop (e.g. to breakeven); --target, --trail-atr 3
python3 "$S" sync                                      # fill/refresh resting limit orders
python3 "$S" cancel 3
python3 "$S" reset-paper --confirm                     # wipe the paper account
```

## Rules the script enforces

- A buy needs a stop. If none is given it uses the ATR/support stop from analysis and says so in `stop_source`.
- Every buy passes the risk engine. `status: rejected_by_risk` lists the violated rules; explain them rather than resubmitting with tweaked numbers until something passes. Sizing questions belong to the risk-management skill.
- Sells can never exceed the held quantity, and the live broker only sells positions it opened - it will not touch other coins in the account.
- `--reason` is stored in the journal. Always pass one; it is what makes the trade-journal review useful later.

## Stops are soft by default

Stops and targets are stored with the position and enforced by the bot each cycle (spot-bot-runner skill). **If the bot is not running, nothing sells.** After a manual buy, tell the user this and either start the bot loop or, on live, enable native stop orders. `references/live-trading.md` explains both.

## Going live

Read `references/live-trading.md` before the first live order. The locks, all required:

1. `run_bot.py set mode=live` in config
2. environment: `SPOTKIT_LIVE=1`, `EXCHANGE_API_KEY`, `EXCHANGE_API_SECRET` (`EXCHANGE_API_PASSWORD` for OKX/KuCoin-style keys)
3. `--live` on the command

Never ask the user to paste API keys into the chat and never write them to a file in the project; they belong in the shell environment or a secret manager. If a key shows up in the conversation, tell the user to revoke it.

## Conventions shared by all spot-trading skills

- Scripts live in this skill's `scripts/` folder (`${CLAUDE_SKILL_DIR}/scripts/`; if that variable is empty, use the folder this SKILL.md is in). They import the shared library from the sibling `_spotkit/` folder, so keep the skills folder intact.
- Every script prints JSON. `"ok": false` comes with an `error` (and often a `hint`) - read it and relay it rather than retrying blindly.
- Settings and state are in `./.spotkit/` of the project (`config.json`, paper account, journal). Create it once with the spot-bot-runner skill (`run_bot.py init`). Defaults apply if it is missing.
- Global flags (`--demo`, `--exchange`, `--live`, and any others shown before the subcommand in the examples) must come **before** the subcommand.
- Add `--demo` to run against a built-in synthetic market with no network or keys. Say so when you do: demo prices are not real.
- Dependencies: `pip install -r requirements.txt` (ccxt, pandas, numpy, tradingview-ta, tradingview-screener).
