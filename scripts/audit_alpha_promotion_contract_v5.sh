#!/data/data/com.termux/files/usr/bin/bash
set -e

cd "$(dirname "$0")/.."
export PYTHONPATH=$PWD
FAIL=0

echo "===== ALPHA PROMOTION CONTRACT V5 AUDIT ====="

echo "===== NO RUNTIME ====="
if ps -ef | grep -Ei "python.*joanbot.runner|joanbot_overnight_supervisor|joanbot.ui.dashboard|telegram_command_bot" | grep -v grep; then
  echo "RUNTIME_ACTIVE_ABORT"
  exit 1
else
  echo "NO_RUNTIME_OK"
fi

echo "===== REQUIRED INPUT ====="
test -f joanbot/alpha/alpha_meta_governance_v5.py || { echo META_FILE_MISSING; exit 1; }
test -f joanbot/alpha/alpha_promotion_contract_v5.py || { echo CONTRACT_FILE_MISSING; exit 1; }

META_N=$(sqlite3 data/joanbot_v14.sqlite "SELECT COUNT(*) FROM latest_alpha_meta_governance_v5;" 2>/dev/null || echo 0)
echo "latest_meta_rows=$META_N"

if [ "$META_N" -le 0 ]; then
  echo "NO_META_INPUT_ABORT"
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
  joanbot/alpha/alpha_meta_governance_v5.py \
  joanbot/alpha/alpha_promotion_contract_v5.py \
  || FAIL=1

echo "===== SAFETY: NOT CONNECTED TO TRADING PATH ====="
if grep -RInE "AlphaPromotionContractV5|alpha_promotion_contract_v5|latest_alpha_promotion_contract_v5" \
  joanbot/intelligence/decision.py \
  joanbot/intelligence/risk.py \
  joanbot/execution \
  --include="*.py"; then
  echo "FAIL_CONTRACT_CONNECTED_TO_TRADING_PATH"
  FAIL=1
else
  echo "TRADING_PATH_CLEAN_OK"
fi

echo "===== SAFETY: NO PROTECTED MUTATION ====="
python - <<'PY' || FAIL=1
from pathlib import Path

s = Path("joanbot/alpha/alpha_promotion_contract_v5.py").read_text()

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

echo "===== REFRESH CONTRACTS ====="
python -m joanbot.alpha.alpha_promotion_contract_v5 --refresh --latest || FAIL=1

echo "===== DB INTEGRITY ====="
sqlite3 data/joanbot_v14.sqlite "PRAGMA integrity_check;" || FAIL=1

echo "===== CONTRACT SUMMARY ====="
sqlite3 -header -column data/joanbot_v14.sqlite "
SELECT
  contract_state,
  COUNT(*) AS n,
  ROUND(MAX(meta_score),2) AS max_meta,
  ROUND(MAX(posterior_score),2) AS max_post,
  SUM(allowed_paper_micro_canary) AS paper_canary_ready
FROM latest_alpha_promotion_contract_v5
GROUP BY contract_state
ORDER BY max_meta DESC, n DESC;
" || FAIL=1

echo "===== TOP CONTRACTS ====="
sqlite3 -header -column data/joanbot_v14.sqlite "
SELECT
  symbol,
  side,
  setup,
  profile,
  horizon_min,
  n,
  ROUND(meta_score,2) AS meta,
  ROUND(posterior_score,2) AS post,
  ROUND(posterior_mean_r,4) AS post_mean,
  ROUND(posterior_lcb_r,4) AS post_lcb,
  ROUND(prob_edge_gt_zero,3) AS p_gt_0,
  ROUND(prob_edge_gt_min,3) AS p_gt_min,
  ROUND(prob_loss_gt_025r,3) AS p_loss,
  ROUND(prob_tail_event,3) AS p_tail,
  ROUND(tensor_quality,2) AS tensor_q,
  ROUND(current_context_fit,2) AS ctx_fit,
  cluster_rank,
  contract_state,
  allowed_paper_micro_canary,
  allowed_direct_open,
  required_execution_mode,
  ROUND(size_cap_usd,2) AS cap,
  expires_at
FROM latest_alpha_promotion_contract_v5
ORDER BY allowed_paper_micro_canary DESC, meta_score DESC, posterior_score DESC, posterior_mean_r DESC, n DESC
LIMIT 60;
" || FAIL=1

echo "===== NO DIRECT OPEN ====="
BAD_OPEN=$(sqlite3 data/joanbot_v14.sqlite "
SELECT COUNT(*)
FROM latest_alpha_promotion_contract_v5
WHERE allowed_direct_open != 0
   OR required_execution_mode NOT IN ('NONE','PAPER_MICRO_CANARY');
")
if [ "$BAD_OPEN" -ne 0 ]; then
  echo "FAIL_DIRECT_OPEN_OR_BAD_MODE=$BAD_OPEN"
  FAIL=1
else
  echo "NO_DIRECT_OPEN_OK"
fi

echo "===== NO PREMATURE PAPER MICRO CANARY ====="
BAD_CONTRACT=$(sqlite3 data/joanbot_v14.sqlite "
SELECT COUNT(*)
FROM latest_alpha_promotion_contract_v5
WHERE allowed_paper_micro_canary=1
  AND (
    n < 90
    OR meta_score < 78
    OR posterior_score < 75
    OR posterior_mean_r < 0.035
    OR posterior_lcb_r <= 0
    OR prob_edge_gt_zero < 0.75
    OR prob_edge_gt_min < 0.65
    OR prob_loss_gt_025r > 0.25
    OR prob_tail_event > 0.35
    OR current_context_fit < 0.60
    OR tensor_quality < 60
    OR tensor_validation_r <= 0
    OR is_cluster_leader != 1
    OR allowed_direct_open != 0
    OR size_cap_usd > 100
    OR max_daily_per_alpha > 1
    OR max_daily_global > 2
  );
")
if [ "$BAD_CONTRACT" -ne 0 ]; then
  echo "FAIL_PREMATURE_CONTRACT=$BAD_CONTRACT"
  FAIL=1
else
  echo "NO_PREMATURE_CONTRACT_OK"
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
C=$(sqlite3 data/joanbot_v14.sqlite "SELECT COUNT(*) FROM alpha_promotion_contract_v5;")
A=$(sqlite3 data/joanbot_v14.sqlite "SELECT COUNT(*) FROM alpha_promotion_contract_audit_v5;")
echo "contracts=$C audit=$A"

if [ "$C" -gt 1800 ]; then echo "FAIL_CONTRACT_BOUND=$C"; FAIL=1; else echo "CONTRACT_BOUND_OK"; fi
if [ "$A" -gt 300 ]; then echo "FAIL_AUDIT_BOUND=$A"; FAIL=1; else echo "AUDIT_BOUND_OK"; fi

echo "===== GIT DIFF CHECK ====="
git diff --check || FAIL=1

echo "===== STATUS ====="
git status --short

if [ "$FAIL" -eq 0 ]; then
  echo "ALPHA_PROMOTION_CONTRACT_V5_AUDIT_OK"
else
  echo "ALPHA_PROMOTION_CONTRACT_V5_AUDIT_FAIL"
  exit 1
fi
