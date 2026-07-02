# JoanBot V11 — Institutional Single-Order Decision Kernel

Aquest paquet no elimina V9/V10. Afegeix una capa V11 que ordena tot el procés perquè no hi hagi solapaments ni decisions per camins paral·lels.

## Objectiu

Convertir el flux en una cadena única:

```text
SYSTEM_SAFETY
→ MARKET_ADAPTER
→ ALPHA_SHADOW
→ EDGE_FACTORY
→ ROBUSTNESS_VALIDATOR
→ SHADOW_REGIME
→ DERIVATIVES_DATA
→ DERIVATIVES_REGIME
→ FEEDBACK_KPI
→ OVERLAP_GUARD
→ DECISION_ORDER
→ CONTROL_PLANE
→ EXECUTION_BRIDGE
→ POST_TRADE_FEEDBACK
→ PAID_API_READINESS
```

## Què afegeix

- `joanbot/institutional/decision_order_v11.py`  
  Registre canònic de l'ordre dels estímuls. Guarda `flow_hash` i stages.

- `joanbot/control/overlap_guard_v11.py`  
  Bloqueja V11 si hi ha posicions legacy o canaries V9/V10 obertes.

- `joanbot/control/control_plane_v11.py`  
  Única autoritat final. Decideix `BLOCK / WAIT / PROBE / REDUCED / FULL`.

- `joanbot/execution/paper_micro_canary_bridge_v11.py`  
  Només llegeix el contracte V11 i només escriu `paper_micro_canary_positions_v11`.

- `joanbot/execution/micro_canary_outcome_feedback_v11.py`  
  Feedback exclusiu de canaries V11.

- `joanbot/analytics/micro_canary_kpi_v11.py`  
  KPIs nets de V11 amb fees/slippage.

- `joanbot/control/api_readiness_gate_v11.py`  
  Gate objectiu per pagar API externa. No activa CoinGlass ni cap pagament.

- `joanbot/runtime/institutional_runtime_v11.py`  
  Runtime únic amb l'ordre institucional complet.

## Regles de seguretat

- No modifica `decisions`, `positions` ni `trades`.
- No permet `allow_standard_open`.
- No permet `allow_direct_open`.
- No obre canary si hi ha V9/V10 canary activa.
- No obre canary si hi ha posició legacy oberta.
- No paga ni consulta API externa.
- El runner queda degradat a adapter de dades/context/shadow.

## Instal·lació

```bash
cd /storage/emulated/0/Download
cp -a joan_crypto_ai_pro_v14 joan_crypto_ai_pro_v14_BACKUP_PRE_V11_$(date +%Y%m%d_%H%M%S)
unzip -o joanbot_v14_INSTITUTIONAL_V11_SINGLE_ORDER_20260628.zip -d joan_crypto_ai_pro_v14
cd joan_crypto_ai_pro_v14
export PYTHONPATH=$PWD
bash scripts/audit_institutional_runtime_v11.sh
```

Només si surt:

```text
V11_SINGLE_ORDER_INSTITUTIONAL_AUDIT_OK
```

commit:

```bash
git add joanbot scripts INSTITUTIONAL_V11_SINGLE_ORDER_README.md V11_CHANGELOG_INSTITUTIONAL.txt
git commit -m "Add V11 institutional single-order decision kernel"
git push
```

Arrencada:

```bash
bash scripts/start_v11_clean.sh
```

Estat:

```bash
bash scripts/status_v11.sh
```

## Criteri per API externa

No pagar CoinGlass/CSAPP/API fins que `latest_paid_api_readiness_gate_v11` indiqui:

```text
PAID_API_READY_FOR_1_MONTH_TEST
paid_api_allowed = 1
```

Aquesta decisió exigeix canaries tancades, PF, expectancy, drawdown, dades gratuïtes preparades i zero errors crítics recents.
