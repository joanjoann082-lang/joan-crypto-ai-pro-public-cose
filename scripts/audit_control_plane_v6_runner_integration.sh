#!/data/data/com.termux/files/usr/bin/bash
set -e

cd "$(dirname "$0")/.."
export PYTHONPATH=$PWD
FAIL=0

echo "===== CONTROL PLANE V6 RUNNER INTEGRATION AUDIT ====="

echo "===== NO RUNTIME ====="
if ps -ef | grep -Ei "python.*joanbot.runner|joanbot_overnight_supervisor|joanbot.ui.dashboard|telegram_command_bot" | grep -v grep; then
  echo "RUNTIME_ACTIVE_ABORT"
  exit 1
else
  echo "NO_RUNTIME_OK"
fi

echo "===== COMPILE ====="
python -m py_compile \
  joanbot/runner.py \
  joanbot/control/control_plane_v6.py \
  joanbot/alpha/alpha_evidence_tensor_v5.py \
  joanbot/alpha/alpha_bayesian_posterior_v5.py \
  joanbot/alpha/alpha_meta_governance_v5.py \
  joanbot/alpha/alpha_promotion_contract_v5.py \
  || FAIL=1

echo "===== STATIC INTEGRATION CHECK ====="
grep -RIn "InstitutionalControlPlaneV6" joanbot/runner.py || FAIL=1
grep -RIn "self.control_plane=InstitutionalControlPlaneV6" joanbot/runner.py || FAIL=1
grep -RIn "control_plane = self.control_plane.refresh" joanbot/runner.py || FAIL=1
grep -RIn "'control_plane': control_plane" joanbot/runner.py || FAIL=1

echo "===== DUPLICATION CHECK ====="
IMPORT_N=$(grep -RIn "from .control.control_plane_v6 import InstitutionalControlPlaneV6" joanbot/runner.py | wc -l)
INIT_N=$(grep -RIn "self.control_plane=InstitutionalControlPlaneV6" joanbot/runner.py | wc -l)
REFRESH_N=$(grep -RIn "control_plane = self.control_plane.refresh" joanbot/runner.py | wc -l)

echo "imports=$IMPORT_N init=$INIT_N refresh=$REFRESH_N"

if [ "$IMPORT_N" -ne 1 ]; then echo "FAIL_CONTROL_IMPORT_COUNT"; FAIL=1; fi
if [ "$INIT_N" -ne 1 ]; then echo "FAIL_CONTROL_INIT_COUNT"; FAIL=1; fi
if [ "$REFRESH_N" -ne 1 ]; then echo "FAIL_CONTROL_REFRESH_COUNT"; FAIL=1; fi

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

echo "===== DB INTEGRITY ====="
sqlite3 data/joanbot_v14.sqlite "PRAGMA integrity_check;" || FAIL=1

echo "===== GIT DIFF CHECK ====="
git diff --check || FAIL=1

echo "===== STATUS ====="
git status --short

if [ "$FAIL" -eq 0 ]; then
  echo "CONTROL_PLANE_V6_RUNNER_INTEGRATION_AUDIT_OK"
else
  echo "CONTROL_PLANE_V6_RUNNER_INTEGRATION_AUDIT_FAIL"
  exit 1
fi
