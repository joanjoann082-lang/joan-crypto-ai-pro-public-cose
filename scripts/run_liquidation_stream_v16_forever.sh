#!/data/data/com.termux/files/usr/bin/bash
cd "$(dirname "$0")/.."
export PYTHONPATH=$PWD

mkdir -p data/v16

LOG="data/v16/liquidation_forever.log"
ERR="data/v16/liquidation_forever_errors.log"
LOCK="data/v16/liquidation_forever.lock"
PIDFILE="data/v16/liquidation_forever.pid"

if [ -f "$LOCK" ]; then
  OLD="$(cat "$LOCK" 2>/dev/null || true)"
  if [ -n "$OLD" ] && kill -0 "$OLD" 2>/dev/null; then
    echo "LIQ_FOREVER_ALREADY_RUNNING pid=$OLD"
    exit 0
  fi
fi

echo $$ > "$LOCK"
echo $$ > "$PIDFILE"

BACKOFF=10

while true; do
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) LIQ_STREAM_START_ATTEMPT backoff=$BACKOFF" >> "$LOG"

  python -m joanbot.institutional_v16.liquidation_stream_v16 \
    >> data/v16/liquidation_stream.log \
    2>> data/v16/liquidation_stream_errors.log

  RC=$?
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) LIQ_STREAM_EXITED rc=$RC" >> "$ERR"

  sleep "$BACKOFF"

  if [ "$BACKOFF" -lt 300 ]; then
    BACKOFF=$((BACKOFF * 2))
  fi
done
