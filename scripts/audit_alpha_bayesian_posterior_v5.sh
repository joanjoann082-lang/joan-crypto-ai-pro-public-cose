#!/data/data/com.termux/files/usr/bin/bash
set -e

cd "$(dirname "$0")/.."
export PYTHONPATH=$PWD
FAIL=0

echo "===== BAYESIAN ALPHA POSTERIOR V5 AUDIT ====="

echo "===== NO RUNTIME ====="
if ps -ef | grep -Ei "python.*joanbot.runner|joanbot_overnight_supervisor|joanbot.ui.dashboard|telegram_command_bot" | grep -v grep; then
  echo "RUNTIME_ACTIVE_ABORT"
  exit 1
else
  echo "NO_RUNTIME_OK"
fi

echo "===== REQUIRED INPUT ====="
test -f joanbot/alpha/alpha_evidence_tensor_v5.py || { echo TENSOR_FILE_MISSING; exit 1; }

TENSOR_N=$(sqlite3 data/joanbot_v14.sqlite "SELECT COUNT(*) FROM latest_alpha_evidence_tensor_v5;" 2>/dev/null || echo 0)
echo "latest_tensor_rows=$TENSOR_N"

if [ "$TENSOR_N" -le 0 ]; then
  echo "NO_TENSOR_INPUT_ABORT"
  exit 1
fi

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
  joanbot/alpha/alpha_bayesian_posterior_v5.py \
  || FAIL=1

echo "===== SAFETY: NOT CONNECTED TO TRADING PATH ====="
if grep -RInE "AlphaBayesianPosteriorV5|alpha_bayesian_posterior_v5|latest_alpha_bayesian_posterior_v5" \
  joanbot/runner.py \
  joanbot/intelligence/decision.py \
  joanbot/intelligence/risk.py \
  joanbot/execution \
  --include="*.py"; then
  echo "FAIL_POSTERIOR_CONNECTED_TOO_EARLY"
  FAIL=1
else
  echo "TRADING_PATH_UNCHANGED_OK"
fi

echo "===== SAFETY: NO PROTECTED MUTATION ====="
python - <<'PY' || FAIL=1
from pathlib import Path

s = Path("joanbot/alpha/alpha_bayesian_posterior_v5.py").read_text()

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

echo "===== REFRESH POSTERIOR ====="
python -m joanbot.alpha.alpha_bayesian_posterior_v5 --refresh --latest || FAIL=1

echo "===== DB INTEGRITY ====="
sqlite3 data/joanbot_v14.sqlite "PRAGMA integrity_check;" || FAIL=1

echo "===== POSTERIOR SUMMARY ====="
sqlite3 -header -column data/joanbot_v14.sqlite "
SELECT
  posterior_state,
  COUNT(*) AS n,
  ROUND(AVG(posterior_score),2) AS avg_score,
  ROUND(MAX(posterior_score),2) AS max_score,
  ROUND(MAX(posterior_mean_r),4) AS max_mean,
  ROUND(MAX(posterior_lcb_r),4) AS max_lcb,
  SUM(allowed_meta_governance) AS meta_ready
FROM latest_alpha_bayesian_posterior_v5
GROUP BY posterior_state
ORDER BY max_score DESC, n DESC;
" || FAIL=1

echo "===== TOP POSTERIOR ====="
sqlite3 -header -column data/joanbot_v14.sqlite "
SELECT
  symbol,
  side,
  setup,
  profile,
  horizon_min,
  n,
  ROUND(effective_n,2) AS eff_n,
  ROUND(tensor_shrunk_r,4) AS tensor_shrunk,
  ROUND(tensor_lcb_r,4) AS tensor_lcb,
  ROUND(tensor_validation_r,4) AS val_r,
  ROUND(posterior_mean_r,4) AS post_mean,
  ROUND(posterior_lcb_r,4) AS post_lcb,
  ROUND(prob_edge_gt_zero,3) AS p_gt_0,
  ROUND(prob_edge_gt_min,3) AS p_gt_min,
  ROUND(prob_loss_gt_025r,3) AS p_loss_025,
  ROUND(prob_tail_event,3) AS p_tail,
  ROUND(tensor_quality,2) AS tensor_q,
  ROUND(posterior_score,2) AS post_score,
  posterior_state,
  allowed_meta_governance
FROM latest_alpha_bayesian_posterior_v5
ORDER BY allowed_meta_governance DESC, posterior_score DESC, posterior_mean_r DESC, n DESC
LIMIT 60;
" || FAIL=1

echo "===== NO DIRECT OPEN ====="
BAD_OPEN=$(sqlite3 data/joanbot_v14.sqlite "
SELECT COUNT(*)
FROM latest_alpha_bayesian_posterior_v5
WHERE allowed_direct_open != 0;
")
if [ "$BAD_OPEN" -ne 0 ]; then
  echo "FAIL_DIRECT_OPEN_ALLOWED=$BAD_OPEN"
  FAIL=1
else
  echo "NO_DIRECT_OPEN_OK"
fi

echo "===== NO PREMATURE META GOVERNANCE ====="
BAD_META=$(sqlite3 data/joanbot_v14.sqlite "
SELECT COUNT(*)
FROM latest_alpha_bayesian_posterior_v5
WHERE allowed_meta_governance=1
  AND (
    n < 90
    OR posterior_mean_r < 0.035
    OR posterior_lcb_r <= 0
    OR prob_edge_gt_min < 0.65
    OR prob_edge_gt_zero < 0.75
    OR prob_loss_gt_025r > 0.25
    OR prob_tail_event > 0.35
    OR current_context_fit < 0.60
    OR tensor_quality < 60
    OR posterior_score < 75
    OR tensor_validation_r <= 0
  );
")
if [ "$BAD_META" -ne 0 ]; then
  echo "FAIL_PREMATURE_META=$BAD_META"
  FAIL=1
else
  echo "NO_PREMATURE_META_OK"
fi

echo "===== SMALL SAMPLE CAPS ====="
BAD_SMALL=$(sqlite3 data/joanbot_v14.sqlite "
SELECT COUNT(*)
FROM latest_alpha_bayesian_posterior_v5
WHERE
  (n < 10 AND posterior_score > 18.01)
  OR (n >= 10 AND n < 30 AND posterior_score > 32.01)
  OR (n >= 30 AND n < 60 AND posterior_score > 50.01)
  OR (n >= 60 AND n < 90 AND posterior_score > 68.01);
")
if [ "$BAD_SMALL" -ne 0 ]; then
  echo "FAIL_SMALL_SAMPLE_CAP=$BAD_SMALL"
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
P=$(sqlite3 data/joanbot_v14.sqlite "SELECT COUNT(*) FROM alpha_bayesian_posterior_v5;")
A=$(sqlite3 data/joanbot_v14.sqlite "SELECT COUNT(*) FROM alpha_bayesian_posterior_audit_v5;")
echo "posterior=$P audit=$A"

if [ "$P" -gt 1800 ]; then echo "FAIL_POSTERIOR_BOUND=$P"; FAIL=1; else echo "POSTERIOR_BOUND_OK"; fi
if [ "$A" -gt 300 ]; then echo "FAIL_AUDIT_BOUND=$A"; FAIL=1; else echo "AUDIT_BOUND_OK"; fi

echo "===== GIT DIFF CHECK ====="
git diff --check || FAIL=1

echo "===== STATUS ====="
git status --short

if [ "$FAIL" -eq 0 ]; then
  echo "ALPHA_BAYESIAN_POSTERIOR_V5_AUDIT_OK"
else
  echo "ALPHA_BAYESIAN_POSTERIOR_V5_AUDIT_FAIL"
  exit 1
fi
