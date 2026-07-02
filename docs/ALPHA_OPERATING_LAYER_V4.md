# Institutional Alpha Operating Layer V4

## Purpose

Create a professional alpha lifecycle architecture for the bot.

This layer prevents loose, duplicated, cosmetic alpha modules. Every discovered alpha must pass through:

1. identity contract
2. evidence contract
3. governance verdict
4. lifecycle state
5. promotion contract
6. future paper micro-canary bridge
7. clean outcome feedback

## Architecture

Market / Context  
→ Universal Shadow Alpha Loop V2  
→ Alpha Feature Store  
→ Alpha Label Store  
→ Alpha Governance  
→ Alpha Lifecycle  
→ Alpha Promotion Policy  
→ Alpha Canary Contract  
→ Future Paper Micro-Canary Bridge  
→ Clean Execution Outcome  
→ Governance Feedback

## Safety

At this stage the layer is not connected to:

- decision.py
- risk.py
- execution
- broker
- positions
- trades

## Non-negotiable rules

- No direct OPEN from shadow evidence.
- No alpha acts without promotion contract.
- No duplicate alpha cluster acts twice.
- No small-sample alpha can trade.
- No decaying alpha can trade.
- No high tail-risk alpha can trade.
- No context-mismatched alpha can trade.
- All promotion must be auditable.
