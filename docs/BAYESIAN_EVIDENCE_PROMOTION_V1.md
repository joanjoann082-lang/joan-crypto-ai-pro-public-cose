# Bayesian Evidence Promotion V1

## Purpose

Upgrade Evidence from raw positive/negative summaries to institutional weighted evidence.

## Inputs

- forward_cases
- forward_results
- evidence_clean_positions_v1
- evidence_positions_with_provenance_v1

## Outputs

- bayesian_evidence_scores_v1
- latest_bayesian_evidence_v1

## Core rules

Forward-only evidence cannot directly become OPEN.

Legacy/pre-contract/reconciliation outcomes are excluded through Evidence Registry.

Clean execution evidence has higher institutional value than shadow/forward evidence.

## Scoring

The model applies:

- forward sample threshold
- clean execution sample threshold
- Bayesian shrinkage
- prior neutral expectancy
- effective sample size
- divergence penalty
- PF requirement
- MAE constraint
- robustness score
- quality score

## Promotion states

- REJECTED_NO_EDGE
- RESEARCH_CANDIDATE
- RESEARCH_CANDIDATE_PENDING_CLEAN_SAMPLE
- MICRO_PROBE_CANDIDATE
- OPEN_ELIGIBLE

## Non-goals

This patch does not modify:

- risk
- broker
- execution
- runner loop
- dashboard
- Telegram

## Next stage

After audit, this can be connected to Statistical Edge Authority as a read-only institutional constraint.

No direct OPEN is allowed without clean execution evidence.
