# Live trading checklist

## Before the first live order

1. **Paper first.** Run the bot on paper for at least two to four weeks or 30+ trades. Compare the journal stats with the walk-forward backtest. If paper results are far worse than the backtest, the backtest was wrong.
2. **API key hygiene.** Create a dedicated key with *spot trading only*: withdrawals disabled, no margin/futures permission, IP whitelist set to the machine running the bot. Consider a sub-account holding only the capital the bot may use.
3. **Start small.** First week: `risk.risk_per_trade_pct` 0.25-0.5 and a low `max_total_exposure_pct`. The goal is to verify fills, fees and stops on your exchange, not to make money.
4. **Verify one round trip manually.** Preview and confirm a minimum-size buy, check it on the exchange, then sell it. Check that the journal's fee and P&L match the exchange's trade history.

## Setup

```bash
export EXCHANGE_API_KEY=...        # from your shell profile or secret manager, never in the repo
export EXCHANGE_API_SECRET=...
export EXCHANGE_API_PASSWORD=...   # only for exchanges that issue a passphrase (OKX, KuCoin, Bitget)
export SPOTKIT_LIVE=1
python3 ../spot-bot-runner/scripts/run_bot.py set mode=live exchange=binance
python3 scripts/trade.py --live account
```

Exchange ids are ccxt ids: `binance`, `bybit`, `okx`, `kraken`, `kucoin`, `cryptocom`, `coinbase`, `bitget`, `gateio` and others.

## How live fills are recorded

- Market orders: the fill price and fee come from the exchange's order response (re-fetched if the response is incomplete).
- Fees charged in the base coin (Binance without BNB discount) reduce the position quantity, so a later "sell all" matches the actual balance.
- Limit orders that do not fill immediately are tracked in `live_positions.json` and become positions when `trade.py --live sync` or the bot cycle sees them filled.
- Live state is separate from paper state. The live broker manages only positions it opened.

## Stops on live

Default: soft stops enforced by the bot each cycle. The loop interval is your worst-case reaction time, and a stopped bot means no stop.

Optional: `run_bot.py set live.exchange_stops=true` places a native stop-loss order after each buy using ccxt's unified `stopLossPrice` parameter. Support and semantics differ by exchange (some need stop-limit, some reserve the balance so a take-profit cannot coexist). The buy result's `native_stop` field says whether it was placed; if not, the soft stop still applies. Test with a minimum-size position before relying on it. The native order is cancelled automatically before the bot or a manual sell closes the position.

## Unattended live trading

`run_bot.py --live loop` and `tv_webhook.py --live` also need `live.autonomous=true`. That setting is the user's explicit statement that the bot may place real orders without asking. Do not set it on their behalf without a clear instruction to do so.

## Stopping everything

```bash
python3 ../risk-management/scripts/risk_tool.py halt --reason "manual"   # no new buys from the next cycle
python3 scripts/trade.py --live account                                    # see what is open
python3 scripts/trade.py --live sell BTC/USDT --confirm                    # close positions one by one
```

Revoking the API key on the exchange is the ultimate off switch.

## Common errors

| Error | Meaning |
|---|---|
| `AuthenticationError` | wrong key/secret, missing passphrase, or IP not whitelisted |
| `InsufficientFunds` | free quote balance is lower than the order (funds may be locked in open orders) |
| `InvalidOrder` | below the exchange minimum or bad precision; check `market_data.py market SYMBOL` |
| `NetworkError` / `RequestTimeout` | exchange unreachable; the order may or may not have been placed - check `account` before retrying |
