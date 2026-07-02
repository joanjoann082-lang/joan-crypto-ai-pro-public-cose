# Research Promotion Policy V1

## Purpose

Convert institutional Bayesian Evidence into controlled research-grade micro-probe eligibility.

## Core rule

No direct OPEN is allowed.

Strong forward-only evidence can only become a CANARY_MICRO_PROBE candidate.

## Inputs

- latest_bayesian_evidence_v1
- decisions

## Outputs

- research_promotion_decisions_v1
- latest_research_promotion_v1

## Safety limits

- BTCUSDT and ETHUSDT only
- no direct OPEN
- max 2 canary probes per setup per 24h
- max 4 global canary probes per 24h
- size multiplier cap 0.025
- absolute size cap 250 USD
- bounded storage: 500 rows

## Non-goals

This patch does not modify:

- runner
- decision engine
- risk
- broker
- execution
- dashboard
- Telegram

## Next stage

After audit, connect this policy to Statistical Edge Authority as a canary gate only.
