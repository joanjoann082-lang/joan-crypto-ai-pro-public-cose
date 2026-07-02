# Liquidity Intelligence V1

## Purpose

Institutional bounded liquidation intelligence for BTC/ETH.

## Source

Binance USD-M Futures force-order websocket:

- BTCUSDT@forceOrder
- ETHUSDT@forceOrder

## Owns

- Liquidation stream ingestion.
- Normalized liquidation event storage.
- Compact feature snapshots.
- Source health.
- Hard retention.

## Does not own

- Trade decisions.
- Risk sizing.
- Execution permission.
- Position management.
- Dashboard rendering.
- Telegram commands.

## Storage

Tables:

- liquidity_liquidation_events_v1
- liquidity_features_v1
- liquidity_source_health_v1

Hard limits:

- 2000 liquidation events per symbol
- 600 feature snapshots per symbol

No unlimited raw data is allowed.

## Features

- buy_liq_usd
- sell_liq_usd
- total_liq_usd
- net_liq_usd
- imbalance
- decayed_imbalance
- short_squeeze_pressure
- long_flush_pressure
- stress_score
- max_event_usd
- p95_event_usd
- latest_event_age_sec
- dominant_bucket_bps
- nearest_above_bucket_bps
- nearest_below_bucket_bps
- source_health

## Boundary

Liquidity Intelligence is context only.

It cannot directly open trades.

Future integration path:

Liquidity Intelligence
→ Market Context / Execution Quality
→ Statistical Evidence
→ Decision
→ Risk
→ Execution
