# Evidence Engine V1

Single read-only source of statistical evidence.

## Owns

- Closed position evidence
- Forward primary evidence
- Forward shadow evidence
- Schema/source health

## Does not own

- Trade decisions
- Risk sizing
- Execution checks
- Position management
- Dashboard rendering
- Telegram commands

## Pipeline

Evidence Engine
→ Statistical Edge Authority
→ Decision
→ Risk
→ Execution
→ Broker

## Rule

One closed position equals one statistical observation.

Forward OPEN/PROBE is primary evidence.

Forward WAIT is shadow evidence and cannot directly promote to OPEN.
