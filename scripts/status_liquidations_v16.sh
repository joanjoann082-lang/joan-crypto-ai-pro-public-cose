#!/data/data/com.termux/files/usr/bin/bash
cd "$(dirname "$0")/.."
DB="data/joanbot_v14.sqlite"

echo "===== LIQUIDATION PROCESS ====="
ps -ef | grep -Ei "run_liquidation_stream_v16_forever|liquidation_stream_v16" | grep -v grep || true

echo
echo "===== LIQUIDATION HEARTBEAT ====="
sqlite3 -line "$DB" "
SELECT *
FROM latest_liquidation_stream_heartbeat_v16;
" 2>/dev/null || echo "NO_LIQUIDATION_HEARTBEAT"

echo
echo "===== LIQUIDATION COUNTS ====="
sqlite3 -header -column "$DB" "
SELECT
  COUNT(*) AS total_events,
  MAX(ts) AS last_event_ts,
  ROUND(SUM(notional_usd),2) AS total_notional
FROM liquidation_events_v16;
" 2>/dev/null || true

echo
echo "===== LAST EVENTS ====="
sqlite3 -header -column "$DB" "
SELECT ts, symbol, side, ROUND(notional_usd,2) AS notional_usd, avg_price
FROM liquidation_events_v16
ORDER BY id DESC
LIMIT 10;
" 2>/dev/null || true

echo
echo "===== LIQ ERRORS ====="
tail -80 data/v16/liquidation_stream_errors.log 2>/dev/null || true
tail -80 data/v16/liquidation_forever_errors.log 2>/dev/null || true
