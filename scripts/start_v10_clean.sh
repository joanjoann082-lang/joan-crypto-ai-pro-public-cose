#!/data/data/com.termux/files/usr/bin/bash
set -e
cd "$(dirname "$0")/.."
export PYTHONPATH=$PWD

echo "===== STOP OLD RUNTIMES ====="
pkill -f "python.*joanbot.runner" 2>/dev/null || true
pkill -f "python.*joanbot.orchestrator" 2>/dev/null || true
pkill -f "python.*joanbot.runtime" 2>/dev/null || true
sleep 3

if ps -ef | grep -Ei "python.*joanbot.runner|python.*joanbot.orchestrator|python.*joanbot.runtime" | grep -v grep; then
  echo "RUNTIME_ACTIVE_ABORT"
  exit 1
fi

command -v termux-wake-lock >/dev/null 2>&1 && termux-wake-lock || true
mkdir -p data

echo "===== START V10 ====="
nohup python -u -m joanbot.runtime.institutional_runtime_v10 > data/institutional_runtime_v10.log 2> data/institutional_runtime_v10_errors.log &
sleep 25

echo "===== PROCESS ====="
ps -ef | grep -Ei "python.*joanbot.runtime.institutional_runtime_v10|python.*joanbot.runner|python.*joanbot.orchestrator" | grep -v grep || echo "NO_PROCESS_ACTIVE"

echo "===== ERRORS ====="
tail -120 data/institutional_runtime_v10_errors.log 2>/dev/null || true

echo "===== STATUS V10 ====="
bash scripts/status_v10.sh
