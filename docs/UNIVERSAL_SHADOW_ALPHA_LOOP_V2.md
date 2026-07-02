# Universal Shadow Alpha Loop V2

## Purpose

Create a real institutional learning loop independent from manual StrategyEngine setups.

The bot currently learns mainly from decisions produced by existing strategy candidates. This module adds a universal shadow learning layer:

- BTC LONG
- BTC SHORT
- ETH LONG
- ETH SHORT
- multiple setup families
- multiple horizons
- multiple TP/SL profiles
- context bucket
- thesis / counter-thesis / invalidation

## Architecture

MarketData / ContextEngine  
→ Universal Shadow Alpha Loop V2  
→ universal_shadow_cases_v2  
→ universal_shadow_results_v2  
→ universal_shadow_registry_v2  
→ future Promotion Policy V2  
→ future micro-canary  
→ Risk / Execution

## Isolation

This module does not write to:

- forward_cases
- forward_results
- decisions
- positions
- trades

This prevents contamination of:

- EvidenceEngine V1
- Bayesian Evidence V1
- Telegram threshold advisor
- existing ForwardTester
- StrategyEngine decisions

## Live integration

Runner calls `step_alpha_shadow()` as a separate learning step.

It does not affect:

- decision.py
- risk.py
- broker
- execution
- open thresholds
- probe thresholds

## Output states

- VALIDATED_SHADOW_ALPHA
- RESEARCH_SHADOW_ALPHA
- WATCHLIST
- REJECTED_NEGATIVE_ALPHA

## Next stage

Promotion Policy V2 may later consume `VALIDATED_SHADOW_ALPHA` rows and allow only capped micro-canary.
