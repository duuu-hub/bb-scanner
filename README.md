# bb-scanner

Bitget USDT perpetual futures Bollinger Band scanner.

## Scan logic

BB(20,2), latest completed candle, checked in this order:

- 1W
- 1D
- 12H
- 4H
- 1H
- 30M
- 15M

Stages:

- PRE-HEAT 4/7: 1W + 1D + 12H + 4H above upper BB
- PRE-HEAT 5/7: + 1H
- PRE-HEAT 6/7: + 30M
- EXTREME 7/7: + 15M

The scanner checks every 15 minutes at minute 01/16/31/46. GitHub scheduled jobs can start a little late.

## Telegram secrets

Repository Settings -> Secrets and variables -> Actions -> New repository secret

Create:

- TELEGRAM_BOT_TOKEN
- TELEGRAM_CHAT_ID

Do not put these values directly in source code.

## Manual test

Open Actions -> Bitget BB Scanner -> Run workflow.

A manual run sends a completion summary to Telegram even if there are no 4/7+ candidates. Scheduled runs only notify on a new candidate, a stage upgrade, or a >=5% live-price move since the previous alert.

## Safety

This repository reads public market data only. It does not place orders and does not require a Bitget API key.
