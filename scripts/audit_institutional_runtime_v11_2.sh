#!/data/data/com.termux/files/usr/bin/bash
set -e

cd "$(dirname "$0")/.."
export PYTHONPATH=$PWD
FAIL=0

echo "===== V11.2 INSTITUTIONAL SINGLE-ORDER + REGIME + ABLATION AUDIT ====="

REQ=(
  joanbot/alpha/derivatives_regime_v10.py
  joanbot/institutional/decision_order_v11.py
  joanbot/control/overlap_guard_v11.py
  joanbot/control/control_plane_v11.py
  joanbot/execution/micro_canary_outcome_feedback_v11.py
  joanbot/analytics/micro_canary_kpi_v11.py
  joanbot/analytics/ablation_engine_v12.py
  joanbot/execution/paper_micro_canary_bridge_v11.py
  joanbot/control/api_readiness_gate_v11.py
  joanbot/runtime/institutional_runtime_v11.py
)
for f in "${REQ[@]}"; do
  [ -f "$f" ] || { echo "MISSING_REQUIRED_FILE:$f"; exit 1; }
done

echo "===== NO RUNTIME ACTIVE ====="
if ps -ef | grep -Ei "python.*joanbot.runner|python.*joanbot.orchestrator|python.*joanbot.runtime" | grep -v grep; then
  echo "RUNTIME_ACTIVE_ABORT"
  exit 1
else
  echo "NO_RUNTIME_OK"
fi

echo "===== COMPILE ====="
python -m py_compile \
  joanbot/alpha/derivatives_regime_v10.py \
  joanbot/institutional/decision_order_v11.py \
  joanbot/control/overlap_guard_v11.py \
  joanbot/control/control_plane_v11.py \
  joanbot/execution/micro_canary_outcome_feedback_v11.py \
  joanbot/analytics/micro_canary_kpi_v11.py \
  joanbot/analytics/ablation_engine_v12.py \
  joanbot/execution/paper_micro_canary_bridge_v11.py \
  joanbot/control/api_readiness_gate_v11.py \
  joanbot/runtime/institutional_runtime_v11.py || FAIL=1

echo "===== LEGACY COUNTS BEFORE ====="
DEC_BEFORE=$(sqlite3 data/joanbot_v14.sqlite "SELECT COUNT(*) FROM decisions;" 2>/dev/null || echo 0)
POS_BEFORE=$(sqlite3 data/joanbot_v14.sqlite "SELECT COUNT(*) FROM positions;" 2>/dev/null || echo 0)
TR_BEFORE=$(sqlite3 data/joanbot_v14.sqlite "SELECT COUNT(*) FROM trades;" 2>/dev/null || echo 0)
echo "decisions=$DEC_BEFORE positions=$POS_BEFORE trades=$TR_BEFORE"

echo "===== RUN V11.2 AUDIT CYCLE ====="
python -m joanbot.runtime.institutional_runtime_v11 --audit-once > V11_2_AUDIT_RESULT.json || FAIL=1
cat V11_2_AUDIT_RESULT.json | sed -n '1,100p'

echo "===== LEGACY COUNTS AFTER ====="
DEC_AFTER=$(sqlite3 data/joanbot_v14.sqlite "SELECT COUNT(*) FROM decisions;" 2>/dev/null || echo 0)
POS_AFTER=$(sqlite3 data/joanbot_v14.sqlite "SELECT COUNT(*) FROM positions;" 2>/dev/null || echo 0)
TR_AFTER=$(sqlite3 data/joanbot_v14.sqlite "SELECT COUNT(*) FROM trades;" 2>/dev/null || echo 0)
echo "decisions=$DEC_AFTER positions=$POS_AFTER trades=$TR_AFTER"
[ "$DEC_BEFORE" = "$DEC_AFTER" ] || { echo "FAIL_DECISIONS_CHANGED"; FAIL=1; }
[ "$POS_BEFORE" = "$POS_AFTER" ] || { echo "FAIL_POSITIONS_CHANGED"; FAIL=1; }
[ "$TR_BEFORE" = "$TR_AFTER" ] || { echo "FAIL_TRADES_CHANGED"; FAIL=1; }

echo "===== DERIVATIVES REGIME V10.2 ====="
sqlite3 -header -column data/joanbot_v14.sqlite "
SELECT
  version,
  symbol,
  selected_side,
  data_state,
  ROUND(data_quality,2) AS data_q,
  derivatives_state,
  allow_v10_canary,
  reduce_size,
  veto_canary,
  ROUND(selected_score,2) AS selected,
  ROUND(opposite_score,2) AS opposite,
  ROUND(directional_delta,2) AS delta,
  ROUND(contradiction_index,2) AS contradiction,
  ROUND(confidence_score,2) AS confidence,
  ROUND(regime_quality_score,2) AS regime_q,
  hard_vetoes,
  reasons
FROM latest_derivatives_regime_v10;
" || FAIL=1

echo "===== V11 CONTROL ====="
sqlite3 -header -column data/joanbot_v14.sqlite "
SELECT
  version,
  global_state,
  decision_tier,
  ROUND(control_score,2) AS control_score,
  ROUND(confidence_score,2) AS confidence,
  recommended_action,
  allow_standard_open,
  allow_direct_open,
  allow_paper_micro_canary,
  max_size_usd,
  edge_symbol,
  edge_side,
  edge_setup,
  edge_profile,
  edge_n,
  ROUND(edge_lcb_r,4) AS edge_lcb,
  ROUND(edge_recent20_avg_r,4) AS r20,
  ROUND(edge_recent50_lcb_r,4) AS r50_lcb,
  ROUND(robustness_score,2) AS robust,
  shadow_regime_state,
  derivatives_state,
  derivatives_data_quality,
  feedback_state,
  kpi_state,
  overlap_state,
  hard_vetoes,
  reasons
FROM latest_institutional_control_plane_v11;
" || FAIL=1

echo "===== ORDER CONTRACT ====="
sqlite3 -header -column data/joanbot_v14.sqlite "
SELECT phase, flow_hash, selected_symbol, selected_side, selected_setup,
       ordered_stage_count, missing_stage_count, hard_vetoes
FROM latest_institutional_decision_order_v11;
" || FAIL=1

ORDER_COUNT=$(sqlite3 data/joanbot_v14.sqlite "SELECT ordered_stage_count FROM latest_institutional_decision_order_v11;" 2>/dev/null || echo 0)
[ "$ORDER_COUNT" -ge 12 ] || { echo "FAIL_ORDER_STAGE_COUNT_TOO_LOW=$ORDER_COUNT"; FAIL=1; }

BAD_OPEN=$(sqlite3 data/joanbot_v14.sqlite "
SELECT COUNT(*) FROM latest_institutional_control_plane_v11
WHERE allow_standard_open != 0 OR allow_direct_open != 0;
" 2>/dev/null || echo 1)
if [ "$BAD_OPEN" -ne 0 ]; then
  echo "FAIL_STANDARD_OR_DIRECT_OPEN_ALLOWED=$BAD_OPEN"
  FAIL=1
else
  echo "NO_STANDARD_OR_DIRECT_OPEN_OK"
fi

echo "===== ABLATION V12 ====="
sqlite3 -header -column data/joanbot_v14.sqlite "
SELECT scenario, eligible_control_n, opened_n, closed_n,
       ROUND(profit_factor,3) AS pf,
       ROUND(expectancy_r,4) AS exp_r,
       ROUND(max_drawdown_r,4) AS max_dd,
       ablation_state,
       hard_vetoes
FROM latest_institutional_ablation_v12
ORDER BY scenario_rank;
" || FAIL=1

echo "===== PAID API READINESS ====="
sqlite3 -header -column data/joanbot_v14.sqlite "
SELECT readiness_state, paid_api_allowed, closed_canaries,
       ROUND(profit_factor,3) AS pf, ROUND(expectancy_r,4) AS exp_r,
       ROUND(max_drawdown_r,4) AS max_dd,
       derivatives_ready_symbols, derivatives_total_symbols, critical_errors_24h,
       ablation_state, ablation_closed_n, ROUND(ablation_expectancy_r,4) AS abl_exp,
       ROUND(ablation_profit_factor,3) AS abl_pf,
       hard_vetoes, required_before_paid_api
FROM latest_paid_api_readiness_gate_v11;
" || FAIL=1

echo "===== DB INTEGRITY ====="
sqlite3 data/joanbot_v14.sqlite "PRAGMA integrity_check;" || FAIL=1

echo "===== GIT DIFF CHECK ====="
git diff --check || FAIL=1

echo "===== STATUS ====="
git status --short || true

if [ "$FAIL" -eq 0 ]; then
  echo "V11_2_REGIME_ABLATION_AUDIT_OK"
else
  echo "V11_2_REGIME_ABLATION_AUDIT_FAIL"
  exit 1
fi
