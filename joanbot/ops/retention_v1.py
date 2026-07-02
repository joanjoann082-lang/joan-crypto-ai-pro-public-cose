from __future__ import annotations

import argparse
import json
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any

VERSION = "RETENTION_V1_INSTITUTIONAL"
DB_PATH = Path("data/joanbot_v14.sqlite")
LOCK_PATH = Path("data/locks/retention_v1.lock")
REPORT_PATH = Path("data/reports/retention_v1_last.json")

# Només taules operatives inflables. No tocar resultats econòmics.
LIMITS: Dict[str, int] = {
    "alerts": 300,
    "decisions": 3000,
    "market_snapshots": 3000,
    "derivatives_snapshots": 3000,
    "orderflow_snapshots": 3000,
    "features": 3000,
    "forward_cases": 5000,
    "forward_results": 5000,
    "runtime_events": 500,
    "news_events": 500,
    "candles": 10000,
}

PROTECTED_TABLES = {
    "positions",
    "trades",
    "position_events",
    "edge_memory",
    "state_integrity_events",
    "runtime_control_audit",
    "sqlite_sequence",
}

class RetentionRefused(RuntimeError):
    pass

def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()

def now_ts() -> float:
    return time.time()

def connect(db_path: Path = DB_PATH) -> sqlite3.Connection:
    con = sqlite3.connect(str(db_path), timeout=30)
    con.row_factory = sqlite3.Row
    return con

def table_exists(con: sqlite3.Connection, table: str) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone() is not None

def count_rows(con: sqlite3.Connection, table: str) -> int:
    if not table_exists(con, table):
        return 0
    return int(con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])

def acquire_lock(ttl_sec: int = 600) -> None:
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    if LOCK_PATH.exists():
        try:
            data = json.loads(LOCK_PATH.read_text(errors="ignore") or "{}")
            age = now_ts() - float(data.get("ts", 0))
            if age < ttl_sec:
                raise RetentionRefused(f"RETENTION_LOCK_ACTIVE age_sec={age:.1f}")
        except RetentionRefused:
            raise
        except Exception:
            pass

    LOCK_PATH.write_text(
        json.dumps({"ts": now_ts(), "pid": os.getpid(), "version": VERSION}, sort_keys=True),
        encoding="utf-8",
    )

def release_lock() -> None:
    try:
        LOCK_PATH.unlink(missing_ok=True)
    except Exception:
        pass

def prune_table(con: sqlite3.Connection, table: str, limit: int, apply: bool) -> Dict[str, Any]:
    if table in PROTECTED_TABLES:
        raise RetentionRefused(f"REFUSE_PROTECTED_TABLE: {table}")

    if table not in LIMITS:
        raise RetentionRefused(f"REFUSE_UNKNOWN_TABLE: {table}")

    if not table_exists(con, table):
        return {
            "table": table,
            "missing": True,
            "limit": limit,
            "before": 0,
            "after": 0,
            "planned_delete": 0,
            "deleted": 0,
        }

    before = count_rows(con, table)
    planned = max(0, before - limit)

    if apply and planned > 0:
        con.execute(
            f"""
            DELETE FROM {table}
            WHERE rowid NOT IN (
                SELECT rowid FROM {table}
                ORDER BY rowid DESC
                LIMIT ?
            )
            """,
            (limit,),
        )

    after = count_rows(con, table) if apply else before

    return {
        "table": table,
        "limit": limit,
        "before": before,
        "after": after,
        "planned_delete": planned,
        "deleted": before - after if apply else 0,
    }

def write_runtime_event(con: sqlite3.Connection, level: str, message: str, payload: Dict[str, Any]) -> None:
    if not table_exists(con, "runtime_events"):
        return
    con.execute(
        "INSERT INTO runtime_events(ts,component,level,message,payload) VALUES(?,?,?,?,?)",
        (utc_now(), "retention", level, message, json.dumps(payload, sort_keys=True)),
    )

def run_retention(db_path: Path = DB_PATH, apply: bool = False) -> Dict[str, Any]:
    acquire_lock()
    con = connect(db_path)
    con.execute("PRAGMA busy_timeout=30000;")

    report: Dict[str, Any] = {
        "version": VERSION,
        "ts": utc_now(),
        "db_path": str(db_path),
        "apply": apply,
        "limits": LIMITS,
        "protected_tables": sorted(PROTECTED_TABLES),
        "tables": [],
        "total_planned_delete": 0,
        "total_deleted": 0,
        "db_size_mb": round(db_path.stat().st_size / 1024 / 1024, 2) if db_path.exists() else 0,
        "manual_compact_needed": False,
    }

    try:
        for table, limit in LIMITS.items():
            row = prune_table(con, table, limit, apply)
            report["tables"].append(row)
            report["total_planned_delete"] += int(row.get("planned_delete", 0))
            report["total_deleted"] += int(row.get("deleted", 0))

        if apply:
            write_runtime_event(con, "INFO", "RETENTION_V1_APPLIED", {
                "version": VERSION,
                "total_deleted": report["total_deleted"],
                "db_size_mb": report["db_size_mb"],
            })

            # Després d'escriure runtime_event, torna a respectar límit.
            if table_exists(con, "runtime_events"):
                prune_table(con, "runtime_events", LIMITS["runtime_events"], True)

            con.commit()
            try:
                con.execute("PRAGMA wal_checkpoint(TRUNCATE);")
                con.execute("PRAGMA optimize;")
                con.commit()
            except sqlite3.OperationalError as e:
                report["checkpoint_warning"] = repr(e)
        else:
            con.rollback()

        if db_path.exists() and db_path.stat().st_size > 5 * 1024 * 1024 * 1024:
            report["manual_compact_needed"] = True

    except Exception as e:
        con.rollback()
        report["error"] = repr(e)
        raise
    finally:
        con.close()
        release_lock()

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = REPORT_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(REPORT_PATH)

    return report

def run_retention_safe(apply: bool = True) -> bool:
    try:
        run_retention(DB_PATH, apply=apply)
        return True
    except Exception:
        return False

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--db", default=str(DB_PATH))
    args = ap.parse_args()

    r = run_retention(Path(args.db), apply=args.apply)
    print("RETENTION_VERSION:", r["version"])
    print("APPLY:", r["apply"])
    print("DB_SIZE_MB:", r["db_size_mb"])
    print("TOTAL_PLANNED_DELETE:", r["total_planned_delete"])
    print("TOTAL_DELETED:", r["total_deleted"])
    print("MANUAL_COMPACT_NEEDED:", r["manual_compact_needed"])
    print("REPORT:", REPORT_PATH)

    for row in r["tables"]:
        print(
            row["table"],
            "before=", row.get("before"),
            "after=", row.get("after"),
            "limit=", row.get("limit"),
            "deleted=", row.get("deleted"),
        )

if __name__ == "__main__":
    main()
