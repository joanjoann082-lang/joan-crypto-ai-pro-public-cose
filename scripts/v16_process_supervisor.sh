#!/data/data/com.termux/files/usr/bin/bash
cd "$(dirname "$0")/.."
export PYTHONPATH=$PWD

mkdir -p data/v16

LOG="data/v16/process_supervisor.log"
ERR="data/v16/process_supervisor_errors.log"
LOCK="data/v16/process_supervisor.lock"
PIDFILE="data/v16/process_supervisor.pid"

if [ -f "$LOCK" ]; then
  OLD="$(cat "$LOCK" 2>/dev/null || true)"
  if [ -n "$OLD" ] && kill -0 "$OLD" 2>/dev/null; then
    echo "PROCESS_SUPERVISOR_ALREADY_RUNNING pid=$OLD"
    exit 0
  fi
fi

echo $$ > "$LOCK"
echo $$ > "$PIDFILE"

log() {
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) $*" >> "$LOG"
}

err() {
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) $*" >> "$ERR"
}

open_v11_count() {
  sqlite3 data/joanbot_v14.sqlite "
  SELECT COUNT(*)
  FROM paper_micro_canary_positions_v11
  WHERE status='OPEN' OR closed_at IS NULL;
  " 2>/dev/null || echo 0
}

while true; do
  log "SUPERVISOR_TICK"

  if ps -ef | grep -Ei "python.*joanbot.runtime.institutional_runtime_v11" | grep -v grep >/dev/null; then
    log "V11_OK"
  else
    OPEN="$(open_v11_count)"
    err "V11_MISSING open_canaries=$OPEN"

    if [ "$OPEN" -eq 0 ]; then
      bash scripts/start_v11_clean.sh >> "$LOG" 2>> "$ERR" || err "V11_RESTART_FAILED"
    fi
  fi

  if ps -ef | grep -Ei "run_alpha_kernel_v16_overnight" | grep -v grep >/dev/null; then
    log "WATCHDOG_OK"
  else
    err "WATCHDOG_MISSING_RESTARTING"
    nohup bash scripts/run_alpha_kernel_v16_overnight.sh \
      > data/v16/overnight_stdout.log \
      2> data/v16/overnight_stderr.log &
    echo $! > data/v16/overnight.pid
  fi

  if ps -ef | grep -Ei "run_liquidation_stream_v16_forever" | grep -v grep >/dev/null; then
    log "LIQ_FOREVER_OK"
  else
    err "LIQ_FOREVER_MISSING_RESTARTING"
    nohup bash scripts/run_liquidation_stream_v16_forever.sh \
      > data/v16/liquidation_forever_stdout.log \
      2> data/v16/liquidation_forever_stderr.log &
    echo $! > data/v16/liquidation_forever.pid
  fi

  sqlite3 data/joanbot_v14.sqlite "PRAGMA quick_check;" >> "$LOG" 2>> "$ERR" || err "DB_QUICK_CHECK_FAILED"

  sleep 180
done
