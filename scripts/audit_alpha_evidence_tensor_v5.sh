#!/data/data/com.termux/files/usr/bin/bash
set -e

cd "$(dirname "$0")/.."
export PYTHONPATH=$PWD
FAIL=0

echo "===== ALPHA EVIDENCE TENSOR V5 AUDIT ====="

echo "===== NO RUNTIME REQUIRED ====="
ps -ef | grep -Ei "python.*joanbot.runner|joanbot_overnight_supervisor|joanbot.ui.dashboard|telegram_command_bot" | grep -v grep && {
  echo "RUNTIME_ACTIVE_ABORT"
  exit 1
} || echo "NO_RUNTIME_OK"

echo "===== REQUIRED FILES ====="
test -f joanbot/alpha/contracts.py || { echo CONTRACTS_MISSING; exit 1; }
test -f joanbot/alpha/alpha_feature_store_v1.py || { echo FEATURE_STORE_MISSING; exit 1; }
test -f joanbot/alpha/alpha_label_store_v1.py || { echo LABEL_STORE_MISSING; exit 1; }
test -f joanbot/alpha/alpha_evidence_tensor_v5.py || { echo TENSOR_MISSING; exit 1; }

echo "===== PROTECTED TABLE COUNTS BEFORE ====="
FC_BEFORE=$(sqlite3 data/joanbot_v14.sqlite "SELECT COUNT(*) FROM forward_cases;")
FR_BEFORE=$(sqlite3 data/joanbot_v14.sqlite "SELECT COUNT(*) FROM forward_results;")
DEC_BEFORE=$(sqlite3 data/joanbot_v14.sqlite "SELECT COUNT(*) FROM decisions;")
POS_BEFORE=$(sqlite3 data/joanbot_v14.sqlite "SELECT COUNT(*) FROM positions;")
TR_BEFORE=$(sqlite3 data/joanbot_v14.sqlite "SELECT COUNT(*) FROM trades;")
echo "forward_cases=$FC_BEFORE forward_results=$FR_BEFORE decisions=$DEC_BEFORE positions=$POS_BEFORE trades=$TR_BEFORE"

echo "===== COMPILE ====="
python -m py_compile \
  joanbot/alpha/contracts.py \
  joanbot/alpha/alpha_feature_store_v1.py \
  joanbot/alpha/alpha_label_store_v1.py \
  joanbot/alpha/alpha_evidence_tensor_v5.py \
  || FAIL=1

echo "===== SAFETY: NOT CONNECTED TO TRADING PATH ====="
if grep -RInE "AlphaEvidenceTensorV5|alpha_evidence_tensor_v5|latest_alpha_evidence_tensor_v5" \
  joanbot/runner.py \
  joanbot/intelligence/decision.py \
  joanbot/intelligence/risk.py \
  joanbot/execution \
  --include="*.py"; then
  echo "FAIL_TENSOR_CONNECTED_TOO_EARLY"
  FAIL=1
else
  echo "TRADING_PATH_UNCHANGED_OK"
fi

echo "===== SAFETY: NO PROTECTED MUTATION ====="
python - <<'PY' || FAIL=1
from pathlib import Path

s = Path("joanbot/alpha/alpha_evidence_tensor_v5.py").read_text()

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

echo "===== REFRESH TENSOR ====="
python -m joanbot.alpha.alpha_evidence_tensor_v5 --refresh --latest || FAIL=1

echo "===== DB INTEGRITY ====="
sqlite3 data/joanbot_v14.sqlite "PRAGMA integrity_check;" || FAIL=1

echo "===== TENSOR SUMMARY ====="
sqlite3 -header -column data/joanbot_v14.sqlite "
SELECT
  COUNT(*) AS latest_alphas,
  ROUND(AVG(tensor_quality),2) AS avg_quality,
  ROUND(MAX(tensor_quality),2) AS max_quality,
  ROUND(MAX(shrunk_expectancy_r),4) AS max_shrunk,
  ROUND(MAX(lcb_expectancy_r),4) AS max_lcb
FROM latest_alpha_evidence_tensor_v5;
" || FAIL=1

echo "===== TOP TENSOR ====="
sqlite3 -header -column data/joanbot_v14.sqlite "
SELECT
  symbol,
  side,
  setup,
  profile,
  horizon_min,
  n,
  ROUND(expectancy_r,4) AS exp_r,
  ROUND(shrunk_expectancy_r,4) AS shrunk_r,
  ROUND(lcb_expectancy_r,4) AS lcb_r,
  ROUND(validation_exp_r,4) AS val_r,
  ROUND(profit_factor_capped,3) AS pf_cap,
  ROUND(avg_mae_r,4) AS mae_r,
  ROUND(mfe_mae_efficiency,3) AS eff,
  fold_positive_n,
  fold_pass,
  decay_state,
  tail_risk_state,
  ROUND(current_context_fit,2) AS ctx_fit,
  ROUND(tensor_quality,2) AS quality
FROM latest_alpha_evidence_tensor_v5
ORDER BY tensor_quality DESC, shrunk_expectancy_r DESC, n DESC
LIMIT 50;
" || FAIL=1

echo "===== QUALITY SANITY ====="
BAD=$(sqlite3 data/joanbot_v14.sqlite "
SELECT COUNT(*)
FROM latest_alpha_evidence_tensor_v5
WHERE n < 10 AND tensor_quality > 35;
")
if [ "$BAD" -ne 0 ]; then
  echo "FAIL_SMALL_SAMPLE_TOO_HIGH=$BAD"
  FAIL=1
else
  echo "SMALL_SAMPLE_CAP_OK"
fi

echo "===== PROTECTED TABLE COUNTS AFTER ====="
FC_AFTER=$(sqlite3 data/joanbot_v14.sqlite "SELECT COUNT(*) FROM forward_cases;")
FR_AFTER=$(sqlite3 data/joanbot_v14.sqlite "SELECT COUNT(*) FROM forward_results;")
DEC_AFTER=$(sqlite3 data/joanbot_v14.sqlite "SELECT COUNT(*) FROM decisions;")
POS_AFTER=$(sqlite3 data/joanbot_v14.sqlite "SELECT COUNT(*) FROM positions;")
TR_AFTER=$(sqlite3 data/joanbot_v14.sqlite "SELECT COUNT(*) FROM trades;")

[ "$FC_BEFORE" = "$FC_AFTER" ] || { echo FAIL_FORWARD_CASES_CHANGED; FAIL=1; }
[ "$FR_BEFORE" = "$FR_AFTER" ] || { echo FAIL_FORWARD_RESULTS_CHANGED; FAIL=1; }
[ "$DEC_BEFORE" = "$DEC_AFTER" ] || { echo FAIL_DECISIONS_CHANGED; FAIL=1; }
[ "$POS_BEFORE" = "$POS_AFTER" ] || { echo FAIL_POSITIONS_CHANGED; FAIL=1; }
[ "$TR_BEFORE" = "$TR_AFTER" ] || { echo FAIL_TRADES_CHANGED; FAIL=1; }

echo "PROTECTED_TABLES_UNCHANGED_OK"

echo "===== STORAGE BOUNDS ====="
T=$(sqlite3 data/joanbot_v14.sqlite "SELECT COUNT(*) FROM alpha_evidence_tensor_v5;")
A=$(sqlite3 data/joanbot_v14.sqlite "SELECT COUNT(*) FROM alpha_evidence_tensor_audit_v5;")
echo "tensor=$T audit=$A"

if [ "$T" -gt 2400 ]; then echo "FAIL_TENSOR_BOUND=$T"; FAIL=1; else echo "TENSOR_BOUND_OK"; fi
if [ "$A" -gt 300 ]; then echo "FAIL_AUDIT_BOUND=$A"; FAIL=1; else echo "AUDIT_BOUND_OK"; fi

echo "===== GIT DIFF CHECK ====="
git diff --check || FAIL=1

echo "===== STATUS ====="
git status --short

if [ "$FAIL" -eq 0 ]; then
  echo "ALPHA_EVIDENCE_TENSOR_V5_AUDIT_OK"
else
  echo "ALPHA_EVIDENCE_TENSOR_V5_AUDIT_FAIL"
  exit 1
fi
