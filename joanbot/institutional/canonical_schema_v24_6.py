#!/usr/bin/env python3
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

VERSION = "V24_7_INSTITUTIONAL_CANONICAL_SCHEMA_INSERT_CONTRACT"

POSITION_TABLE = "paper_micro_canary_positions_v11"
INTENT_TABLE = "institutional_quant_canary_execution_intents_v17_7_2"
SCHEMA_AUDIT_TABLE = "institutional_v24_6_schema_contract_audit"

POSITION_COLUMNS = {
    "ts": "TEXT",
    "opened_at": "TEXT",
    "closed_at": "TEXT",
    "last_managed_at": "TEXT",
    "version": "TEXT",
    "adapter_version": "TEXT",
    "intent_id": "INTEGER",
    "stable_position_id": "TEXT",
    "key": "TEXT",
    "symbol": "TEXT",
    "side": "TEXT",
    "setup": "TEXT",
    "status": "TEXT",
    "entry_price": "REAL",
    "stop_loss_price": "REAL",
    "take_profit_price": "REAL",
    "exit_price": "REAL",
    "size_usd": "REAL",
    "requested_size_mult": "REAL",
    "manager_state": "TEXT",
    "close_reason": "TEXT",
    "pnl_usd": "REAL",
    "net_pnl_usd": "REAL",
    "pnl_r": "REAL",
    "net_pnl_r": "REAL",
    "mfe_r": "REAL",
    "mae_r": "REAL",
    "price_meta": "TEXT",
    "accounting_repair_version": "TEXT",
    "accounting_repaired_at": "TEXT",
}

INTENT_COLUMNS = {
    "position_row_id": "INTEGER",
    "stable_position_id": "TEXT",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def qid(x: str) -> str:
    return '"' + str(x).replace('"', '""') + '"'


def table_exists(con: sqlite3.Connection, table: str) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone() is not None


def cols(con: sqlite3.Connection, table: str) -> List[str]:
    if not table_exists(con, table):
        return []
    return [r[1] for r in con.execute(f"PRAGMA table_info({qid(table)})")]


def table_info(con: sqlite3.Connection, table: str) -> List[Dict[str, Any]]:
    if not table_exists(con, table):
        return []
    return [
        {
            "cid": r[0],
            "name": r[1],
            "type": r[2],
            "notnull": int(r[3] or 0),
            "dflt_value": r[4],
            "pk": int(r[5] or 0),
        }
        for r in con.execute(f"PRAGMA table_info({qid(table)})")
    ]


def create_audit_table(con: sqlite3.Connection) -> None:
    con.execute(f"""
    CREATE TABLE IF NOT EXISTS {qid(SCHEMA_AUDIT_TABLE)} (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT,
        version TEXT,
        action TEXT,
        table_name TEXT,
        column_name TEXT,
        payload TEXT
    )
    """)


def audit(con: sqlite3.Connection, action: str, table: str, column: Optional[str] = None, payload: Optional[Dict[str, Any]] = None) -> None:
    create_audit_table(con)
    con.execute(
        f"""
        INSERT INTO {qid(SCHEMA_AUDIT_TABLE)}
        (ts, version, action, table_name, column_name, payload)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (utc_now(), VERSION, action, table, column, json.dumps(payload or {}, sort_keys=True, default=str)),
    )


def ensure_position_table(con: sqlite3.Connection) -> None:
    if not table_exists(con, POSITION_TABLE):
        con.execute(f"CREATE TABLE {qid(POSITION_TABLE)} (id INTEGER PRIMARY KEY AUTOINCREMENT)")
        audit(con, "CREATE_TABLE", POSITION_TABLE)

    existing = set(cols(con, POSITION_TABLE))
    for name, typ in POSITION_COLUMNS.items():
        if name not in existing:
            con.execute(f"ALTER TABLE {qid(POSITION_TABLE)} ADD COLUMN {qid(name)} {typ}")
            audit(con, "ADD_COLUMN", POSITION_TABLE, name, {"type": typ})


def ensure_intent_table_columns(con: sqlite3.Connection) -> None:
    if not table_exists(con, INTENT_TABLE):
        return

    existing = set(cols(con, INTENT_TABLE))
    for name, typ in INTENT_COLUMNS.items():
        if name not in existing:
            con.execute(f"ALTER TABLE {qid(INTENT_TABLE)} ADD COLUMN {qid(name)} {typ}")
            audit(con, "ADD_COLUMN", INTENT_TABLE, name, {"type": typ})


def backfill_position_keys(con: sqlite3.Connection) -> int:
    if not table_exists(con, POSITION_TABLE):
        return 0

    c = set(cols(con, POSITION_TABLE))
    if not {"key", "symbol", "side", "setup"}.issubset(c):
        return 0

    before = con.total_changes
    con.execute(f"""
        UPDATE {qid(POSITION_TABLE)}
        SET key = UPPER(symbol) || '|' || UPPER(side) || '|' || setup
        WHERE (key IS NULL OR TRIM(key)='')
          AND symbol IS NOT NULL
          AND side IS NOT NULL
          AND setup IS NOT NULL
    """)
    changed = con.total_changes - before
    if changed:
        audit(con, "BACKFILL_KEYS", POSITION_TABLE, "key", {"rows": changed})
    return changed


def assert_no_open_key_duplicates(con: sqlite3.Connection) -> None:
    if not table_exists(con, POSITION_TABLE):
        return

    c = set(cols(con, POSITION_TABLE))
    if not {"key", "status"}.issubset(c):
        raise RuntimeError("CANONICAL_SCHEMA_MISSING_KEY_OR_STATUS")

    rows = con.execute(f"""
        SELECT key, COUNT(*) AS n
        FROM {qid(POSITION_TABLE)}
        WHERE UPPER(status)='OPEN'
          AND key IS NOT NULL
          AND TRIM(key)!=''
        GROUP BY key
        HAVING COUNT(*) > 1
    """).fetchall()

    if rows:
        payload = [{"key": r[0], "n": r[1]} for r in rows]
        audit(con, "OPEN_KEY_DUPLICATE_ABORT", POSITION_TABLE, "key", {"duplicates": payload})
        raise RuntimeError("OPEN_KEY_DUPLICATES:" + json.dumps(payload, sort_keys=True))


def ensure_indexes(con: sqlite3.Connection) -> None:
    if table_exists(con, POSITION_TABLE):
        c = set(cols(con, POSITION_TABLE))
        if {"status", "key"}.issubset(c):
            con.execute(f"CREATE INDEX IF NOT EXISTS idx_v24_7_positions_status_key ON {qid(POSITION_TABLE)} (status, key)")
        if {"status", "symbol"}.issubset(c):
            con.execute(f"CREATE INDEX IF NOT EXISTS idx_v24_7_positions_status_symbol ON {qid(POSITION_TABLE)} (status, symbol)")
        if {"intent_id"}.issubset(c):
            con.execute(f"CREATE INDEX IF NOT EXISTS idx_v24_7_positions_intent_id ON {qid(POSITION_TABLE)} (intent_id)")

    if table_exists(con, INTENT_TABLE):
        ic = set(cols(con, INTENT_TABLE))
        if {"adapter_status", "intent_state"}.issubset(ic):
            con.execute(f"CREATE INDEX IF NOT EXISTS idx_v24_7_intents_adapter_state ON {qid(INTENT_TABLE)} (adapter_status, intent_state)")


def position_notnull_columns(con: sqlite3.Connection) -> List[Dict[str, Any]]:
    return [
        r for r in table_info(con, POSITION_TABLE)
        if r["notnull"] == 1 and r["pk"] == 0 and r["dflt_value"] is None
    ]


def _latest_non_null_value(con: sqlite3.Connection, col: str) -> Any:
    return None

def _latest_non_null_value_DISABLED_FINAL(con: sqlite3.Connection, col: str) -> Any:
    try:
        if col not in cols(con, POSITION_TABLE):
            return None
        r = con.execute(
            f"""
            SELECT {qid(col)}
            FROM {qid(POSITION_TABLE)}
            WHERE {qid(col)} IS NOT NULL
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
        return r[0] if r else None
    except Exception:
        return None


def _default_for_column(con: sqlite3.Connection, name: str, typ: str, row: Dict[str, Any]) -> Any:
    n = str(name).lower()
    t = str(typ or "").upper()

    def num(*keys: str, default: float = 0.0) -> float:
        for k in keys:
            try:
                v = row.get(k)
                if v is not None:
                    return float(v)
            except Exception:
                pass
        return default

    def txt(*keys: str, default: str = "") -> str:
        for k in keys:
            v = row.get(k)
            if v is not None and str(v) != "":
                return str(v)
        return default

    if n == "family_name":
        return "V24_9_FINAL_CANONICAL_PAPER_CANARY"
    if n in {"profile", "profile_name"}:
        return "MAX_QUANT_CANARY"
    if n in {"account", "account_name"}:
        return "PAPER"
    if n in {"mode", "requested_mode"}:
        return "PAPER_MICRO_CANARY_ONLY"
    if n == "execution_permission":
        return "PAPER_ONLY_NO_REAL_EXECUTION"
    if n in {"source", "source_name"}:
        return "V24_9_FINAL_CANONICAL_INSERT_CONTRACT"
    if n in {"source_version", "schema_version"}:
        return VERSION
    if n in {"exchange", "venue", "market"}:
        return "PAPER"
    if n in {"order_id", "client_order_id", "contract_hash"}:
        return txt("stable_position_id", default="V24_9_FINAL_INSERT_CONTRACT")
    if n in {"strategy", "strategy_name"}:
        return txt("setup", default="CANONICAL")
    if n == "symbol":
        return txt("symbol", default="UNKNOWN")
    if n == "side":
        return txt("side", default="UNKNOWN")
    if n == "setup":
        return txt("setup", default="UNKNOWN")
    if n == "status":
        return txt("status", default="UNKNOWN")
    if n == "key":
        if row.get("key"):
            return str(row["key"])
        return f"{str(row.get('symbol') or 'UNKNOWN').upper()}|{str(row.get('side') or 'UNKNOWN').upper()}|{row.get('setup') or 'UNKNOWN'}"
    if n in {"ts", "opened_at", "last_managed_at", "created_at", "updated_at"}:
        return utc_now()
    if n in {"version", "adapter_version"}:
        return txt(n, default=VERSION)
    if n == "horizon_min":
        return 360
    if n == "entry_price":
        return num("entry_price", default=0.0)
    if n in {"stop_price", "stop_loss_price"}:
        return num("stop_loss_price", "stop_price", "entry_price", default=0.0)
    if n == "take_profit_price":
        return num("take_profit_price", "entry_price", default=0.0)
    if n == "initial_risk_pct":
        return num("initial_risk_pct", default=0.0045)
    if n == "size_usd":
        return num("size_usd", default=0.0)
    if n in {"pnl_usd", "pnl_r", "net_pnl_usd", "net_pnl_r", "mfe_r", "mae_r"}:
        return num(n, default=0.0)
    if n == "fee_usd_est":
        return num("fee_usd_est", default=0.0)
    if n == "slippage_usd_est":
        return num("slippage_usd_est", default=0.0)
    if n in {"control_id", "source_edge_id"}:
        try:
            return int(row.get("intent_id") or 0)
        except Exception:
            return 0
    if n == "reason":
        status = str(row.get("status") or "").upper()
        if status == "OPEN":
            return "OPENED_CANONICAL_PAPER_CANARY"
        if status == "CLOSED":
            return txt("close_reason", default="CANONICAL_POSITION_CLOSED")
        return "CANONICAL_POSITION_ROW"
    if n in {"payload", "metadata", "extra", "contract_json"}:
        return txt("payload", "metadata", "extra", "contract_json", default="{}")
    if n == "price_meta":
        return txt("price_meta", default="{}")

    if "INT" in t:
        return 0
    if any(x in t for x in ["REAL", "FLOA", "DOUB", "NUM", "DEC"]):
        return 0.0
    if "BLOB" in t:
        return b""

    return f"V24_9_FINAL_CANONICAL_DEFAULT_{name}"


def build_position_insert_row(con: sqlite3.Connection, base: Dict[str, Any]) -> Dict[str, Any]:
    row = dict(base)

    if not row.get("key") and row.get("symbol") and row.get("side") and row.get("setup"):
        row["key"] = f"{str(row['symbol']).upper()}|{str(row['side']).upper()}|{row['setup']}"

    for meta in position_notnull_columns(con):
        name = meta["name"]
        if name not in row or row.get(name) is None:
            row[name] = _default_for_column(con, name, meta.get("type") or "", row)

    return row


def insert_position_row(con: sqlite3.Connection, base: Dict[str, Any], required: Optional[List[str]] = None) -> int:
    ensure_runtime_schema(con)

    c = set(cols(con, POSITION_TABLE))
    required = required or []
    missing = [x for x in required if x not in c]
    if missing:
        raise RuntimeError(f"CANONICAL_SCHEMA_MISSING_REQUIRED_COLUMNS:{missing}")

    row = build_position_insert_row(con, base)
    data = {k: v for k, v in row.items() if k in c}

    names = list(data.keys())
    cur = con.execute(
        f"""
        INSERT INTO {qid(POSITION_TABLE)}
        ({",".join(qid(k) for k in names)})
        VALUES ({",".join("?" for _ in names)})
        """,
        [data[k] for k in names],
    )
    return int(cur.lastrowid)


def validate_position_insert_contract(con: sqlite3.Connection) -> Dict[str, Any]:
    if not table_exists(con, POSITION_TABLE):
        return {"ok": False, "error": "POSITION_TABLE_MISSING"}

    base = {
        "ts": utc_now(),
        "opened_at": utc_now(),
        "last_managed_at": utc_now(),
        "closed_at": utc_now(),
        "version": VERSION,
        "adapter_version": VERSION,
        "intent_id": -999999,
        "stable_position_id": "V24_7_SCHEMA_PROBE",
        "key": "SCHEMA_PROBE|LONG|INSERT_CONTRACT",
        "symbol": "SCHEMA_PROBE",
        "side": "LONG",
        "setup": "INSERT_CONTRACT",
        "status": "CLOSED",
        "entry_price": 1.0,
        "stop_loss_price": 0.99,
        "take_profit_price": 1.01,
        "exit_price": 1.0,
        "size_usd": 1.0,
        "requested_size_mult": 0.00001,
        "manager_state": "SCHEMA_PROBE_ROLLBACK",
        "close_reason": "SCHEMA_PROBE_ROLLBACK",
        "pnl_usd": 0.0,
        "net_pnl_usd": 0.0,
        "pnl_r": 0.0,
        "net_pnl_r": 0.0,
        "mfe_r": 0.0,
        "mae_r": 0.0,
        "price_meta": "{}",
    }

    row = build_position_insert_row(con, base)
    c = set(cols(con, POSITION_TABLE))
    data = {k: v for k, v in row.items() if k in c}
    names = list(data.keys())

    try:
        con.execute("SAVEPOINT v24_7_insert_probe")
        con.execute(
            f"""
            INSERT INTO {qid(POSITION_TABLE)}
            ({",".join(qid(k) for k in names)})
            VALUES ({",".join("?" for _ in names)})
            """,
            [data[k] for k in names],
        )
        con.execute("ROLLBACK TO v24_7_insert_probe")
        con.execute("RELEASE v24_7_insert_probe")
        return {
            "ok": True,
            "error": None,
            "inserted_columns": sorted(names),
            "notnull_defaults": {m["name"]: row.get(m["name"]) for m in position_notnull_columns(con)},
        }
    except Exception as e:
        try:
            con.execute("ROLLBACK TO v24_7_insert_probe")
            con.execute("RELEASE v24_7_insert_probe")
        except Exception:
            pass
        return {
            "ok": False,
            "error": repr(e),
            "inserted_columns": sorted(names),
            "notnull_defaults": {m["name"]: row.get(m["name"]) for m in position_notnull_columns(con)},
        }


def ensure_runtime_schema(con: sqlite3.Connection) -> Dict[str, Any]:
    create_audit_table(con)
    ensure_position_table(con)
    ensure_intent_table_columns(con)
    backfilled = backfill_position_keys(con)
    assert_no_open_key_duplicates(con)
    ensure_indexes(con)
    report = schema_report(con)
    audit(con, "ENSURE_RUNTIME_SCHEMA_OK", "ALL", None, report | {"backfilled_keys": backfilled})
    return report


def schema_report(con: sqlite3.Connection) -> Dict[str, Any]:
    position_cols = set(cols(con, POSITION_TABLE))
    intent_cols = set(cols(con, INTENT_TABLE)) if table_exists(con, INTENT_TABLE) else set()

    missing_position = sorted([c for c in POSITION_COLUMNS if c not in position_cols])
    missing_intent = sorted([c for c in INTENT_COLUMNS if c not in intent_cols]) if intent_cols else sorted(INTENT_COLUMNS)

    duplicate_open_keys = []
    if table_exists(con, POSITION_TABLE) and {"key", "status"}.issubset(position_cols):
        duplicate_open_keys = [
            {"key": r[0], "n": r[1]}
            for r in con.execute(f"""
                SELECT key, COUNT(*) AS n
                FROM {qid(POSITION_TABLE)}
                WHERE UPPER(status)='OPEN'
                  AND key IS NOT NULL
                  AND TRIM(key)!=''
                GROUP BY key
                HAVING COUNT(*) > 1
            """).fetchall()
        ]

    insert_contract = validate_position_insert_contract(con)

    return {
        "version": VERSION,
        "position_table": POSITION_TABLE,
        "intent_table": INTENT_TABLE,
        "position_schema_ok": not missing_position,
        "intent_schema_ok": not missing_intent if intent_cols else False,
        "missing_position_columns": missing_position,
        "missing_intent_columns": missing_intent,
        "duplicate_open_keys": duplicate_open_keys,
        "position_notnull_columns": position_notnull_columns(con),
        "position_insert_contract_ok": bool(insert_contract.get("ok")),
        "position_insert_contract_error": insert_contract.get("error"),
        "position_insert_defaults": insert_contract.get("notnull_defaults"),
        "schema_ok": (not missing_position) and (not duplicate_open_keys) and bool(insert_contract.get("ok")),
    }


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="/storage/emulated/0/Download/joan_crypto_ai_pro_v14/data/joanbot_v14.sqlite")
    args = ap.parse_args()

    con = sqlite3.connect(args.db, timeout=30, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=30000")
    con.execute("PRAGMA journal_mode=WAL")
    report = ensure_runtime_schema(con)
    print(json.dumps(report, indent=2, sort_keys=True))
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
