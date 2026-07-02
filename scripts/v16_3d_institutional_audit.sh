#!/data/data/com.termux/files/usr/bin/bash
cd "$(dirname "$0")/.."
DB="data/joanbot_v14.sqlite"

echo "===== V16.3D INSTITUTIONAL AUDIT ====="

echo
echo "===== 1 ACTIVE PROCESSES ====="
ps -ef | grep -Ei "institutional_runtime_v11|run_alpha_kernel_v16_overnight|run_liquidation_stream_v16_forever|liquidation_stream_v16|v16_process_supervisor|joanbot.runner|joanbot.orchestrator|institutional_runtime_v9|institutional_runtime_v10" | grep -v grep || true

echo
echo "===== 2 OPEN CANARIES ====="
sqlite3 -header -column "$DB" "
SELECT id, symbol, side, setup, status, opened_at, closed_at, ROUND(net_pnl_r,4) net_r, reason
FROM paper_micro_canary_positions_v11
ORDER BY id DESC
LIMIT 10;
" 2>/dev/null || echo "NO_V11_CANARY_TABLE"

echo
echo "===== 3 DB HEALTH ====="
sqlite3 "$DB" "PRAGMA quick_check;" 2>/dev/null || echo "DB_QUICK_CHECK_FAILED"

echo
echo "===== 4 DB FILES ====="
ls -lh data/joanbot_v14.sqlite data/joanbot_v14.sqlite-wal data/joanbot_v14.sqlite-shm 2>/dev/null || true

echo
echo "===== 5 TOP TABLE SIZE ====="
sqlite3 -header -column "$DB" "
SELECT name, ROUND(SUM(pgsize)/1024.0/1024.0,2) mb
FROM dbstat
GROUP BY name
ORDER BY mb DESC
LIMIT 30;
" 2>/dev/null || echo "DBSTAT_FAILED"

echo
echo "===== 6 V16 TABLE EXISTENCE ====="
sqlite3 -header -column "$DB" "
SELECT name
FROM sqlite_master
WHERE name LIKE 'alpha_%v16%' OR name LIKE '%liquidation%v16%'
ORDER BY name;
" 2>/dev/null || true

echo
echo "===== 7 V16 COUNTS ====="
sqlite3 -header -column "$DB" "
SELECT 'alpha_research_v16' table_name, COUNT(*) n FROM alpha_research_v16
UNION ALL SELECT 'alpha_setup_registry_v16', COUNT(*) FROM alpha_setup_registry_v16
UNION ALL SELECT 'alpha_payload_library_v16', COUNT(*) FROM alpha_payload_library_v16
UNION ALL SELECT 'alpha_research_rollup_v16', COUNT(*) FROM alpha_research_rollup_v16
UNION ALL SELECT 'alpha_setup_registry_rollup_v16', COUNT(*) FROM alpha_setup_registry_rollup_v16
UNION ALL SELECT 'alpha_integration_registry_v16', COUNT(*) FROM alpha_integration_registry_v16
UNION ALL SELECT 'alpha_system_integrity_v16', COUNT(*) FROM alpha_system_integrity_v16
UNION ALL SELECT 'liquidation_stream_heartbeat_v16', COUNT(*) FROM liquidation_stream_heartbeat_v16
UNION ALL SELECT 'liquidation_events_v16', COUNT(*) FROM liquidation_events_v16;
" 2>/dev/null || echo "SOME_V16_TABLES_MISSING"

echo
echo "===== 8 PAYLOAD LIBRARY ====="
sqlite3 -header -column "$DB" "
SELECT
  COUNT(*) payloads,
  ROUND(SUM(raw_bytes)/1024.0/1024.0,2) raw_mb,
  ROUND(SUM(compressed_bytes)/1024.0/1024.0,2) compressed_mb,
  ROUND(100.0 * SUM(compressed_bytes) / NULLIF(SUM(raw_bytes),0),2) compressed_pct
FROM alpha_payload_library_v16;
" 2>/dev/null || echo "NO_PAYLOAD_LIBRARY"

echo
echo "===== 9 V16 FINAL GATE ====="
sqlite3 -line "$DB" "
SELECT ts, version, gate_state, policy, allow_trade, size_mult,
selected_symbol, selected_side, selected_setup, selected_profile,
selected_horizon_min, hard_vetoes
FROM latest_alpha_final_gate_v16;
" 2>/dev/null || echo "NO_V16_FINAL_GATE_VIEW"

echo
echo "===== 10 V16 GATE INSIDE V11 CONTROL PAYLOAD ====="
python - <<'PY'
import sqlite3, json
db="data/joanbot_v14.sqlite"
con=sqlite3.connect(db)
con.row_factory=sqlite3.Row
try:
    r=con.execute("""
    SELECT ts, global_state, decision_tier, recommended_action,
           allow_paper_micro_canary, max_size_usd, hard_vetoes, payload
    FROM latest_institutional_control_plane_v11;
    """).fetchone()
    if not r:
        print("NO_CONTROL_ROW")
    else:
        p=json.loads(r["payload"] or "{}")
        out={
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
            "alpha_gate_v16_allow_trade": p.get("alpha_gate_v16_allow_trade"),
            "alpha_gate_v16_hard_vetoes": p.get("alpha_gate_v16_hard_vetoes"),
        }
        print(json.dumps(out, indent=2, default=str))
finally:
    con.close()
PY

echo
echo "===== 11 LIQUIDATION HEALTH ====="
sqlite3 -line "$DB" "
SELECT *
FROM latest_liquidation_stream_heartbeat_v16;
" 2>/dev/null || echo "NO_LIQ_HEARTBEAT"

echo
echo "===== 12 INTEGRATION REGISTRY ====="
sqlite3 -header -column "$DB" "
SELECT component, state, role, overlap_guard, hard_contract
FROM alpha_integration_registry_v16
ORDER BY component;
" 2>/dev/null || echo "NO_INTEGRATION_REGISTRY"

echo
echo "===== 13 SYSTEM INTEGRITY ====="
sqlite3 -header -column "$DB" "
SELECT ts, check_name, state, severity
FROM alpha_system_integrity_v16
ORDER BY id DESC
LIMIT 20;
" 2>/dev/null || echo "NO_SYSTEM_INTEGRITY"

echo
echo "===== 14 CODE MARKERS ====="
echo "--- control_plane_v11 ---"
grep -n "ALPHA_GATE_V16\|alpha_gate_v16\|apply_alpha_final_gate_v16" joanbot/control/control_plane_v11.py 2>/dev/null | head -80 || true

echo "--- alpha_kernel_v16 ---"
grep -n "ALPHA_KERNEL_V16_3B_STORAGE_SPINE\|archive_payload_v16\|alpha_payload_library_v16" joanbot/institutional_v16/alpha_kernel_v16.py 2>/dev/null | head -80 || true

echo "--- liquidation_stream_v16 ---"
grep -n "LIQUIDATION_STREAM_V16_2\|latest_liquidation_stream_heartbeat_v16\|WS_PONG\|btcusdt@forceOrder" joanbot/institutional_v16/liquidation_stream_v16.py 2>/dev/null | head -80 || true

echo
echo "===== 15 SCRIPT INVENTORY ====="
ls -lh scripts/*v16* scripts/status*v16* 2>/dev/null || true

echo
echo "===== 16 RECENT ERRORS ====="
tail -100 data/v16/integration_guard_errors.log 2>/dev/null || true
tail -100 data/v16/storage_spine_guard_errors.log 2>/dev/null || true
tail -100 data/v16/overnight_errors.log 2>/dev/null || true
tail -100 data/v16/process_supervisor_errors.log 2>/dev/null || true
tail -100 data/v16/liquidation_stream_errors.log 2>/dev/null || true
tail -100 data/v16/liquidation_forever_errors.log 2>/dev/null || true
tail -100 data/institutional_runtime_v11_errors.log 2>/dev/null || true

echo
echo "===== 17 GIT STATUS ====="
git status --short 2>/dev/null || true
git log --oneline -5 2>/dev/null || true

echo
echo "===== AUDIT_DONE ====="
