# Performance Stack V1

## Purpose

Performance Stack V1 is a read-only analytics layer.

It does not trade.
It does not change thresholds.
It does not modify decisions.
It measures whether the bot has edge after Execution Contract V1.

## Why this is required

Before this layer, the bot mixed:

- legacy contradictory positions
- PROBE executions
- low-sample setups
- realized trades
- partial exits
- current open conflicts

This made performance interpretation unreliable.

## Components

- `joanbot/analytics/performance_attribution_v1.py`
- `data/performance_baseline_v1.json`
- `data/reports/performance_attribution_v1_report.json`

The generated files in `data/` are runtime artifacts and must not be committed.

## Metrics

- realized PnL
- winrate
- profit factor
- expectancy
- fees
- best/worst trade
- setup attribution
- reason attribution
- post-baseline tracking
- open conflict detection
- execution rejection tracking

## Authority classification

This stack does not enforce decisions. It produces inputs for Setup Authority V1.

Suggested interpretation:

- `INSUFFICIENT_SAMPLE`: no promotion
- `NEGATIVE_EDGE`: reduce/block candidate
- `MIXED`: watch only
- `PROMISING`: small-size only
- `VALIDATED_CANDIDATE`: candidate for normal size after review

## Next layer

`SETUP_AUTHORITY_V1`

That layer may consume this report and apply decision/risk changes.
