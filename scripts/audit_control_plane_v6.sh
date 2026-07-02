#!/data/data/com.termux/files/usr/bin/bash
set -e

cd "$(dirname "$0")/.."
export PYTHONPATH=$PWD
FAIL=0

echo "===== CONTROL PLANE V6 AUDIT ====="

echo "===== NO RUNTIME ====="
if ps -ef | grep -Ei "python.*joanbot.runner|joanbot_overnight_supervisor|joanbot.ui.dashboard|telegram_command_bot" | grep -v grep; then
  echo "RUNTIME_ACTIVE_ABORT"
  exit 1
else
  echo "NO_RUNTIME_OK"
fi

echo "===== REQUIRED INPUTS ====="
test -f joanbot/control/control_plane_v6.py || { echo CONTROL_PLANE_FILE_MISSING; exit 1; }
test -f joanbot/alpha/alpha_promotion_contract_v5.py || { echo PROMOTION_CONTRACT_FILE_MISSING; exit 1; }

CONTRACTS=$(sqlite3 data/joanbot_v14.sqlite "SELECT COUNT(*) FROM latest_alpha_promotion_contract_v5;" 2>/dev/null || echo 0)
echo "latest_contracts=$CONTRACTS"
if [ "$CONTRACTS" -le 0 ]; then
  echo "NO_CONTRACT_INPUT_ABORT"
  exit 1
fi

echo "===== PROTECTED COUNTS BEFORE ====="
DEC_BEFORE=$(sqlite3 data/joanbot_v14.sqlite "SELECT COUNT(*) FROM decisions;" 2>/dev/null || echo 0)
POS_BEFORE=$(sqlite3 data/joanbot_v14.sqlite "SELECT COUNT(*) FROM positions;" 2>/dev/null || echo 0)
TR_BEFORE=$(sqlite3 data/joanbot_v14.sqlite "SELECT COUNT(*) FROM trades;" 2>/dev/null || echo 0)
FC_BEFORE=$(sqlite3 data/joanbot_v14.sqlite "SELECT COUNT(*) FROM forward_cases;" 2>/dev/null || echo 0)
FR_BEFORE=$(sqlite3 data/joanbot_v14.sqlite "SELECT COUNT(*) FROM forward_results;" 2>/dev/null || echo 0)

echo "decisions=$DEC_BEFORE positions=$POS_BEFORE trades=$TR_BEFORE forward_cases=$FC_BEFORE forward_results=$FR_BEFORE"

echo "===== COMPILE ====="
python -m py_compile \
  joanbot/control/control_plane_v6.py \
  joanbot/alpha/alpha_promotion_contract_v5.py \
  joanbot/runner.py \
  || FAIL=1

echo "===== SOURCE SAFETY: NO PROTECTED MUTATION ====="
python - <<'PY' || FAIL=1
from pathlib import Path

s = Path("joanbot/control/control_plane_v6.py").read_text()

forbidden = [
    "INSERT INTO decisions", "UPDATE decisions", "DELETE FROM decisions",
    "INSERT INTO positions", "UPDATE positions", "DELETE FROM positions",
    "INSERT INTO trades", "UPDATE trades", "DELETE FROM trades",
    "INSERT INTO forward_cases", "UPDATE forward_cases", "DELETE FROM forward_cases",
    "INSERT INTO forward_results", "UPDATE forward_results", "DELETE FROM forward_results",
]

hits = [x for x in forbidden if x in s]
if hits:
    print("FAIL_FORBIDDEN_MUTATION", hits)
    raise SystemExit(1)

print("NO_FORBIDDEN_MUTATION_OK")
PY

echo "===== REFRESH CONTROL PLANE ====="
python -m joanbot.control.control_plane_v6 --refresh --latest || FAIL=1

echo "===== DB INTEGRITY ====="
sqlite3 data/joanbot_v14.sqlite "PRAGMA integrity_check;" || FAIL=1

echo "===== LATEST CONTROL PLANE ====="
sqlite3 -header -column data/joanbot_v14.sqlite "
SELECT
  global_state,
  control_score,
  recommended_action,
  next_required_build,
  allow_standard_open,
  allow_direct_open,
  allow_paper_micro_canary,
  force_learning_only,
  veto_new_positions,
  required_execution_mode,
  ROUND(max_size_usd,2) AS max_size,
  max_daily_new_positions,
  contracts_n,
  micro_canary_ready,
  ROUND(max_meta_score,2) AS max_meta,
  ROUND(max_posterior_score,2) AS max_post,
  ROUND(max_posterior_lcb_r,4) AS max_lcb,
  ROUND(max_tensor_quality,2) AS tensor_q,
  ROUND(shadow_100_avg_r,4) AS sh100,
  ROUND(shadow_300_avg_r,4) AS sh300,
  best_family_symbol,
  best_family_side,
  best_family_name,
  best_family_horizon_min,
  best_family_n,
  ROUND(best_family_avg_r,4) AS family_avg,
  ROUND(best_family_winrate,2) AS family_wr,
  hard_vetoes,
  reasons
FROM latest_institutional_control_plane_v6;
" || FAIL=1

echo "===== SAFETY: NO DIRECT/STANDARD OPEN ====="
BAD=$(sqlite3 data/joanbot_v14.sqlite "
SELECT COUNT(*)
FROM latest_institutional_control_plane_v6
WHERE allow_direct_open != 0
   OR allow_standard_open != 0;
")
if [ "$BAD" -ne 0 ]; then
  echo "FAIL_CONTROL_PLANE_ALLOWS_DIRECT_OR_STANDARD_OPEN=$BAD"
  FAIL=1
else
  echo "NO_DIRECT_OR_STANDARD_OPEN_OK"
fi

echo "===== SAFETY: MICRO ONLY IF CONTRACT READY ====="
BAD_MICRO=$(sqlite3 data/joanbot_v14.sqlite "
SELECT COUNT(*)
FROM latest_institutional_control_plane_v6
WHERE allow_paper_micro_canary=1
  AND (
    micro_canary_ready <= 0
    OR max_meta_score < 78
    OR max_posterior_score < 75
    OR max_posterior_lcb_r <= 0
    OR max_prob_edge_gt_zero < 0.75
    OR max_prob_edge_gt_min < 0.65
    OR max_tensor_quality < 60
    OR max_size_usd > 50
    OR max_daily_new_positions > 1
  );
")
if [ "$BAD_MICRO" -ne 0 ]; then
  echo "FAIL_PREMATURE_MICRO_CONTROL=$BAD_MICRO"
  FAIL=1
else
  echo "NO_PREMATURE_MICRO_OK"
fi

echo "===== PROTECTED COUNTS AFTER ====="
DEC_AFTER=$(sqlite3 data/joanbot_v14.sqlite "SELECT COUNT(*) FROM decisions;" 2>/dev/null || echo 0)
POS_AFTER=$(sqlite3 data/joanbot_v14.sqlite "SELECT COUNT(*) FROM positions;" 2>/dev/null || echo 0)
TR_AFTER=$(sqlite3 data/joanbot_v14.sqlite "SELECT COUNT(*) FROM trades;" 2>/dev/null || echo 0)
FC_AFTER=$(sqlite3 data/joanbot_v14.sqlite "SELECT COUNT(*) FROM forward_cases;" 2>/dev/null || echo 0)
FR_AFTER=$(sqlite3 data/joanbot_v14.sqlite "SELECT COUNT(*) FROM forward_results;" 2>/dev/null || echo 0)

[ "$DEC_BEFORE" = "$DEC_AFTER" ] || { echo FAIL_DECISIONS_CHANGED; FAIL=1; }
[ "$POS_BEFORE" = "$POS_AFTER" ] || { echo FAIL_POSITIONS_CHANGED; FAIL=1; }
[ "$TR_BEFORE" = "$TR_AFTER" ] || { echo FAIL_TRADES_CHANGED; FAIL=1; }
[ "$FC_BEFORE" = "$FC_AFTER" ] || { echo FAIL_FORWARD_CASES_CHANGED; FAIL=1; }
[ "$FR_BEFORE" = "$FR_AFTER" ] || { echo FAIL_FORWARD_RESULTS_CHANGED; FAIL=1; }

echo "PROTECTED_TABLES_UNCHANGED_OK"

echo "===== DECISION PATH CLEAN ====="
if grep -RInE "InstitutionalControlPlaneV6|latest_institutional_control_plane_v6|institutional_control_plane_v6" \
  joanbot/intelligence/decision.py \
  joanbot/intelligence/risk.py \
  joanbot/execution \
  --include="*.py"; then
  echo "FAIL_CONTROL_CONNECTED_TO_TRADING_PATH_TOO_EARLY"
  FAIL=1
else
  echo "TRADING_PATH_CLEAN_OK"
fi

echo "===== DB OBJECTS ====="
OBJ_N=$(sqlite3 data/joanbot_v14.sqlite "
SELECT COUNT(*)
FROM sqlite_master
WHERE name IN (
  'institutional_control_plane_v6',
  'latest_institutional_control_plane_v6',
  'institutional_control_plane_audit_v6'
);
")
echo "control_plane_objects=$OBJ_N"
if [ "$OBJ_N" -ne 3 ]; then
  echo "FAIL_CONTROL_PLANE_OBJECTS_MISSING"
  FAIL=1
fi

echo "===== STORAGE BOUNDS ====="
C=$(sqlite3 data/joanbot_v14.sqlite "SELECT COUNT(*) FROM institutional_control_plane_v6;" 2>/dev/null || echo 0)
A=$(sqlite3 data/joanbot_v14.sqlite "SELECT COUNT(*) FROM institutional_control_plane_audit_v6;" 2>/dev/null || echo 0)
echo "control=$C audit=$A"

if [ "$C" -gt 1200 ]; then echo "FAIL_CONTROL_BOUND=$C"; FAIL=1; fi
if [ "$A" -gt 300 ]; then echo "FAIL_CONTROL_AUDIT_BOUND=$A"; FAIL=1; fi

echo "===== GIT DIFF CHECK ====="
git diff --check || FAIL=1

echo "===== STATUS ====="
git status --short

if [ "$FAIL" -eq 0 ]; then
  echo "CONTROL_PLANE_V6_AUDIT_OK"
else
  echo "CONTROL_PLANE_V6_AUDIT_FAIL"
  exit 1
fi
