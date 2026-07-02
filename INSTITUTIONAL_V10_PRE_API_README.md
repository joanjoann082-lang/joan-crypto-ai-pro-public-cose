# JoanBot Institutional V10 Pre-API

This package adds a professional pre-paid-API operating layer. It is designed to prevent paying for CoinGlass or any external API before the bot proves it can exploit free derivatives data and V10 micro-canary feedback.

## What V10 adds

- Free Binance derivatives data spine from existing snapshots
- Side-aware derivatives regime gate
- V10 control plane combining:
  - exact robust edge
  - shadow regime
  - derivatives regime
  - micro-canary feedback
  - KPI state
- V10 paper micro-canary bridge with fee/slippage-adjusted net R
- KPI engine: profit factor, expectancy, max drawdown
- Paid API readiness gate: objective YES/NO for CoinGlass/API spend
- Audit/start/status scripts

## Safety contract

V10 does not permit standard or direct opens. It can only open isolated paper micro-canary positions in `paper_micro_canary_positions_v10`.

Protected legacy tables:

- `decisions`
- `positions`
- `trades`

V10 audit checks that these counts do not change.

## Install / run on Termux

From project root:

```bash
cd /storage/emulated/0/Download/joan_crypto_ai_pro_v14
export PYTHONPATH=$PWD
bash scripts/audit_institutional_runtime_v10.sh
```

Only if audit prints:

```text
V10_PRE_API_INSTITUTIONAL_AUDIT_OK
```

start V10:

```bash
bash scripts/start_v10_clean.sh
```

Status:

```bash
bash scripts/status_v10.sh
```

## Paid API rule

Do not pay CoinGlass/API until `latest_paid_api_readiness_gate_v10` returns:

```text
PAID_API_READY_FOR_1_MONTH_TEST
paid_api_allowed = 1
```

By default it will say NOT READY until there are enough closed V10 micro-canaries and positive net KPIs.
