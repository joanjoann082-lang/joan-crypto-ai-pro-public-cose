# State Integrity & Reconciliation V1

## Purpose

State Integrity V1 repairs contaminated paper state created before Execution Contract V1.

It does not generate alpha.
It does not modify strategy logic.
It does not modify decision thresholds.
It does not replace Execution Contract V1.

## Responsibilities

- detect same-symbol open conflicts
- build immutable reconciliation plans
- validate mark prices are fresh
- require plan hash before apply
- close legacy conflicts in paper ledger
- mark reconciliation trades as non-strategy-attributable
- create a clean post-reconciliation baseline
- write audit trail to runtime_events and state_integrity_events

## Non-overlap

- Execution Contract V1 prevents future bad execution.
- State Integrity V1 reconciles old bad state.
- Performance Stack V1 measures results.
- Setup Authority V1 will later enforce setup-level edge.

## Apply protocol

1. Stop runner.
2. Backup DB.
3. Dry-run.
4. Copy PLAN_HASH.
5. Apply with PLAN_HASH.
6. Validate zero open conflicts.
7. Restart runner only after validation.
