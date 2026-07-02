#!/data/data/com.termux/files/usr/bin/bash
set -e

cd "$(dirname "$0")/.."
export PYTHONPATH=$PWD

FAIL=0

echo "===== BAYESIAN EVIDENCE PROMOTION V1 AUDIT ====="

echo "===== NO RUNTIME ====="
if ps -ef | grep -Ei "joanbot.runner|joanbot.ui.dashboard|telegram_command_bot" | grep -v grep; then
  echo "RUNTIME_ACTIVE_ABORT"
  exit 1
else
  echo "NO_RUNTIME_OK"
fi

echo "===== COMPILE ====="
python -m py_compile \
  joanbot/intelligence/evidence_registry_v1.py \
  joanbot/intelligence/bayesian_evidence_v1.py \
  joanbot/intelligence/evidence_engine_v1.py \
  joanbot/intelligence/statistical_edge_authority_v1.py \
  joanbot/intelligence/decision.py \
  joanbot/intelligence/risk.py \
  joanbot/execution/contract.py \
  joanbot/execution/broker.py \
  joanbot/runner.py \
  || FAIL=1

echo "===== REFRESH REGISTRY FIRST ====="
python -m joanbot.intelligence.evidence_registry_v1 --refresh --summary || FAIL=1

echo "===== REFRESH BAYESIAN EVIDENCE ====="
python -m joanbot.intelligence.bayesian_evidence_v1 --refresh --latest || FAIL=1

echo "===== DB INTEGRITY ====="
sqlite3 -header -column data/joanbot_v14.sqlite "PRAGMA integrity_check;" || FAIL=1

echo "===== BAYESIAN LATEST ====="
sqlite3 -header -column data/joanbot_v14.sqlite "
SELECT
  symbol,
  side,
  setup,
  forward_n,
  ROUND(forward_exp_r,4) AS fw_exp,
  ROUND(forward_pf,3) AS fw_pf,
  clean_exec_n,
  excluded_exec_n,
  ROUND(effective_n,2) AS eff_n,
  ROUND(shrunk_exp_r,4) AS shrunk_exp,
  ROUND(divergence_penalty,3) AS div_pen,
  ROUND(quality_score,2) AS quality,
  status,
  allow_probe,
  allow_open,
  ROUND(size_multiplier_cap,3) AS size_cap,
  reasons
FROM latest_bayesian_evidence_v1
ORDER BY quality_score DESC, shrunk_exp_r DESC;
" || FAIL=1

echo "===== SAFETY: NO OPEN WITHOUT CLEAN EXECUTION ====="
BAD_OPEN=$(sqlite3 data/joanbot_v14.sqlite "
SELECT COUNT(*)
FROM latest_bayesian_evidence_v1
WHERE allow_open=1
  AND clean_exec_n < 20;
")
if [ "$BAD_OPEN" != "0" ]; then
  echo "FAIL_OPEN_ALLOWED_WITHOUT_CLEAN_EXECUTION=$BAD_OPEN"
  FAIL=1
else
  echo "NO_OPEN_WITHOUT_CLEAN_EXECUTION_OK"
fi

echo "===== SAFETY: FORWARD ONLY DOES NOT OPEN ====="
BAD_FORWARD_ONLY=$(sqlite3 data/joanbot_v14.sqlite "
SELECT COUNT(*)
FROM latest_bayesian_evidence_v1
WHERE allow_open=1
  AND clean_exec_n=0
  AND forward_n>=100;
")
if [ "$BAD_FORWARD_ONLY" != "0" ]; then
  echo "FAIL_FORWARD_ONLY_OPEN=$BAD_FORWARD_ONLY"
  FAIL=1
else
  echo "FORWARD_ONLY_NO_DIRECT_OPEN_OK"
fi

echo "===== BOUNDED STORAGE ====="
ROWS=$(sqlite3 data/joanbot_v14.sqlite "SELECT COUNT(*) FROM bayesian_evidence_scores_v1;")
if [ "$ROWS" -gt 500 ]; then
  echo "FAIL_BAYESIAN_EVIDENCE_OVER_LIMIT=$ROWS"
  FAIL=1
else
  echo "BAYESIAN_EVIDENCE_STORAGE_BOUND_OK rows=$ROWS"
fi

echo "===== DECISION PATH UNCHANGED ====="
if grep -RInE "bayesian_evidence_v1|latest_bayesian_evidence_v1|bayesian_evidence_scores_v1" \
  joanbot/runner.py joanbot/intelligence/decision.py joanbot/intelligence/risk.py joanbot/execution --include="*.py"; then
  echo "FAIL_BAYESIAN_EVIDENCE_ALREADY_IN_DECISION_PATH"
  FAIL=1
else
  echo "DECISION_PATH_UNCHANGED_OK"
fi

echo "===== RAW OUTCOME ACCESS CHECK IN EVIDENCE ENGINE ====="
if grep -nEi "FROM[[:space:]]+positions|JOIN[[:space:]]+positions|FROM[[:space:]]+trades|JOIN[[:space:]]+trades" joanbot/intelligence/evidence_engine_v1.py; then
  echo "FAIL_RAW_OUTCOME_ACCESS_IN_EVIDENCE_ENGINE"
  FAIL=1
else
  echo "EVIDENCE_ENGINE_NO_RAW_OUTCOME_ACCESS_OK"
fi

echo "===== SIZE CHECK ====="
ls -lh data/joanbot_v14.sqlite*
df -h /storage/emulated

echo "===== GIT DIFF CHECK ====="
git diff --check || FAIL=1

echo "===== STATUS ====="
git status --short

if [ "$FAIL" -eq 0 ]; then
  echo "BAYESIAN_EVIDENCE_PROMOTION_V1_AUDIT_OK"
else
  echo "BAYESIAN_EVIDENCE_PROMOTION_V1_AUDIT_FAIL"
  exit 1
fi
