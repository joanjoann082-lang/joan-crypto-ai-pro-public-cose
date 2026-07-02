#!/data/data/com.termux/files/usr/bin/bash
set -e

cd "$(dirname "$0")/.."
export PYTHONPATH=$PWD

FAIL=0

echo "===== RESEARCH PROMOTION POLICY V1 AUDIT ====="

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
  joanbot/intelligence/research_promotion_policy_v1.py \
  joanbot/intelligence/evidence_engine_v1.py \
  joanbot/intelligence/statistical_edge_authority_v1.py \
  joanbot/intelligence/decision.py \
  joanbot/intelligence/risk.py \
  joanbot/execution/contract.py \
  joanbot/execution/broker.py \
  joanbot/runner.py \
  || FAIL=1

echo "===== REFRESH FOUNDATION ====="
python -m joanbot.intelligence.evidence_registry_v1 --refresh --summary || FAIL=1
python -m joanbot.intelligence.bayesian_evidence_v1 --refresh --latest || FAIL=1

echo "===== REFRESH RESEARCH PROMOTION ====="
python -m joanbot.intelligence.research_promotion_policy_v1 --refresh --latest || FAIL=1

echo "===== DB INTEGRITY ====="
sqlite3 -header -column data/joanbot_v14.sqlite "PRAGMA integrity_check;" || FAIL=1

echo "===== LATEST RESEARCH PROMOTION ====="
sqlite3 -header -column data/joanbot_v14.sqlite "
SELECT
  symbol,
  side,
  setup,
  source_status,
  forward_n,
  ROUND(forward_exp_r,4) AS fw_exp,
  ROUND(forward_pf,3) AS fw_pf,
  clean_exec_n,
  excluded_exec_n,
  ROUND(shrunk_exp_r,4) AS shrunk_exp,
  ROUND(divergence_penalty,3) AS div_pen,
  ROUND(quality_score,2) AS quality,
  allow_canary_probe,
  allow_direct_open,
  ROUND(size_multiplier_cap,4) AS size_cap,
  ROUND(absolute_size_usd_cap,2) AS usd_cap,
  promotion_state,
  reasons
FROM latest_research_promotion_v1
ORDER BY allow_canary_probe DESC, quality_score DESC;
" || FAIL=1

echo "===== SAFETY: NO DIRECT OPEN ====="
BAD_OPEN=$(sqlite3 data/joanbot_v14.sqlite "
SELECT COUNT(*)
FROM latest_research_promotion_v1
WHERE allow_direct_open != 0;
")
if [ "$BAD_OPEN" != "0" ]; then
  echo "FAIL_DIRECT_OPEN_ALLOWED=$BAD_OPEN"
  FAIL=1
else
  echo "NO_DIRECT_OPEN_OK"
fi

echo "===== SAFETY: CANARY SIZE CAPS ====="
BAD_SIZE=$(sqlite3 data/joanbot_v14.sqlite "
SELECT COUNT(*)
FROM latest_research_promotion_v1
WHERE allow_canary_probe=1
  AND (
    size_multiplier_cap > 0.025
    OR absolute_size_usd_cap > 250
  );
")
if [ "$BAD_SIZE" != "0" ]; then
  echo "FAIL_CANARY_SIZE_CAP=$BAD_SIZE"
  FAIL=1
else
  echo "CANARY_SIZE_CAP_OK"
fi

echo "===== SAFETY: DECISION PATH UNCHANGED ====="
if grep -RInE "research_promotion_policy_v1|latest_research_promotion_v1|research_promotion_decisions_v1" \
  joanbot/runner.py joanbot/intelligence/decision.py joanbot/intelligence/risk.py joanbot/execution --include="*.py"; then
  echo "FAIL_RESEARCH_PROMOTION_ALREADY_IN_DECISION_PATH"
  FAIL=1
else
  echo "DECISION_PATH_UNCHANGED_OK"
fi

echo "===== STORAGE BOUND ====="
ROWS=$(sqlite3 data/joanbot_v14.sqlite "SELECT COUNT(*) FROM research_promotion_decisions_v1;")
if [ "$ROWS" -gt 500 ]; then
  echo "FAIL_RESEARCH_PROMOTION_OVER_LIMIT=$ROWS"
  FAIL=1
else
  echo "RESEARCH_PROMOTION_STORAGE_BOUND_OK rows=$ROWS"
fi

echo "===== SIZE CHECK ====="
ls -lh data/joanbot_v14.sqlite*
df -h /storage/emulated

echo "===== GIT DIFF CHECK ====="
git diff --check || FAIL=1

echo "===== STATUS ====="
git status --short

if [ "$FAIL" -eq 0 ]; then
  echo "RESEARCH_PROMOTION_POLICY_V1_AUDIT_OK"
else
  echo "RESEARCH_PROMOTION_POLICY_V1_AUDIT_FAIL"
  exit 1
fi
