#!/usr/bin/env python3
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

DB = Path("data/joanbot_v14.sqlite")
OUT = Path("data/v18_9_5_liquidation_binding")

VERSION = "V18.9.5_CANONICAL_LIQUIDATION_BINDING"

LATEST = "institutional_market_data_latest_v18_9"
HISTORY = "institutional_market_data_history_v18_9"
HEALTH = "institutional_market_data_health_v18_9"
AUDIT = "institutional_market_data_semantic_audit_v18_9_1"
AUDIT_ARCHIVE = "institutional_market_data_semantic_audit_archive_v18_9_5"

LIQ_ROLLUP = "institutional_liquidation_rollup_latest_v18_10"
LIQ_HEALTH = "institutional_liquidation_collector_health_v18_10"

SYMBOLS = {
    "BTCUSDT": "BTC_LIQUIDATIONS",
    "ETHUSDT": "ETH_LIQUIDATIONS",
}

CORE = [
    "BTC_PRICE", "ETH_PRICE",
    "BTC_CHANGE_24H", "ETH_CHANGE_24H",
    "BTC_FUNDING", "ETH_FUNDING",
    "BTC_OI", "ETH_OI",
    "BTC_LONG_SHORT", "ETH_LONG_SHORT",
    "BTC_CVD", "ETH_CVD",
    "BTC_LIQUIDATIONS", "ETH_LIQUIDATIONS",
    "VIX", "DXY", "NASDAQ", "NASDAQ_CHANGE", "US10Y", "FEAR_GREED",
]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def qid(x: str) -> str:
    return '"' + str(x).replace('"', '""') + '"'


def fnum(x: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if x is None or x == "":
            return default
        return float(str(x).replace(",", ""))
    except Exception:
        return default


def parse_ts(ts: Any):
    if not ts:
        return None
    try:
        s = str(ts).replace("Z", "+00:00")
        d = datetime.fromisoformat(s)
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.astimezone(timezone.utc)
    except Exception:
        return None


def age_min(ts: Any) -> Optional[float]:
    d = parse_ts(ts)
    if not d:
        return None
    return max(0.0, (datetime.now(timezone.utc) - d).total_seconds() / 60.0)


def connect():
    con = sqlite3.connect(DB, timeout=90, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=90000")
    try:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=NORMAL")
    except Exception:
        pass
    return con


def exists(con, table: str) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone() is not None


def colset(con, table: str):
    if not exists(con, table):
        return set()
    return {r[1] for r in con.execute(f"PRAGMA table_info({qid(table)})")}


def add_col(con, table: str, name: str, spec: str):
    if name not in colset(con, table):
        con.execute(f"ALTER TABLE {qid(table)} ADD COLUMN {qid(name)} {spec}")


def ensure_tables(con):
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
        CREATE TABLE IF NOT EXISTS {qid(AUDIT_ARCHIVE)} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            archived_at TEXT NOT NULL,
            original_id INTEGER,
            ts TEXT,
            metric TEXT,
            rejected_value TEXT,
            reason TEXT,
            source TEXT,
            source_detail TEXT,
            payload TEXT,
            archive_reason TEXT
        )
    """)

    for c, s in {
        "value_text": "TEXT",
        "age_min": "REAL",
        "stale_limit_min": "REAL",
        "quality": "REAL",
        "source": "TEXT",
        "source_detail": "TEXT",
        "payload": "TEXT",
    }.items():
        add_col(con, LATEST, c, s)
        add_col(con, HISTORY, c, s)

    for c, s in {
        "invalid_count": "INTEGER DEFAULT 0",
        "error_count": "INTEGER DEFAULT 0",
        "payload": "TEXT",
    }.items():
        add_col(con, HEALTH, c, s)


def emit_market(con, metric: str, value: Optional[float], status: str, source: str, source_detail: str, payload: Dict[str, Any], ts: Optional[str] = None):
    ts = ts or now_iso()
    a = age_min(ts)
    quality = 1.0 if status == "LIVE" else 0.0

    con.execute(f"""
        INSERT OR REPLACE INTO {qid(LATEST)}
        (metric, scope, ts, value, value_text, status, age_min, stale_limit_min, quality, source, source_detail, payload)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        metric,
        "GLOBAL" if metric in ("VIX", "DXY", "NASDAQ", "NASDAQ_CHANGE", "US10Y", "FEAR_GREED") else metric.split("_")[0] + "USDT",
        ts,
        value,
        None if value is None else str(value),
        status,
        a,
        3.0,
        quality,
        source,
        source_detail,
        json.dumps(payload, sort_keys=True),
    ))

    con.execute(f"""
        INSERT INTO {qid(HISTORY)}
        (metric, scope, ts, value, value_text, status, age_min, stale_limit_min, quality, source, source_detail, payload)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        metric,
        "GLOBAL" if metric in ("VIX", "DXY", "NASDAQ", "NASDAQ_CHANGE", "US10Y", "FEAR_GREED") else metric.split("_")[0] + "USDT",
        ts,
        value,
        None if value is None else str(value),
        status,
        a,
        3.0,
        quality,
        source,
        source_detail,
        json.dumps(payload, sort_keys=True),
    ))


def canonical_liquidation_bind(con):
    results = []

    if not exists(con, LIQ_ROLLUP):
        for sym, metric in SYMBOLS.items():
            emit_market(
                con,
                metric,
                None,
                "MISS",
                "liquidation_collector_v18_10",
                f"{sym}.rollup_table_missing",
                {"reason": "rollup_table_missing"},
            )
            results.append({"metric": metric, "status": "MISS", "reason": "rollup_table_missing"})
        return results

    for sym, metric in SYMBOLS.items():
        r = con.execute(f"""
            SELECT *
            FROM {qid(LIQ_ROLLUP)}
            WHERE symbol=?
            LIMIT 1
        """, (sym,)).fetchone()

        if not r:
            emit_market(
                con,
                metric,
                None,
                "MISS",
                "liquidation_collector_v18_10",
                f"{sym}.rollup_missing",
                {"reason": "symbol_rollup_missing", "symbol": sym},
            )
            results.append({"metric": metric, "status": "MISS", "reason": "symbol_rollup_missing"})
            continue

        row = dict(r)
        state = str(row.get("connection_state") or "")
        value = fnum(row.get("total_15m_usd"), 0.0)
        ts = row.get("ts") or now_iso()
        a = age_min(ts)

        payload = {
            "version": VERSION,
            "symbol": sym,
            "collector_row": row,
            "interpretation": "0.0 is valid when collector is live; it means no liquidation events in the 15m window.",
        }

        if "LIVE" in state and value is not None and value >= 0 and (a is None or a <= 3.0):
            emit_market(
                con,
                metric,
                value,
                "LIVE",
                "liquidation_collector_v18_10_canonical",
                f"{sym}.total_15m_usd.zero_allowed",
                payload,
                ts=ts,
            )
            results.append({"metric": metric, "status": "LIVE", "value": value, "reason": "canonical_zero_allowed"})
        elif "LIVE" in state and value is not None and value >= 0:
            emit_market(
                con,
                metric,
                value,
                "STALE",
                "liquidation_collector_v18_10_canonical",
                f"{sym}.total_15m_usd.stale_rollup",
                payload,
                ts=ts,
            )
            results.append({"metric": metric, "status": "STALE", "value": value, "reason": "stale_rollup"})
        elif value is not None and value < 0:
            emit_market(
                con,
                metric,
                None,
                "INVALID",
                "liquidation_collector_v18_10_canonical",
                f"{sym}.negative_liquidation_value",
                payload,
                ts=ts,
            )
            results.append({"metric": metric, "status": "INVALID", "value": value, "reason": "negative_value"})
        else:
            emit_market(
                con,
                metric,
                None,
                "MISS",
                "liquidation_collector_v18_10_canonical",
                f"{sym}.collector_not_live",
                payload,
                ts=ts,
            )
            results.append({"metric": metric, "status": "MISS", "reason": "collector_not_live", "state": state})

    return results


def archive_false_positive_liq_rejects(con):
    if not exists(con, AUDIT):
        return 0

    rows = con.execute(f"""
        SELECT id, ts, metric, rejected_value, reason, source, source_detail, payload
        FROM {qid(AUDIT)}
        WHERE metric IN ('BTC_LIQUIDATIONS','ETH_LIQUIDATIONS')
          AND reason='LIQUIDATION_REQUIRES_LIQ_SOURCE'
          AND CAST(COALESCE(rejected_value,'0') AS REAL)=0.0
    """).fetchall()

    for r in rows:
        d = dict(r)
        con.execute(f"""
            INSERT INTO {qid(AUDIT_ARCHIVE)}
            (archived_at, original_id, ts, metric, rejected_value, reason, source, source_detail, payload, archive_reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            now_iso(),
            d.get("id"),
            d.get("ts"),
            d.get("metric"),
            d.get("rejected_value"),
            d.get("reason"),
            d.get("source"),
            d.get("source_detail"),
            d.get("payload"),
            "FALSE_POSITIVE_ZERO_LIQUIDATION_WITH_CANONICAL_COLLECTOR",
        ))

    con.execute(f"""
        DELETE FROM {qid(AUDIT)}
        WHERE metric IN ('BTC_LIQUIDATIONS','ETH_LIQUIDATIONS')
          AND reason='LIQUIDATION_REQUIRES_LIQ_SOURCE'
          AND CAST(COALESCE(rejected_value,'0') AS REAL)=0.0
    """)

    return len(rows)


def reconcile_health(con, binding_results, archived: int):
    rows = con.execute(f"""
        SELECT metric, status, value, age_min, source, source_detail
        FROM {qid(LATEST)}
    """).fetchall()

    by_metric = {r["metric"]: dict(r) for r in rows}

    live = sum(1 for r in rows if r["status"] == "LIVE")
    stale = sum(1 for r in rows if r["status"] == "STALE")
    miss = sum(1 for r in rows if r["status"] == "MISS")
    invalid = sum(1 for r in rows if r["status"] == "INVALID")

    core_missing = []
    core_invalid = []

    for m in CORE:
        r = by_metric.get(m)
        if not r or r.get("status") in (None, "MISS"):
            core_missing.append(m)
        elif r.get("status") == "INVALID":
            core_invalid.append(m)

    if core_invalid:
        summary = "BAD_CORE_INVALID"
    elif core_missing:
        summary = "DEGRADED_CORE_MISSING"
    elif invalid:
        summary = "DEGRADED_INVALID_NONCORE"
    elif stale:
        summary = "DEGRADED_STALE_NONCORE"
    elif miss:
        summary = "DEGRADED_NONCORE_MISSING"
    else:
        summary = "OK"

    payload = {
        "version": VERSION,
        "binding_results": binding_results,
        "archived_false_positive_liq_rejects": archived,
        "core_missing": core_missing,
        "core_invalid": core_invalid,
        "counts": {
            "live": live,
            "stale": stale,
            "miss": miss,
            "invalid": invalid,
        },
    }

    con.execute(f"""
        INSERT INTO {qid(HEALTH)}
        (ts, version, live_count, stale_count, miss_count, invalid_count, error_count, summary, payload)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        now_iso(),
        VERSION,
        live,
        stale,
        miss,
        invalid,
        0,
        summary,
        json.dumps(payload, sort_keys=True),
    ))

    return {
        "summary": summary,
        "live": live,
        "stale": stale,
        "miss": miss,
        "invalid": invalid,
        "core_missing": core_missing,
        "core_invalid": core_invalid,
    }


def main():
    OUT.mkdir(parents=True, exist_ok=True)

    if not DB.exists():
        raise SystemExit("DB_MISSING")

    con = connect()
    ensure_tables(con)

    qc = con.execute("PRAGMA quick_check").fetchone()[0]

    binding = canonical_liquidation_bind(con)
    archived = archive_false_positive_liq_rejects(con)
    health = reconcile_health(con, binding, archived)

    report = {
        "utc": now_iso(),
        "version": VERSION,
        "quick_check": qc,
        "binding": binding,
        "archived_false_positive_liq_rejects": archived,
        "health": health,
    }

    (OUT / "canonical_liquidation_binding_report.json").write_text(json.dumps(report, indent=2, sort_keys=True))

    print("===== V18.9.5 CANONICAL LIQUIDATION BINDING =====")
    print("quick_check:", qc)
    print("binding:", binding)
    print("archived_false_positive_liq_rejects:", archived)
    print("health:", health)

    con.close()

    if qc != "ok":
        return 2
    if health["summary"].startswith("BAD"):
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
