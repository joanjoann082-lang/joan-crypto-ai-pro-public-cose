# JoanBot V11.2 — Institutional Regime + Ablation

Aquest paquet no afegeix API de pagament. Millora el bot abans de pagar res.

## Canvis clau

1. **Derivatives Regime V10.2**
   - Separa flux direccional, crowding, carry/basis, liquidacions i book pressure.
   - Corregeix el punt feble de funding: Binance funding és decimal; `0.0001 = 1 bp`.
   - Afegeix `contradiction_index`, `confidence_score` i `regime_quality_score`.
   - Publica igualment a `latest_derivatives_regime_v10` perquè V11 continua compatible.

2. **Control Plane V11.2**
   - Full canary de 50 $ només amb `DERIVATIVES_CONFIRM_STRONG`, shadow support, robust edge i qualitat alta.
   - Confirmació normal obre només reduced/probe, no full.
   - Soft conflict ja no es tracta com neutre: espera.

3. **Ablation Engine V12**
   - Compara escenaris:
     - `A_EDGE_ONLY`
     - `B_EDGE_PLUS_SHADOW_REGIME`
     - `C_EDGE_PLUS_DERIVATIVES_REGIME`
     - `D_FULL_SINGLE_ORDER_V11`
   - Calcula PF, expectancy, drawdown i mostra.
   - No inventa counterfactuals: marca explícitament que és ablation observacional.

4. **Paid API Readiness Gate V11.2**
   - Ara exigeix que l'ablation engine hagi corregut.
   - No permet pagar API si el full single-order no mostra KPI defensable.

## Ordre real del runtime

```text
market/context/shadow
→ cluster
→ edge factory
→ robustness
→ shadow regime
→ derivatives spine
→ derivatives regime V10.2
→ feedback + KPI
→ overlap guard
→ decision order
→ control V11.2
→ paper canary bridge
→ feedback + KPI
→ ablation V12
→ paid API readiness
```

## Instal·lació

```bash
cd /storage/emulated/0/Download
cp -a joan_crypto_ai_pro_v14 joan_crypto_ai_pro_v14_BACKUP_PRE_V11_2_$(date +%Y%m%d_%H%M%S)
unzip -o joanbot_v14_INSTITUTIONAL_V11_2_REGIME_ABLATION_20260628.zip -d joan_crypto_ai_pro_v14
cd joan_crypto_ai_pro_v14
export PYTHONPATH=$PWD
bash scripts/audit_institutional_runtime_v11_2.sh
```

Si surt OK:

```bash
git add joanbot scripts INSTITUTIONAL_V11_2_REGIME_ABLATION_README.md V11_2_CHANGELOG_INSTITUTIONAL.txt
git commit -m "Add V11.2 derivatives regime and ablation gate"
git push
bash scripts/start_v11_clean.sh
```

## Veredicte sobre API

Encara no paguis CoinGlass fins que `latest_paid_api_readiness_gate_v11` indiqui:

```text
PAID_API_READY_FOR_1_MONTH_TEST
paid_api_allowed = 1
```
