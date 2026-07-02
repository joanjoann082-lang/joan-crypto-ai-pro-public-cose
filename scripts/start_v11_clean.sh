#!/data/data/com.termux/files/usr/bin/bash
set -e

cd "$(dirname "$0")/.."
export PYTHONPATH=$PWD
mkdir -p data

FORCE=0
if [ "${1:-}" = "--force" ] || [ "${1:-}" = "--force-risk" ]; then
  FORCE=1
fi

echo "===== V11 CLEAN START ====="


OPEN_V11=$(sqlite3 data/joanbot_v14.sqlite "
SELECT COUNT(*)
FROM paper_micro_canary_positions_v11
WHERE status='OPEN' OR closed_at IS NULL;
" 2>/dev/null || echo 0)

if [ "${1:-}" = "--force" ] && [ "$OPEN_V11" -gt 0 ]; then
  echo "V11_OPEN_CANARY_FORCE_RESTART_ABORT"
  echo "open_v11_canaries=$OPEN_V11"
  echo "Use: bash scripts/v11_position_manager_once.sh"
  echo "Emergency only: bash scripts/start_v11_clean.sh --force-risk"
  exit 1
fi

echo "===== CURRENT BOT PROCESSES ====="
ps -ef | grep -Ei "python.*joanbot.runtime|python.*joanbot.runner|python.*joanbot.orchestrator" | grep -v grep || echo "NO_PROCESS_ACTIVE"

V11_ACTIVE="$(ps -ef | grep -Ei 'python.*-m joanbot.runtime.institutional_runtime_v11' | grep -v grep || true)"

if [ -n "$V11_ACTIVE" ] && [ "$FORCE" -ne 1 ]; then
  echo
  echo "V11_ALREADY_ACTIVE_NO_RESTART"
  echo "$V11_ACTIVE"
  echo
  echo "Use: bash scripts/start_v11_clean.sh --force"
  echo "only if you intentionally want to restart V11."
  echo
  bash scripts/status_v11.sh || true
  exit 0
fi

echo
echo "===== STOP LEGACY / OLD RUNTIMES ====="
pkill -f "python.*joanbot.runner" 2>/dev/null || true
pkill -f "python.*joanbot.orchestrator" 2>/dev/null || true
pkill -f "python.*joanbot.runtime.alpha_runtime_v7" 2>/dev/null || true
pkill -f "python.*joanbot.runtime.institutional_runtime_v9" 2>/dev/null || true
pkill -f "python.*joanbot.runtime.institutional_runtime_v10" 2>/dev/null || true

if [ "$FORCE" -eq 1 ]; then
  echo "FORCE_RESTART_V11"
  pkill -f "python.*joanbot.runtime.institutional_runtime_v11" 2>/dev/null || true
fi

sleep 3

echo
echo "===== VERIFY NO BAD RUNTIME ====="
BAD="$(ps -ef | grep -Ei 'python.*joanbot.runner|python.*joanbot.orchestrator|python.*alpha_runtime_v7|python.*institutional_runtime_v9|python.*institutional_runtime_v10' | grep -v grep || true)"
if [ -n "$BAD" ]; then
  echo "BAD_RUNTIME_STILL_ACTIVE_ABORT"
  echo "$BAD"
  exit 1
fi
echo "NO_BAD_RUNTIME_OK"

V11_ACTIVE="$(ps -ef | grep -Ei 'python.*-m joanbot.runtime.institutional_runtime_v11' | grep -v grep || true)"
if [ -n "$V11_ACTIVE" ]; then
  echo
  echo "V11_ALREADY_ACTIVE_AFTER_CLEAN"
  echo "$V11_ACTIVE"
  bash scripts/status_v11.sh || true
  exit 0
fi

echo
echo "===== WAKE LOCK ====="
command -v termux-wake-lock >/dev/null 2>&1 && termux-wake-lock || true

echo
echo
echo "===== PRE-START DB RETENTION ====="
bash scripts/db_retention_light_v11.sh || {
  echo "DB_RETENTION_FAILED_ABORT"
  exit 1
}

echo "===== START V11 ====="
TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "$TS START_V11" >> data/institutional_runtime_v11_process.log

nohup python -u -m joanbot.runtime.institutional_runtime_v11 \
  > data/institutional_runtime_v11.log \
  2> data/institutional_runtime_v11_errors.log &

PID=$!
echo "$PID" > data/institutional_runtime_v11.pid
echo "PID=$PID"

sleep 8

if kill -0 "$PID" 2>/dev/null; then
  echo "V11_PROCESS_ALIVE_AFTER_8S"
else
  echo "V11_DIED_AFTER_START"
  echo "===== ERRORS ====="
  tail -200 data/institutional_runtime_v11_errors.log || true
  echo "===== LOG ====="
  tail -200 data/institutional_runtime_v11.log || true
  exit 1
fi

sleep 20

echo
echo "===== FINAL PROCESS ====="
ps -ef | grep -Ei "python.*joanbot.runtime|python.*joanbot.runner|python.*joanbot.orchestrator" | grep -v grep || echo "NO_PROCESS_ACTIVE"

echo
echo "===== STATUS V11 ====="
bash scripts/status_v11.sh || true
