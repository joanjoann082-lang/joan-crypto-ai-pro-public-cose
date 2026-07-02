#!/data/data/com.termux/files/usr/bin/bash
set -e

cd "$(dirname "$0")/.."
export PYTHONPATH=$PWD
FAIL=0

echo "===== V9.2 DECAY POLICY AUDIT ====="

echo "===== NO RUNTIME ====="
if ps -ef | grep -Ei "python.*joanbot.runner|python.*joanbot.orchestrator|python.*joanbot.runtime" | grep -v grep; then
  echo "RUNTIME_ACTIVE_ABORT"
  exit 1
else
  echo "NO_RUNTIME_OK"
fi

echo "===== COMPILE ====="
python -m py_compile \
  joanbot/alpha/institutional_edge_factory_v8.py \
  joanbot/alpha/edge_robustness_validator_v9.py \
  joanbot/execution/micro_canary_outcome_feedback_v9.py \
  joanbot/control/control_plane_v9.py \
  joanbot/execution/paper_micro_canary_bridge_v9.py \
  joanbot/runtime/institutional_runtime_v9.py \
  || FAIL=1

echo "===== LEGACY COUNTS BEFORE ====="
DEC_BEFORE=$(sqlite3 data/joanbot_v14.sqlite "SELECT COUNT(*) FROM decisions;" 2>/dev/null || echo 0)
POS_BEFORE=$(sqlite3 data/joanbot_v14.sqlite "SELECT COUNT(*) FROM positions;" 2>/dev/null || echo 0)
TR_BEFORE=$(sqlite3 data/joanbot_v14.sqlite "SELECT COUNT(*) FROM trades;" 2>/dev/null || echo 0)
echo "decisions=$DEC_BEFORE positions=$POS_BEFORE trades=$TR_BEFORE"

echo "===== RUN V9.2 AUDIT CYCLE ====="
python -m joanbot.runtime.institutional_runtime_v9 --audit-once || FAIL=1

echo "===== LEGACY COUNTS AFTER ====="
DEC_AFTER=$(sqlite3 data/joanbot_v14.sqlite "SELECT COUNT(*) FROM decisions;" 2>/dev/null || echo 0)
POS_AFTER=$(sqlite3 data/joanbot_v14.sqlite "SELECT COUNT(*) FROM positions;" 2>/dev/null || echo 0)
TR_AFTER=$(sqlite3 data/joanbot_v14.sqlite "SELECT COUNT(*) FROM trades;" 2>/dev/null || echo 0)
echo "decisions=$DEC_AFTER positions=$POS_AFTER trades=$TR_AFTER"

[ "$DEC_BEFORE" = "$DEC_AFTER" ] || { echo "FAIL_DECISIONS_CHANGED"; FAIL=1; }
[ "$POS_BEFORE" = "$POS_AFTER" ] || { echo "FAIL_POSITIONS_CHANGED"; FAIL=1; }
[ "$TR_BEFORE" = "$TR_AFTER" ] || { echo "FAIL_TRADES_CHANGED"; FAIL=1; }

echo "===== POSITIVE COOLING POLICY CHECK ====="
BAD_COOLING=$(sqlite3 data/joanbot_v14.sqlite "
SELECT COUNT(*)
FROM latest_edge_robustness_validator_v9
WHERE n >= 45
  AND avg_r >= 0.12
  AND lcb_r >= 0.05
  AND winrate >= 65
  AND worst_r > -0.75
  AND recent20_avg_r >= 0.03
  AND recent20_lcb_r >= 0
  AND recent50_lcb_r >= 0.05
  AND overfit_penalty = 0
  AND canary_permission != 1;
")
if [ "$BAD_COOLING" -ne 0 ]; then
  echo "FAIL_POSITIVE_COOLING_EDGE_NOT_PROMOTED=$BAD_COOLING"
  FAIL=1
else
  echo "POSITIVE_COOLING_POLICY_OK"
fi

echo "===== TOP ROBUSTNESS ====="
sqlite3 -header -column data/joanbot_v14.sqlite "
SELECT
  symbol,
  side,
  family_name,
  setup,
  profile,
  horizon_min,
  n,
  ROUND(avg_r,4) AS avg_r,
  ROUND(lcb_r,4) AS lcb_r,
  ROUND(recent20_avg_r,4) AS r20,
  ROUND(recent20_lcb_r,4) AS r20_lcb,
  ROUND(recent50_lcb_r,4) AS r50_lcb,
  ROUND(decay_guard,4) AS decay,
  ROUND(winrate,2) AS wr,
  ROUND(worst_r,4) AS worst,
  ROUND(robustness_score,2) AS score,
  validation_state,
  canary_permission,
  hard_vetoes
FROM latest_edge_robustness_validator_v9
ORDER BY canary_permission DESC, robustness_score DESC, lcb_r DESC
LIMIT 10;
"

echo "===== CONTROL V9.2 ====="
sqlite3 -header -column data/joanbot_v14.sqlite "
SELECT
  global_state,
  ROUND(control_score,2) AS control_score,
  recommended_action,
  next_required_build,
  allow_standard_open,
  allow_direct_open,
  allow_paper_micro_canary,
  force_learning_only,
  veto_new_positions,
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
  ROUND(edge_winrate,2) AS edge_wr,
  ROUND(robustness_score,2) AS robust_score,
  validation_state,
  regime_state,
  feedback_state,
  hard_vetoes,
  reasons
FROM latest_institutional_control_plane_v9;
"

echo "===== HARD SAFETY ====="
BAD_OPEN=$(sqlite3 data/joanbot_v14.sqlite "
SELECT COUNT(*)
FROM latest_institutional_control_plane_v9
WHERE allow_standard_open != 0
   OR allow_direct_open != 0;
")
if [ "$BAD_OPEN" -ne 0 ]; then
  echo "FAIL_STANDARD_OR_DIRECT_OPEN_ALLOWED=$BAD_OPEN"
  FAIL=1
else
  echo "NO_STANDARD_OR_DIRECT_OPEN_OK"
fi

echo "===== DB ====="
sqlite3 data/joanbot_v14.sqlite "PRAGMA integrity_check;" || FAIL=1

echo "===== GIT DIFF CHECK ====="
git diff --check || FAIL=1

echo "===== STATUS ====="
git status --short

if [ "$FAIL" -eq 0 ]; then
  echo "V9_2_DECAY_POLICY_AUDIT_OK"
else
  echo "V9_2_DECAY_POLICY_AUDIT_FAIL"
  exit 1
fi
