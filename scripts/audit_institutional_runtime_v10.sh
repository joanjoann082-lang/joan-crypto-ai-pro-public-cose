#!/data/data/com.termux/files/usr/bin/bash
set -e

cd "$(dirname "$0")/.."
export PYTHONPATH=$PWD
FAIL=0

REQ_FILES=(
  joanbot/runtime/institutional_runtime_v10.py
  joanbot/market/derivatives_data_spine_v10.py
  joanbot/alpha/derivatives_regime_v10.py
  joanbot/control/control_plane_v10.py
  joanbot/execution/paper_micro_canary_bridge_v10.py
  joanbot/execution/micro_canary_outcome_feedback_v10.py
  joanbot/analytics/micro_canary_kpi_v10.py
  joanbot/control/api_readiness_gate_v10.py
)

echo "===== V10 PRE-API INSTITUTIONAL AUDIT ====="

echo "===== REQUIRED FILES ====="
for f in "${REQ_FILES[@]}"; do
  if [ ! -f "$f" ]; then
    echo "MISSING_REQUIRED_FILE: $f"
    FAIL=1
  else
    echo "OK $f"
  fi
done
[ "$FAIL" -eq 0 ] || exit 1

echo "===== NO RUNTIME ACTIVE ====="
if ps -ef | grep -Ei "python.*joanbot.runner|python.*joanbot.orchestrator|python.*joanbot.runtime" | grep -v grep; then
  echo "RUNTIME_ACTIVE_ABORT"
  exit 1
else
  echo "NO_RUNTIME_OK"
fi

echo "===== COMPILE ====="
python -m py_compile \
  joanbot/runtime/institutional_runtime_v10.py \
  joanbot/market/derivatives_data_spine_v10.py \
  joanbot/alpha/derivatives_regime_v10.py \
  joanbot/control/control_plane_v10.py \
  joanbot/execution/paper_micro_canary_bridge_v10.py \
  joanbot/execution/micro_canary_outcome_feedback_v10.py \
  joanbot/analytics/micro_canary_kpi_v10.py \
  joanbot/control/api_readiness_gate_v10.py || FAIL=1

echo "===== LEGACY COUNTS BEFORE ====="
DEC_BEFORE=$(sqlite3 data/joanbot_v14.sqlite "SELECT COUNT(*) FROM decisions;" 2>/dev/null || echo 0)
POS_BEFORE=$(sqlite3 data/joanbot_v14.sqlite "SELECT COUNT(*) FROM positions;" 2>/dev/null || echo 0)
TR_BEFORE=$(sqlite3 data/joanbot_v14.sqlite "SELECT COUNT(*) FROM trades;" 2>/dev/null || echo 0)
echo "decisions=$DEC_BEFORE positions=$POS_BEFORE trades=$TR_BEFORE"

echo "===== AUDIT-ONCE: NO CANARY OPEN ALLOWED ====="
python -m joanbot.runtime.institutional_runtime_v10 --audit-once >/tmp/joanbot_v10_audit_once.json || FAIL=1
cat /tmp/joanbot_v10_audit_once.json | tail -60 || true

echo "===== LEGACY COUNTS AFTER ====="
DEC_AFTER=$(sqlite3 data/joanbot_v14.sqlite "SELECT COUNT(*) FROM decisions;" 2>/dev/null || echo 0)
POS_AFTER=$(sqlite3 data/joanbot_v14.sqlite "SELECT COUNT(*) FROM positions;" 2>/dev/null || echo 0)
TR_AFTER=$(sqlite3 data/joanbot_v14.sqlite "SELECT COUNT(*) FROM trades;" 2>/dev/null || echo 0)
echo "decisions=$DEC_AFTER positions=$POS_AFTER trades=$TR_AFTER"

[ "$DEC_BEFORE" = "$DEC_AFTER" ] || { echo "FAIL_DECISIONS_CHANGED"; FAIL=1; }
[ "$POS_BEFORE" = "$POS_AFTER" ] || { echo "FAIL_POSITIONS_CHANGED"; FAIL=1; }
[ "$TR_BEFORE" = "$TR_AFTER" ] || { echo "FAIL_TRADES_CHANGED"; FAIL=1; }

echo "===== HARD SAFETY: NO STANDARD/DIRECT OPEN ====="
BAD_OPEN=$(sqlite3 data/joanbot_v14.sqlite "
SELECT COUNT(*)
FROM latest_institutional_control_plane_v10
WHERE allow_standard_open != 0
   OR allow_direct_open != 0;
" 2>/dev/null || echo 1)
if [ "$BAD_OPEN" -ne 0 ]; then
  echo "FAIL_STANDARD_OR_DIRECT_OPEN_ALLOWED=$BAD_OPEN"
  FAIL=1
else
  echo "NO_STANDARD_OR_DIRECT_OPEN_OK"
fi

echo "===== AUDIT MODE DID NOT OPEN V10 CANARY ====="
OPENED_AUDIT=$(python - <<'PY'
import json
p='/tmp/joanbot_v10_audit_once.json'
try:
    j=json.load(open(p))
    print(1 if j.get('canary',{}).get('opened',{}).get('opened') else 0)
except Exception:
    print(1)
PY
)
if [ "$OPENED_AUDIT" -ne 0 ]; then
  echo "FAIL_AUDIT_MODE_OPENED_CANARY"
  FAIL=1
else
  echo "AUDIT_MODE_NO_CANARY_OPEN_OK"
fi

echo "===== CONTROL V10 ====="
sqlite3 -header -column data/joanbot_v14.sqlite "
SELECT
  global_state,
  ROUND(control_score,2) AS control_score,
  recommended_action,
  allow_standard_open,
  allow_direct_open,
  allow_paper_micro_canary,
  max_size_usd,
  edge_symbol,
  edge_side,
  edge_family,
  edge_setup,
  edge_profile,
  edge_horizon_min,
  edge_n,
  ROUND(edge_avg_r,4) AS edge_avg,
  ROUND(edge_lcb_r,4) AS edge_lcb,
  ROUND(robustness_score,2) AS robust_score,
  validation_state,
  shadow_regime_state,
  derivatives_state,
  ROUND(derivatives_score,2) AS deriv_score,
  ROUND(derivatives_data_quality,2) AS deriv_q,
  feedback_state,
  kpi_state,
  hard_vetoes,
  reasons
FROM latest_institutional_control_plane_v10;
" || FAIL=1

echo "===== DERIVATIVES SPINE ====="
sqlite3 -header -column data/joanbot_v14.sqlite "
SELECT
  symbol,
  data_state,
  ROUND(data_quality,2) AS q,
  sample_n,
  ROUND(data_age_sec,1) AS age_s,
  ROUND(funding_rate,5) AS funding,
  ROUND(oi_change_5m,4) AS oi5,
  ROUND(oi_change_30m,4) AS oi30,
  ROUND(long_short_ratio,4) AS ls,
  ROUND(taker_buy_sell_ratio,4) AS taker,
  ROUND(cvd_ratio,4) AS cvd,
  hard_vetoes
FROM latest_derivatives_data_spine_v10
ORDER BY symbol;
" || FAIL=1

echo "===== KPI V10 ====="
sqlite3 -header -column data/joanbot_v14.sqlite "
SELECT
  total_n, open_n, closed_n, wins, losses,
  ROUND(winrate,2) AS wr,
  ROUND(profit_factor,3) AS pf,
  ROUND(expectancy_r,4) AS exp_r,
  ROUND(max_drawdown_r,4) AS max_dd,
  kpi_state,
  hard_vetoes
FROM latest_micro_canary_kpi_v10;
" || true

echo "===== PAID API READINESS ====="
sqlite3 -header -column data/joanbot_v14.sqlite "
SELECT
  readiness_state,
  paid_api_allowed,
  closed_canaries,
  ROUND(profit_factor,3) AS pf,
  ROUND(expectancy_r,4) AS exp_r,
  ROUND(max_drawdown_r,4) AS max_dd,
  derivatives_ready_symbols,
  derivatives_total_symbols,
  critical_errors_24h,
  hard_vetoes,
  required_before_paid_api
FROM latest_paid_api_readiness_gate_v10;
" || FAIL=1

echo "===== DB ====="
sqlite3 data/joanbot_v14.sqlite "PRAGMA integrity_check;" || FAIL=1

echo "===== GIT DIFF CHECK ====="
git diff --check || FAIL=1

echo "===== STATUS ====="
git status --short || true

if [ "$FAIL" -eq 0 ]; then
  echo "V10_PRE_API_INSTITUTIONAL_AUDIT_OK"
else
  echo "V10_PRE_API_INSTITUTIONAL_AUDIT_FAIL"
  exit 1
fi
