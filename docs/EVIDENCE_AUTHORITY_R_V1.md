# Evidence Authority R V1

## Purpose

Institutional R-normalized evidence and statistical authority separation.

## Architecture

EvidenceEngineV1 owns evidence:

- Closed positions
- R-multiple normalization
- USD fallback diagnostics
- Forward OPEN/PROBE evidence
- Forward WAIT shadow evidence
- Edge memory normalization
- Source health

StatisticalEdgeAuthorityV1 owns permission only:

- allow_open
- allow_probe
- score_adjustment
- size_multiplier
- authority_status

## Rules

One closed position equals one statistical observation.

OPEN requires sufficient R-normalized closed-position evidence.

Forward OPEN/PROBE is primary auxiliary evidence.

Forward WAIT is shadow evidence. It can support only tiny PROBE research and can never directly promote to OPEN.

Negative closed-position expectancy blocks promotion.

CAPITULATION_REBOUND_LONG remains blocked when closed-position evidence is negative.

No direct DB evidence queries are allowed inside StatisticalEdgeAuthorityV1.
