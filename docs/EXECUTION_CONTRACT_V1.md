# Execution Contract V1

## Purpose

Execution Contract V1 defines the minimum institutional safety rules required before any paper trade can be opened.

This contract is intentionally conservative. Its job is not to find alpha. Its job is to prevent execution errors that make alpha measurement unreliable.

## Execution rules

1. WAIT is never executable.
2. PROBE is advisory only and must never open a position.
3. OPEN is the only executable decision.
4. Broker is the final execution authority.
5. Max one open position per symbol.
6. Involuntary hedging is forbidden: no LONG and SHORT at the same time on the same symbol.
7. CAPITULATION_REBOUND_LONG is quarantined until live/forward evidence improves.
8. Any rejected execution must be recorded as EXECUTION_REJECTED in runtime_events.
9. Existing positions are not force-closed by this contract.
10. Runtime pause remains valid and independent.

## Current policy

- max open positions per symbol: 1
- PROBE execution: disabled
- same-symbol duplicate: disabled
- opposite-side hedge: disabled
- quarantined setups:
  - CAPITULATION_REBOUND_LONG

## Why this exists

The bot had opened contradictory positions:

- BTCUSDT LONG and BTCUSDT SHORT
- ETHUSDT LONG and ETHUSDT SHORT

It also executed PROBE decisions as paper trades.

That made performance analysis invalid. This contract prevents that.
