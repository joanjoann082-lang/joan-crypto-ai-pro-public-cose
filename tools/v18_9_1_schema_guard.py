#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB = Path("data/joanbot_v14.sqlite")
OUT = Path("data/v18_9_1_data_plane")
BACKUPS = Path("data/backups/schema_migrations")

LATEST = "institutional_market_data_latest_v18_9"
HISTORY = "institutional_market_data_history_v18_9"
HEALTH = "institutional_market_data_health_v18_9"
AUDIT = "institutional_market_data_semantic_audit_v18_9_1"

VERSION = "V18.9.1_SCHEMA_GUARD"


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def qid(x: str) -> str:
    return '"' + str(x).replace('"', '""') + '"'


def connect():
    con = sqlite3.connect(DB, timeout=20)
    con.row_factory = sqlite3.Row
    return con


def table_exists(con, table: str) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone() is not None


def cols(con, table: str):
    if not table_exists(con, table):
        return {}
    return {
        r[1]: {
            "type": r[2],
            "notnull": r[3],
            "default": r[4],
            "pk": r[5],
        }
        for r in con.execute(f"PRAGMA table_info({qid(table)})")
    }


def create_base_tables(con):
    con.execute(f"""
        CREATE TABLE IF NOT EXISTS {qid(LATEST)} (
            metric TEXT NOT NULL,
            scope TEXT NOT NULL,
            ts TEXT NOT NULL,
            value REAL,
            value_text TEXT,
            status TEXT NOT NULL DEFAULT 'MISS',
            age_min REAL,
            stale_limit_min REAL,
            quality REAL,
            source TEXT,
            source_detail TEXT,
            payload TEXT,
            PRIMARY KEY(metric, scope)
        )
    """)

    con.execute(f"""
        CREATE TABLE IF NOT EXISTS {qid(HISTORY)} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            metric TEXT NOT NULL,
            scope TEXT NOT NULL,
            ts TEXT NOT NULL,
            value REAL,
            value_text TEXT,
            status TEXT NOT NULL DEFAULT 'MISS',
            age_min REAL,
            stale_limit_min REAL,
            quality REAL,
            source TEXT,
            source_detail TEXT,
            payload TEXT
        )
    """)

    con.execute(f"""
        CREATE TABLE IF NOT EXISTS {qid(HEALTH)} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            version TEXT NOT NULL,
            live_count INTEGER DEFAULT 0,
            stale_count INTEGER DEFAULT 0,
            miss_count INTEGER DEFAULT 0,
            invalid_count INTEGER DEFAULT 0,
            error_count INTEGER DEFAULT 0,
            summary TEXT,
            payload TEXT
        )
    """)

    con.execute(f"""
        CREATE TABLE IF NOT EXISTS {qid(AUDIT)} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            metric TEXT,
            rejected_value TEXT,
            reason TEXT,
            source TEXT,
            source_detail TEXT,
            payload TEXT
        )
    """)


EXPECTED = {
    LATEST: {
        "metric": "TEXT",
        "scope": "TEXT",
        "ts": "TEXT",
        "value": "REAL",
        "value_text": "TEXT",
        "status": "TEXT DEFAULT 'MISS'",
        "age_min": "REAL",
        "stale_limit_min": "REAL",
        "quality": "REAL",
        "source": "TEXT",
        "source_detail": "TEXT",
        "payload": "TEXT",
    },
    HISTORY: {
        "metric": "TEXT",
        "scope": "TEXT",
        "ts": "TEXT",
        "value": "REAL",
        "value_text": "TEXT",
        "status": "TEXT DEFAULT 'MISS'",
        "age_min": "REAL",
        "stale_limit_min": "REAL",
        "quality": "REAL",
        "source": "TEXT",
        "source_detail": "TEXT",
        "payload": "TEXT",
    },
    HEALTH: {
        "ts": "TEXT",
        "version": "TEXT",
        "live_count": "INTEGER DEFAULT 0",
        "stale_count": "INTEGER DEFAULT 0",
        "miss_count": "INTEGER DEFAULT 0",
        "invalid_count": "INTEGER DEFAULT 0",
        "error_count": "INTEGER DEFAULT 0",
        "summary": "TEXT",
        "payload": "TEXT",
    },
    AUDIT: {
        "ts": "TEXT",
        "metric": "TEXT",
        "rejected_value": "TEXT",
        "reason": "TEXT",
        "source": "TEXT",
        "source_detail": "TEXT",
        "payload": "TEXT",
    },
}


def missing_columns(con):
    missing = {}
    for table, expected in EXPECTED.items():
        existing = cols(con, table)
        table_missing = []
        for name, spec in expected.items():
            if name not in existing:
                table_missing.append((name, spec))
        if table_missing:
            missing[table] = table_missing
    return missing


def safe_backup_if_needed(con, missing):
    if not missing:
        return None

    marker = OUT / "schema_guard_backup_done.marker"
    if marker.exists():
        return "SKIPPED_ALREADY_DONE"

    if not DB.exists():
        return "SKIPPED_DB_MISSING"

    BACKUPS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    backup = BACKUPS / f"joanbot_v14_before_v18_9_1_schema_{stamp}.sqlite"

    db_size = DB.stat().st_size
    free = shutil.disk_usage(DB.parent).free

    if free < db_size + 700 * 1024 * 1024:
        marker.write_text("backup skipped: insufficient free space\n")
        return "SKIPPED_LOW_SPACE"

    dst = sqlite3.connect(str(backup))
    con.backup(dst)
    dst.close()

    marker.write_text(str(backup) + "\n")
    return str(backup)


def add_missing_columns(con, missing):
    applied = []

    for table, items in missing.items():
        for name, spec in items:
            sql = f"ALTER TABLE {qid(table)} ADD COLUMN {qid(name)} {spec}"
            con.execute(sql)
            applied.append({"table": table, "column": name, "spec": spec})

    return applied


def normalize_existing_health(con):
    if not table_exists(con, HEALTH):
        return

    c = cols(con, HEALTH)
    updates = []

    if "invalid_count" in c:
        con.execute(f"UPDATE {qid(HEALTH)} SET invalid_count=0 WHERE invalid_count IS NULL")
        updates.append("invalid_count_null_to_0")

    for col in ["live_count", "stale_count", "miss_count", "error_count"]:
        if col in c:
            con.execute(f"UPDATE {qid(HEALTH)} SET {qid(col)}=0 WHERE {qid(col)} IS NULL")
            updates.append(f"{col}_null_to_0")

    return updates


def main():
    OUT.mkdir(parents=True, exist_ok=True)

    if not DB.exists():
        raise SystemExit("DB_MISSING")

    con = connect()
    qc_before = con.execute("PRAGMA quick_check").fetchone()[0]

    con.execute("BEGIN IMMEDIATE")
    create_base_tables(con)

    missing = missing_columns(con)
    backup = safe_backup_if_needed(con, missing)
    applied = add_missing_columns(con, missing)
    normalized = normalize_existing_health(con)

    con.commit()

    qc_after = con.execute("PRAGMA quick_check").fetchone()[0]

    report = {
        "version": VERSION,
        "utc": now_iso(),
        "quick_check_before": qc_before,
        "quick_check_after": qc_after,
        "backup": backup,
        "missing_before": {
            k: [{"column": c, "spec": s} for c, s in v]
            for k, v in missing.items()
        },
        "applied": applied,
        "normalized": normalized,
        "final_columns": {
            table: list(cols(con, table).keys())
            for table in [LATEST, HISTORY, HEALTH, AUDIT]
        },
    }

    (OUT / "schema_guard_report.json").write_text(json.dumps(report, indent=2, sort_keys=True))

    print("===== V18.9.1 SCHEMA GUARD =====")
    print("quick_check_before:", qc_before)
    print("quick_check_after:", qc_after)
    print("backup:", backup)
    print("applied:", applied if applied else "none")
    print("normalized:", normalized if normalized else "none")
    print("health_columns:", report["final_columns"][HEALTH])

    con.close()

    if qc_after != "ok":
        return 2

    if "invalid_count" not in report["final_columns"][HEALTH]:
        print("ERROR: invalid_count still missing")
        return 3

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
