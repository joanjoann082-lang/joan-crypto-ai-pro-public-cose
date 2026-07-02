#!/data/data/com.termux/files/usr/bin/bash

cd "$(dirname "$0")/.."
export PYTHONPATH=$PWD

mkdir -p data/v16

PIDFILE="data/v16/overnight.pid"
LOCKFILE="data/v16/overnight.lock"
LOG="data/v16/overnight.log"
ERR="data/v16/overnight_errors.log"

echo $$ > "$PIDFILE"
echo $$ > "$LOCKFILE"

log() {
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) $*" >> "$LOG"
}

err() {
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) $*" >> "$ERR"
}

notify_error() {
  MSG="$1"
  python - <<PY 2>/dev/null || true
try:
    from joanbot.integrations.telegram_v11 import send_message
    send_message("⚠️ JoanBot V16 watchdog: ${MSG}")
except Exception:
    pass
PY
}

run_step() {
  NAME="$1"
  shift

  log "STEP_START $NAME"

  "$@" >> "$LOG" 2>> "$ERR"
  RC=$?

  if [ "$RC" -ne 0 ]; then
    err "STEP_FAIL $NAME rc=$RC"
    notify_error "step failed: $NAME rc=$RC"
    return "$RC"
  fi

  log "STEP_OK $NAME"
  return 0
}

check_v11_alive() {
  if ps -ef | grep -Ei "python.*joanbot.runtime.institutional_runtime_v11" | grep -v grep >/dev/null; then
    log "V11_ALIVE"
    return 0
  fi

  err "V11_NOT_ALIVE"

  OPEN_V11=$(sqlite3 data/joanbot_v14.sqlite "
  SELECT COUNT(*)
  FROM paper_micro_canary_positions_v11
  WHERE status='OPEN' OR closed_at IS NULL;
  " 2>/dev/null || echo 0)

  if [ "$OPEN_V11" -eq 0 ]; then
    err "V11_RESTART_ATTEMPT_NO_OPEN_CANARY"
    bash scripts/start_v11_clean.sh >> "$LOG" 2>> "$ERR" || {
      err "V11_RESTART_FAILED"
      notify_error "V11 not alive and restart failed"
      return 1
    }
    notify_error "V11 was not alive; restarted without open canary"
  else
    notify_error "V11 not alive but open canary exists; not restarting"
  fi
}

light_db_check() {
  sqlite3 data/joanbot_v14.sqlite "PRAGMA quick_check;" >> "$LOG" 2>> "$ERR" || {
    err "DB_QUICK_CHECK_FAILED"
    notify_error "DB quick_check failed"
  }
}

retention() {
  if [ -x scripts/v16_retention.sh ]; then
    bash scripts/v16_retention.sh >> "$LOG" 2>> "$ERR" || err "V16_RETENTION_FAILED"
  else
    sqlite3 data/joanbot_v14.sqlite "PRAGMA wal_checkpoint(TRUNCATE);" >> "$LOG" 2>> "$ERR" || true
  fi
}

log "V16_RESILIENT_WATCHDOG_START pid=$$"

i=0

while true; do
  log "TICK $i"

  check_v11_alive

  run_step "ALPHA_KERNEL_V16_ONCE" bash scripts/run_alpha_kernel_v16_once.sh
  run_step "POSITION_MANAGER_V11_ONCE" bash scripts/v11_position_manager_once.sh

  if [ $((i % 10)) -eq 0 ]; then
    light_db_check
  fi

  if [ $((i % 20)) -eq 0 ]; then
    retention
  fi

  i=$((i+1))
  sleep 180
done
