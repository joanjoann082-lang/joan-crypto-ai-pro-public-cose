#!/data/data/com.termux/files/usr/bin/bash
set -e

cd "$(dirname "$0")/.."
export PYTHONPATH=$PWD
FAIL=0

echo "===== ALPHA OPERATING LAYER V5 RUNNER INTEGRATION AUDIT ====="

echo "===== NO RUNTIME ====="
if ps -ef | grep -Ei "python.*joanbot.runner|joanbot_overnight_supervisor|joanbot.ui.dashboard|telegram_command_bot" | grep -v grep; then
  echo "RUNTIME_ACTIVE_ABORT"
  exit 1
else
  echo "NO_RUNTIME_OK"
fi

echo "===== COMPILE FULL ALPHA PATH ====="
python -m py_compile \
  joanbot/runner.py \
  joanbot/alpha/alpha_evidence_tensor_v5.py \
  joanbot/alpha/alpha_bayesian_posterior_v5.py \
  joanbot/alpha/alpha_meta_governance_v5.py \
  joanbot/alpha/alpha_promotion_contract_v5.py \
  joanbot/intelligence/decision.py \
  joanbot/intelligence/risk.py \
  || FAIL=1

echo "===== RUNNER STATIC CHECK ====="
grep -RIn "AlphaEvidenceTensorV5" joanbot/runner.py || FAIL=1
grep -RIn "AlphaBayesianPosteriorV5" joanbot/runner.py || FAIL=1
grep -RIn "AlphaMetaGovernanceV5" joanbot/runner.py || FAIL=1
grep -RIn "AlphaPromotionContractV5" joanbot/runner.py || FAIL=1
grep -RIn "alpha_operating_layer_v5_refresh" joanbot/runner.py || FAIL=1

echo "===== SAFETY: NOT IN DECISION/RISK/EXECUTION ====="
if grep -RInE "AlphaEvidenceTensorV5|AlphaBayesianPosteriorV5|AlphaMetaGovernanceV5|AlphaPromotionContractV5|latest_alpha_promotion_contract_v5" \
  joanbot/intelligence/decision.py \
  joanbot/intelligence/risk.py \
  joanbot/execution \
  --include="*.py"; then
  echo "FAIL_ALPHA_LAYER_CONNECTED_TO_TRADING_PATH"
  FAIL=1
else
  echo "TRADING_PATH_CLEAN_OK"
fi

echo "===== DB INTEGRITY ====="
sqlite3 data/joanbot_v14.sqlite "PRAGMA integrity_check;" || FAIL=1

echo "===== GIT DIFF CHECK ====="
git diff --check || FAIL=1

echo "===== STATUS ====="
git status --short

if [ "$FAIL" -eq 0 ]; then
  echo "ALPHA_OPERATING_LAYER_V5_RUNNER_INTEGRATION_AUDIT_OK"
else
  echo "ALPHA_OPERATING_LAYER_V5_RUNNER_INTEGRATION_AUDIT_FAIL"
  exit 1
fi
