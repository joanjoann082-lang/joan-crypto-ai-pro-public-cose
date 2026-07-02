#!/data/data/com.termux/files/usr/bin/bash
set -e

cd "$(dirname "$0")/.."
export PYTHONPATH=$PWD

FAIL=0
RET_EVENTS=2000
RET_FEATURES=600

echo "===== LIQUIDITY INTELLIGENCE V1 AUDIT ====="

echo "===== NO RUNTIME ====="
if ps -ef | grep -Ei "joanbot.runner|telegram_command_bot|dashboard" | grep -v grep; then
  echo "RUNTIME_ACTIVE_ABORT"
  exit 1
else
  echo "NO_RUNTIME_OK"
fi

echo "===== DEPENDENCY ====="
python - <<'PY' || FAIL=1
import websocket
print("WEBSOCKET_CLIENT_OK")
PY

echo "===== COMPILE ====="
python -m py_compile \
  joanbot/market/liquidity_intelligence_v1.py \
  joanbot/intelligence/evidence_engine_v1.py \
  joanbot/intelligence/statistical_edge_authority_v1.py \
  joanbot/intelligence/decision.py \
  joanbot/intelligence/risk.py \
  joanbot/execution/contract.py \
  joanbot/runner.py \
  || FAIL=1

echo "===== BOUNDARY CHECK ====="
if grep -R "liquidity_intelligence_v1" joanbot/intelligence joanbot/execution joanbot/runner.py 2>/dev/null; then
  echo "BAD_LIQUIDITY_INTELLIGENCE_ALREADY_IN_DECISION_PIPELINE"
  FAIL=1
else
  echo "LIQUIDITY_NOT_IN_DECISION_PIPELINE_OK"
fi

echo "===== SELF TEST TEMP DB ====="
TMP_DB="data/tmp_liquidity_intelligence_selftest.sqlite"
rm -f "$TMP_DB"
JOANBOT_DB="$TMP_DB" python -m joanbot.market.liquidity_intelligence_v1 --self-test || FAIL=1
rm -f "$TMP_DB"

echo "===== INIT REAL DB ====="
python -m joanbot.market.liquidity_intelligence_v1 --init || FAIL=1

echo "===== SNAPSHOT REAL DB APPLY ====="
python -m joanbot.market.liquidity_intelligence_v1 \
  --symbols BTCUSDT,ETHUSDT \
  --lookback-min 15 \
  --retention-events "$RET_EVENTS" \
  --retention-features "$RET_FEATURES" \
  --snapshot \
  --apply \
  || FAIL=1

echo "===== OPTIONAL 20S LIVE STREAM SOURCE CHECK ====="
python -m joanbot.market.liquidity_intelligence_v1 \
  --symbols BTCUSDT,ETHUSDT \
  --lookback-min 15 \
  --retention-events "$RET_EVENTS" \
  --retention-features "$RET_FEATURES" \
  --stream-seconds 20 \
  --snapshot \
  --apply \
  || echo "LIVE_STREAM_NON_FATAL_SOURCE_ERROR"

echo "===== DB SAFETY ====="
sqlite3 -header -column data/joanbot_v14.sqlite "PRAGMA integrity_check;" || FAIL=1

echo "===== TABLES ====="
sqlite3 -header -column data/joanbot_v14.sqlite "
SELECT name
FROM sqlite_master
WHERE type='table'
  AND name LIKE 'liquidity%';

SELECT symbol, COUNT(*) AS event_rows, ROUND(SUM(usd),2) AS total_usd, MAX(event_ms) AS latest_event_ms
FROM liquidity_liquidation_events_v1
GROUP BY symbol;

SELECT symbol, COUNT(*) AS feature_rows, MAX(ts) AS latest_ts,
       ROUND(AVG(stress_score),2) AS avg_stress,
       ROUND(MAX(total_liq_usd),2) AS max_total_liq
FROM liquidity_features_v1
GROUP BY symbol;

SELECT * FROM liquidity_source_health_v1;
" || FAIL=1

echo "===== RETENTION CHECK ====="
OVER_EVENTS=$(sqlite3 data/joanbot_v14.sqlite "
SELECT COUNT(*)
FROM (
  SELECT symbol
  FROM liquidity_liquidation_events_v1
  GROUP BY symbol
  HAVING COUNT(*) > $RET_EVENTS
);
")

OVER_FEATURES=$(sqlite3 data/joanbot_v14.sqlite "
SELECT COUNT(*)
FROM (
  SELECT symbol
  FROM liquidity_features_v1
  GROUP BY symbol
  HAVING COUNT(*) > $RET_FEATURES
);
")

if [ "$OVER_EVENTS" != "0" ]; then
  echo "RETENTION_EVENTS_FAIL=$OVER_EVENTS"
  FAIL=1
else
  echo "RETENTION_EVENTS_OK"
fi

if [ "$OVER_FEATURES" != "0" ]; then
  echo "RETENTION_FEATURES_FAIL=$OVER_FEATURES"
  FAIL=1
else
  echo "RETENTION_FEATURES_OK"
fi

echo "===== NO RAW TABLE CHECK ====="
RAW_TABLES=$(sqlite3 data/joanbot_v14.sqlite "
SELECT COUNT(*)
FROM sqlite_master
WHERE type='table'
  AND (
    name LIKE '%raw_liq%'
    OR name LIKE '%raw_force%'
    OR name LIKE '%liquidation_raw%'
  );
")
if [ "$RAW_TABLES" != "0" ]; then
  echo "RAW_TABLE_FAIL=$RAW_TABLES"
  FAIL=1
else
  echo "NO_RAW_TABLE_OK"
fi

echo "===== SIZE CHECK ====="
ls -lh data/joanbot_v14.sqlite*
df -h /storage/emulated

echo "===== GIT DIFF CHECK ====="
git diff --check || FAIL=1

echo "===== STATUS ====="
git status --short

if [ "$FAIL" -eq 0 ]; then
  echo "LIQUIDITY_INTELLIGENCE_V1_AUDIT_OK"
else
  echo "LIQUIDITY_INTELLIGENCE_V1_AUDIT_FAIL"
  exit 1
fi
