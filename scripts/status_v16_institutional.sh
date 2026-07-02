#!/data/data/com.termux/files/usr/bin/bash
cd "$(dirname "$0")/.."
DB="data/joanbot_v14.sqlite"

echo "===== 1 PROCESSES ====="
ps -ef | grep -Ei "institutional_runtime_v11|run_alpha_kernel_v16_overnight|run_liquidation_stream_v16_forever|liquidation_stream_v16|v16_process_supervisor|joanbot.runner|joanbot.orchestrator|institutional_runtime_v9|institutional_runtime_v10" | grep -v grep || true

echo
echo "===== 2 DB HEALTH ====="
sqlite3 "$DB" "PRAGMA quick_check;" 2>/dev/null || true

echo
echo "===== 3 INTEGRATION REGISTRY ====="
sqlite3 -header -column "$DB" "
SELECT component, state, role, overlap_guard
FROM alpha_integration_registry_v16
ORDER BY component;
" 2>/dev/null || true

echo
echo "===== 4 SYSTEM INTEGRITY ====="
sqlite3 -header -column "$DB" "
SELECT ts, check_name, state, severity
FROM alpha_system_integrity_v16
ORDER BY id DESC
LIMIT 20;
" 2>/dev/null || true

echo
echo "===== 5 CONTRACTS ====="
sqlite3 -header -column "$DB" "
SELECT contract_name, state, severity, rule
FROM alpha_institutional_contract_v16
ORDER BY id DESC
LIMIT 20;
" 2>/dev/null || true

echo
echo "===== 6 V16 COUNTS ====="
for T in alpha_research_v16 alpha_setup_registry_v16 alpha_payload_library_v16 alpha_research_rollup_v16 alpha_setup_registry_rollup_v16 alpha_integration_registry_v16 alpha_system_integrity_v16 alpha_institutional_contract_v16 liquidation_stream_heartbeat_v16 liquidation_events_v16; do
  EXISTS=$(sqlite3 "$DB" "SELECT COUNT(*) FROM sqlite_master WHERE type IN ('table','view') AND name='$T';" 2>/dev/null || echo 0)
  if [ "$EXISTS" = "1" ]; then
    C=$(sqlite3 "$DB" "SELECT COUNT(*) FROM $T;" 2>/dev/null || echo ERR)
    echo "$T $C"
  else
    echo "$T MISSING"
  fi
done

echo
echo "===== 7 PAYLOAD LIBRARY ====="
sqlite3 -header -column "$DB" "
SELECT
  COUNT(*) AS payloads,
  ROUND(SUM(raw_bytes)/1024.0/1024.0,2) AS raw_mb,
  ROUND(SUM(compressed_bytes)/1024.0/1024.0,2) AS compressed_mb,
  ROUND(100.0 * SUM(compressed_bytes) / NULLIF(SUM(raw_bytes),0),2) AS compressed_pct
FROM alpha_payload_library_v16;
" 2>/dev/null || true

echo
echo "===== 8 V16 GATE INSIDE V11 ====="
python - <<'PY'
import sqlite3, json
con = sqlite3.connect("data/joanbot_v14.sqlite")
con.row_factory = sqlite3.Row
try:
    r = con.execute("""
        SELECT ts, global_state, decision_tier, recommended_action,
               allow_paper_micro_canary, max_size_usd, hard_vetoes, payload
        FROM latest_institutional_control_plane_v11;
    """).fetchone()
    if not r:
        print("NO_CONTROL_ROW")
    else:
        p = json.loads(r["payload"] or "{}")
        print(json.dumps({
            "ts": r["ts"],
            "global_state": r["global_state"],
            "decision_tier": r["decision_tier"],
            "recommended_action": r["recommended_action"],
            "allow_paper_micro_canary": r["allow_paper_micro_canary"],
            "max_size_usd": r["max_size_usd"],
            "hard_vetoes": r["hard_vetoes"],
            "alpha_gate_v16_seen": p.get("alpha_gate_v16_seen"),
            "alpha_gate_v16_state": p.get("alpha_gate_v16_state"),
            "alpha_gate_v16_policy": p.get("alpha_gate_v16_policy"),
            "alpha_gate_v16_hard_vetoes": p.get("alpha_gate_v16_hard_vetoes"),
        }, indent=2, default=str))
finally:
    con.close()
PY

echo
echo "===== 9 LIQUIDATION HEALTH ====="
sqlite3 -line "$DB" "
SELECT *
FROM latest_liquidation_stream_heartbeat_v16;
" 2>/dev/null || true

echo
echo "===== 10 RECENT ERRORS ====="
tail -80 data/v16/integration_guard_errors.log 2>/dev/null || true
tail -80 data/v16/storage_spine_guard_errors.log 2>/dev/null || true
tail -80 data/v16/overnight_errors.log 2>/dev/null || true
tail -80 data/v16/process_supervisor_errors.log 2>/dev/null || true
tail -80 data/v16/liquidation_stream_errors.log 2>/dev/null || true
tail -80 data/v16/liquidation_forever_errors.log 2>/dev/null || true
tail -80 data/institutional_runtime_v11_errors.log 2>/dev/null || true
