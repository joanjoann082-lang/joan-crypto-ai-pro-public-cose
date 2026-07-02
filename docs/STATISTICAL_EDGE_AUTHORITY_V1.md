# Statistical Edge Authority V1.1

## Purpose

Institutional statistical authority before Risk and Execution.

## Main correction

This version evaluates completed position lifecycle evidence, not individual trade rows.

One closed position = one statistical observation.

This avoids false statistics caused by partial take-profits, giveback exits or multi-row trade logs.

## Pipeline

Strategy
→ Edge Memory
→ Statistical Edge Authority
→ Decision
→ Risk
→ Execution Contract
→ Broker
→ Dashboard / Telegram / Audit

## Responsibility boundary

Statistical Edge Authority decides if a setup has statistical permission.

Execution Contract remains only an emergency execution fallback and market/position validator. It must not be the main statistical authority.

## Statuses

- BLOCKED
- QUARANTINED
- INSUFFICIENT_SAMPLE
- PROBE_ONLY
- PROMISING
- VALIDATED

## Initial policy

- Negative position-level expectancy with enough sample blocks OPEN.
- CAPITULATION_REBOUND_LONG with negative position evidence is quarantined.
- TREND_BOUNCE_SHORT cannot scale to OPEN until enough closed-position evidence exists.
- No DB mutation.
- No runner mutation.
- Rollback through Git.
