# TradingView alerts to spot orders

## Flow

TradingView alert fires -> HTTPS POST to your URL -> `tv_webhook.py` checks the secret -> risk engine sizes and checks the order -> broker (paper by default) -> journal.

## 1. Start the receiver

```bash
export TV_WEBHOOK_SECRET='at-least-12-random-characters'
python3 scripts/tv_webhook.py            # paper mode, 127.0.0.1:8787 (port from tradingview.webhook_port)
```

It binds to localhost on purpose. TradingView needs a public HTTPS URL on port 443 (or 80), so put a tunnel or reverse proxy in front of it rather than opening the port directly:

- quick: `cloudflared tunnel --url http://localhost:8787` or `ngrok http 8787`
- permanent: nginx/Caddy with TLS on a VPS, proxying to `127.0.0.1:8787`

Webhook alerts need a paid TradingView plan and two-factor authentication enabled on the TradingView account.

## 2. Alert message

In the alert dialog, enable "Webhook URL", paste the public URL, and use this as the message:

```json
{"secret": "at-least-12-random-characters", "action": "buy", "symbol": "{{ticker}}", "stop": {{plot("Stop")}}}
```

| Field | Required | Meaning |
|---|---|---|
| `secret` | yes | must equal `TV_WEBHOOK_SECRET` |
| `action` | yes | `buy` or `sell` (sell closes the whole bot-managed position) |
| `symbol` | yes | `BTCUSDT`, `BINANCE:BTCUSDT` or `BTC/USDT`; quote must match config |
| `stop` | no | stop price; if missing, the ATR/support stop from analysis is used |
| `target` | no | take-profit price; if missing, `risk.take_profit_r` is applied |
| `reason` | no | free text stored in the journal |

The bundled Pine strategy (`spotkit_confluence.pine`) already emits this JSON through `alert()`. Create the alert with condition "Spotkit Confluence" -> "alert() function calls only" and leave the message box as is; set the script's "Webhook secret" input to your secret.

## 3. What can come back

- `403` bad secret
- `200 {"status": "filled", ...}` order placed
- `200 {"status": "rejected", "why": [...]}` the risk engine said no (already holding, exposure cap, kill switch, cooldown, daily loss limit...)
- `200 {"status": "ignored"}` sell alert with no position

Rejections are logged as `entry_rejected` events in the journal.

## Live mode

Alert-driven live trading is unattended trading. It needs all of:

1. `mode=live` and `live.autonomous=true` in `.spotkit/config.json`
2. `SPOTKIT_LIVE=1`, `EXCHANGE_API_KEY`, `EXCHANGE_API_SECRET` in the environment
3. `python3 scripts/tv_webhook.py --live`

Run it on paper for a couple of weeks first and compare the journal with the TradingView strategy tester. Anyone who learns the secret and URL can trigger buys within your risk limits, so keep the secret out of screenshots and shared chart layouts, and rotate it if in doubt.
