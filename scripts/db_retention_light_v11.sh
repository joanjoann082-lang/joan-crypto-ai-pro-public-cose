#!/data/data/com.termux/files/usr/bin/bash
set -e

cd "$(dirname "$0")/.."
DB="data/joanbot_v14.sqlite"

echo "===== V11 LIGHT DB RETENTION ====="

sqlite3 "$DB" "
DELETE FROM market_snapshots
WHERE rowid NOT IN (
  SELECT rowid FROM market_snapshots ORDER BY rowid DESC LIMIT 1500
);

DELETE FROM institutional_decision_order_v11
WHERE rowid NOT IN (
  SELECT rowid FROM institutional_decision_order_v11 ORDER BY rowid DESC LIMIT 500
);

DELETE FROM institutional_control_plane_v11
WHERE rowid NOT IN (
  SELECT rowid FROM institutional_control_plane_v11 ORDER BY rowid DESC LIMIT 500
);

DELETE FROM runtime_events
WHERE rowid NOT IN (
  SELECT rowid FROM runtime_events ORDER BY rowid DESC LIMIT 2000
);

PRAGMA wal_checkpoint(TRUNCATE);
PRAGMA optimize;
"

echo "DB_RETENTION_LIGHT_V11_OK"
