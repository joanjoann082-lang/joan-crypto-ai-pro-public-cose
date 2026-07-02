from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timezone

DB = "data/joanbot_v14.sqlite"
VERSION = "V16_3E_INSTITUTIONAL_CONTRACT_CLOSE"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def js(x) -> str:
    return json.dumps(x or {}, separators=(",", ":"), sort_keys=True, ensure_ascii=False, default=str)


def connect():
    con = sqlite3.connect(DB, timeout=120)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=120000;")
    return con


def q(cur, sql, params=(), attempts=10):
    last = None
    for i in range(attempts):
        try:
            return cur.execute(sql, params)
        except sqlite3.OperationalError as e:
            last = e
            msg = str(e).lower()
            if "locked" not in msg and "busy" not in msg:
                raise
            time.sleep(2 + i * 2)
    raise last


def exists(cur, name: str) -> bool:
    return q(
        cur,
        "SELECT COUNT(*) FROM sqlite_master WHERE type IN ('table','view') AND name=?",
        (name,),
    ).fetchone()[0] > 0


def scalar(cur, sql: str, default=0):
    try:
        row = q(cur, sql).fetchone()
        if not row:
            return default
        return list(row)[0]
    except Exception:
        return default


def ensure(cur):
    q(cur, """
        CREATE TABLE IF NOT EXISTS alpha_integration_registry_v16 (
            component TEXT PRIMARY KEY,
            updated_at TEXT NOT NULL,
            version TEXT NOT NULL,
            role TEXT NOT NULL,
            source_of_truth TEXT NOT NULL,
            state TEXT NOT NULL,
            overlap_guard TEXT NOT NULL,
            hard_contract TEXT NOT NULL,
            payload TEXT
        );
    """)

    q(cur, """
        CREATE TABLE IF NOT EXISTS alpha_system_integrity_v16 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            version TEXT NOT NULL,
            check_name TEXT NOT NULL,
            state TEXT NOT NULL,
            severity TEXT NOT NULL,
            payload TEXT
        );
    """)

    q(cur, """
        CREATE TABLE IF NOT EXISTS alpha_institutional_contract_v16 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            version TEXT NOT NULL,
            contract_name TEXT NOT NULL,
            state TEXT NOT NULL,
            severity TEXT NOT NULL,
            rule TEXT NOT NULL,
            payload TEXT
        );
    """)

    q(cur, "DROP VIEW IF EXISTS latest_alpha_system_integrity_v16;")
    q(cur, """
        CREATE VIEW latest_alpha_system_integrity_v16 AS
        SELECT *
        FROM alpha_system_integrity_v16
        ORDER BY id DESC
        LIMIT 100;
    """)


def upsert_registry(cur, component, role, source, state, overlap, contract, payload=None):
    q(cur, """
        INSERT INTO alpha_integration_registry_v16 (
            component, updated_at, version, role, source_of_truth,
            state, overlap_guard, hard_contract, payload
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(component) DO UPDATE SET
            updated_at=excluded.updated_at,
            version=excluded.version,
            role=excluded.role,
            source_of_truth=excluded.source_of_truth,
            state=excluded.state,
            overlap_guard=excluded.overlap_guard,
            hard_contract=excluded.hard_contract,
            payload=excluded.payload;
    """, (
        component,
        now_iso(),
        VERSION,
        role,
        source,
        state,
        overlap,
        contract,
        js(payload or {}),
    ))


def integrity(cur, name, state, severity, payload=None):
    q(cur, """
        INSERT INTO alpha_system_integrity_v16 (
            ts, version, check_name, state, severity, payload
        )
        VALUES (?, ?, ?, ?, ?, ?);
    """, (now_iso(), VERSION, name, state, severity, js(payload or {})))


def contract(cur, name, rule, severity="CRITICAL", state="ACTIVE"):
    q(cur, """
        INSERT INTO alpha_institutional_contract_v16 (
            ts, version, contract_name, state, severity, rule, payload
        )
        VALUES (?, ?, ?, ?, ?, ?, ?);
    """, (now_iso(), VERSION, name, state, severity, rule, js({"version": VERSION})))


def read_control(cur):
    out = {"exists": False, "alpha_gate_v16_seen": False}
    if not exists(cur, "latest_institutional_control_plane_v11"):
        return out

    row = q(cur, """
        SELECT ts, global_state, decision_tier, recommended_action,
               allow_paper_micro_canary, max_size_usd, hard_vetoes, payload
        FROM latest_institutional_control_plane_v11
        LIMIT 1;
    """).fetchone()

    if not row:
        return out

    out = dict(row)
    out["exists"] = True

    try:
        payload = json.loads(row["payload"] or "{}")
    except Exception:
        payload = {}

    out["alpha_gate_v16_seen"] = bool(payload.get("alpha_gate_v16_seen"))
    out["alpha_gate_v16_state"] = payload.get("alpha_gate_v16_state")
    out["alpha_gate_v16_policy"] = payload.get("alpha_gate_v16_policy")
    out["alpha_gate_v16_hard_vetoes"] = payload.get("alpha_gate_v16_hard_vetoes")
    return out


def read_liq(cur):
    out = {"exists": False, "state": None, "total_errors": None}
    if not exists(cur, "latest_liquidation_stream_heartbeat_v16"):
        return out

    row = q(cur, """
        SELECT state, total_errors, version, stream_url, ts
        FROM latest_liquidation_stream_heartbeat_v16
        LIMIT 1;
    """).fetchone()

    if not row:
        return out

    out = dict(row)
    out["exists"] = True
    return out


def main():
    con = connect()
    cur = con.cursor()

    quick = q(cur, "PRAGMA quick_check;").fetchone()[0]
    if quick != "ok":
        raise SystemExit(f"DB_QUICK_CHECK_FAILED={quick}")

    q(cur, "BEGIN IMMEDIATE;")
    ensure(cur)

    open_canaries = scalar(cur, """
        SELECT COUNT(*)
        FROM paper_micro_canary_positions_v11
        WHERE status='OPEN' OR closed_at IS NULL;
    """, 999)

    payloads = scalar(cur, "SELECT COUNT(*) FROM alpha_payload_library_v16;", 0) if exists(cur, "alpha_payload_library_v16") else 0
    raw_mb = scalar(cur, "SELECT ROUND(SUM(raw_bytes)/1024.0/1024.0,2) FROM alpha_payload_library_v16;", 0) if exists(cur, "alpha_payload_library_v16") else 0
    compressed_mb = scalar(cur, "SELECT ROUND(SUM(compressed_bytes)/1024.0/1024.0,2) FROM alpha_payload_library_v16;", 0) if exists(cur, "alpha_payload_library_v16") else 0

    research_rollup = scalar(cur, "SELECT COUNT(*) FROM alpha_research_rollup_v16;", 0) if exists(cur, "alpha_research_rollup_v16") else 0
    setup_rollup = scalar(cur, "SELECT COUNT(*) FROM alpha_setup_registry_rollup_v16;", 0) if exists(cur, "alpha_setup_registry_rollup_v16") else 0
    alpha_research_n = scalar(cur, "SELECT COUNT(*) FROM alpha_research_v16;", 0) if exists(cur, "alpha_research_v16") else 0
    setup_registry_n = scalar(cur, "SELECT COUNT(*) FROM alpha_setup_registry_v16;", 0) if exists(cur, "alpha_setup_registry_v16") else 0

    control = read_control(cur)
    liq = read_liq(cur)

    liq_ok = (
        liq.get("exists")
        and liq.get("state") in ("WS_OPEN", "WS_PONG", "EVENT_STORED")
        and int(liq.get("total_errors") or 0) == 0
    )

    storage_ok = payloads > 0 and research_rollup > 0 and setup_rollup > 0
    control_ok = bool(control.get("alpha_gate_v16_seen"))

    upsert_registry(
        cur,
        "V11_RUNTIME",
        "single active paper execution runtime",
        "python -u -m joanbot.runtime.institutional_runtime_v11",
        "ACTIVE_EXPECTED",
        "no joanbot.runner, orchestrator, V9 runtime or V10 runtime can run in parallel",
        "V11 can execute only if V11 control permits and V16 final gate does not block",
    )

    upsert_registry(
        cur,
        "V16_FINAL_GATE",
        "mandatory quantitative hard-gate above V11 control",
        "latest_alpha_final_gate_v16 + alpha_gate_v16_* persisted inside latest_institutional_control_plane_v11.payload",
        "MANDATORY",
        "no older V14/V15 final gate can be final authority",
        "can block/reduce only; cannot force open",
        control,
    )

    upsert_registry(
        cur,
        "V16_STORAGE_SPINE",
        "compressed payload library, setup rollups and retention spine",
        "alpha_payload_library_v16 + alpha_research_rollup_v16 + alpha_setup_registry_rollup_v16",
        "ACTIVE_EXPECTED",
        "heavy payloads must be archived by hash instead of duplicated per tick",
        "no checkpoint or compact inside open transaction",
        {
            "payloads": payloads,
            "raw_mb": raw_mb,
            "compressed_mb": compressed_mb,
            "research_rollup": research_rollup,
            "setup_rollup": setup_rollup,
        },
    )

    upsert_registry(
        cur,
        "V16_LIQUIDATION_STREAM",
        "BTC/ETH Binance forceOrder liquidation health source",
        "latest_liquidation_stream_heartbeat_v16 + liquidation_events_v16",
        "ACTIVE_EXPECTED",
        "one forever wrapper and one websocket process only",
        "WS_OPEN/WS_PONG is valid even if total liquidation events are zero",
        liq,
    )

    upsert_registry(
        cur,
        "V16_RESEARCH_KERNEL",
        "quantitative research layer: Bayesian posterior, CPCV, LCB, risk, attribution and quarantine",
        "alpha_research_v16 + alpha_setup_registry_v16 + alpha_final_gate_v16",
        "RESEARCH_ONLY_UNTIL_SAMPLE_READY",
        "research layer cannot bypass V16 final gate or V11 single-pipeline guard",
        "no live/paper canary without robust edge, sample, CPCV and gate permission",
    )

    integrity(cur, "DB_QUICK_CHECK", "OK", "INFO", {"quick_check": quick})
    integrity(cur, "OPEN_CANARY_SAFETY", "OK" if open_canaries == 0 else "FAIL", "CRITICAL" if open_canaries else "INFO", {"open_canaries": open_canaries})
    integrity(cur, "V16_GATE_PERSISTED_IN_V11_CONTROL", "OK" if control_ok else "FAIL", "CRITICAL" if not control_ok else "INFO", control)
    integrity(cur, "V16_LIQUIDATION_HEALTH", "OK" if liq_ok else "WARN", "WARN" if not liq_ok else "INFO", liq)
    integrity(cur, "V16_STORAGE_SPINE", "OK" if storage_ok else "FAIL", "CRITICAL" if not storage_ok else "INFO", {
        "payloads": payloads,
        "raw_mb": raw_mb,
        "compressed_mb": compressed_mb,
        "research_rollup": research_rollup,
        "setup_rollup": setup_rollup,
        "alpha_research_v16": alpha_research_n,
        "alpha_setup_registry_v16": setup_registry_n,
    })

    contract(cur, "NO_OVERLAPPING_RUNTIMES", "Only institutional_runtime_v11 may be the execution runtime.")
    contract(cur, "NO_OVERLAPPING_FINAL_GATES", "V16 final gate is the only alpha final authority.")
    contract(cur, "NO_RESEARCH_FORCE_OPEN", "Research/Discovery can rank and block but cannot force execution.")
    contract(cur, "NO_DB_MAINTENANCE_WITH_OPEN_CANARY", "Heavy DB maintenance is forbidden while a V11 canary is open.")
    contract(cur, "NO_CHECKPOINT_INSIDE_TRANSACTION", "WAL checkpoint/compact must run only after commit and closed connection.")
    contract(cur, "NO_FAKE_EXTERNAL_DATA", "ETF/options unavailable must remain DATA_UNAVAILABLE_NOT_FAKED.")
    contract(cur, "NO_PAYLOAD_BLOAT", "Heavy payloads must be deduplicated through alpha_payload_library_v16.")

    con.commit()
    con.close()

    print(json.dumps({
        "version": VERSION,
        "state": "DONE",
        "db_quick_check": quick,
        "open_canaries": open_canaries,
        "control_ok": control_ok,
        "liq_ok": liq_ok,
        "storage_ok": storage_ok,
        "payloads": payloads,
        "raw_mb": raw_mb,
        "compressed_mb": compressed_mb,
        "research_rollup": research_rollup,
        "setup_rollup": setup_rollup,
        "alpha_research_v16": alpha_research_n,
        "alpha_setup_registry_v16": setup_registry_n,
    }, indent=2, default=str))


if __name__ == "__main__":
    main()
