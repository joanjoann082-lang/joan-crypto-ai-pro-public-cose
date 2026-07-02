#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional

from joanbot.institutional.canonical_paper_accounting_v24_4 import (
    POSITION_TABLE,
    START_EQUITY,
    canonical_exit_price,
    canonical_price,
    compute_pnl,
    fnum,
    qid,
    safe_json,
    stop_take_prices,
    trigger_state,
    utc_now,
)
from joanbot.institutional.canonical_schema_v24_6 import (
    ensure_runtime_schema,
    assert_no_open_key_duplicates,
    insert_position_row,
)
from joanbot.institutional.kernel_contract_v24_6 import ADAPTER_CAPS

ROOT = Path("/storage/emulated/0/Download/joan_crypto_ai_pro_v14")
DB = ROOT / "data" / "joanbot_v14.sqlite"
OUT = ROOT / "data" / "v24_4_accounting_core"
OUT.mkdir(parents=True, exist_ok=True)

VERSION = "V24_4_CANONICAL_PAPER_ADAPTER_V24_6D_TRANSACTIONAL_CORE"

INTENT_TABLE = "institutional_quant_canary_execution_intents_v17_7_2"
AUDIT_TABLE = "institutional_v24_4_canonical_adapter_audit"
HEALTH_TABLE = "institutional_v24_4_canonical_adapter_health"

VALID_MODE = "PAPER_MICRO_CANARY_ONLY"
VALID_PERMISSION = "PAPER_ONLY_NO_REAL_EXECUTION"

PENDING_STATES = {
    "MAX_QUANT_MANUAL_APPROVED_PENDING_PAPER_ADAPTER",
}

PENDING_STATUS = {
    "PENDING_ADAPTER_BINDING",
}

OPEN_STATUS = "OPEN"
CLOSED_STATUS = "CLOSED"


def connect() -> sqlite3.Connection:
    con = sqlite3.connect(str(DB), timeout=60, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=60000")
    con.execute("PRAGMA journal_mode=WAL")
    return con


def table_exists(con: sqlite3.Connection, table: str) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone() is not None


def cols(con: sqlite3.Connection, table: str) -> List[str]:
    if not table_exists(con, table):
        return []
    return [r[1] for r in con.execute(f"PRAGMA table_info({qid(table)})")]


def create_tables(con: sqlite3.Connection) -> None:
    con.execute(f"""
    CREATE TABLE IF NOT EXISTS {qid(AUDIT_TABLE)} (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT,
        version TEXT,
        event_type TEXT,
        intent_id INTEGER,
        position_row_id INTEGER,
        key TEXT,
        symbol TEXT,
        side TEXT,
        setup TEXT,
        reason TEXT,
        payload TEXT
    )
    """)

    con.execute(f"""
    CREATE TABLE IF NOT EXISTS {qid(HEALTH_TABLE)} (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT,
        version TEXT,
        quick_check TEXT,
        pending_intents INTEGER,
        opened_positions INTEGER,
        managed_positions INTEGER,
        closed_positions INTEGER,
        rejected_intents INTEGER,
        errors INTEGER,
        payload TEXT
    )
    """)


def audit(
    con: sqlite3.Connection,
    event_type: str,
    intent_id: Optional[int] = None,
    position_row_id: Optional[int] = None,
    key: Optional[str] = None,
    symbol: Optional[str] = None,
    side: Optional[str] = None,
    setup: Optional[str] = None,
    reason: Optional[str] = None,
    payload: Optional[Dict[str, Any]] = None,
) -> None:
    con.execute(
        f"""
        INSERT INTO {qid(AUDIT_TABLE)}
        (ts, version, event_type, intent_id, position_row_id, key, symbol, side, setup, reason, payload)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            utc_now(),
            VERSION,
            event_type,
            intent_id,
            position_row_id,
            key,
            symbol,
            side,
            setup,
            reason,
            safe_json(payload or {}),
        ),
    )


def update_dynamic(con: sqlite3.Connection, table: str, row_id: int, values: Dict[str, Any]) -> None:
    existing = set(cols(con, table))
    data = {k: v for k, v in values.items() if k in existing}
    if not data:
        raise RuntimeError(f"NO_COLUMNS_TO_UPDATE:{table}:{sorted(values)}")

    names = list(data.keys())
    con.execute(
        f"""
        UPDATE {qid(table)}
        SET {",".join(qid(k) + "=?" for k in names)}
        WHERE id=?
        """,
        [data[k] for k in names] + [row_id],
    )


def insert_dynamic(con: sqlite3.Connection, table: str, values: Dict[str, Any], required: Optional[List[str]] = None) -> int:
    existing = set(cols(con, table))
    required = required or []

    missing_required = [k for k in required if k not in existing]
    if missing_required:
        raise RuntimeError(f"CANONICAL_SCHEMA_MISSING_REQUIRED_COLUMNS:{table}:{missing_required}")

    data = {k: v for k, v in values.items() if k in existing}
    if not data:
        raise RuntimeError(f"NO_COLUMNS_TO_INSERT:{table}")

    names = list(data.keys())
    cur = con.execute(
        f"""
        INSERT INTO {qid(table)}
        ({",".join(qid(k) for k in names)})
        VALUES ({",".join("?" for _ in names)})
        """,
        [data[k] for k in names],
    )
    return int(cur.lastrowid)


def make_key(symbol: str, side: str, setup: str) -> str:
    return f"{str(symbol).upper()}|{str(side).upper()}|{setup}"


def pending_intents(con: sqlite3.Connection) -> List[Dict[str, Any]]:
    if not table_exists(con, INTENT_TABLE):
        return []

    c = set(cols(con, INTENT_TABLE))
    required = {
        "id",
        "version",
        "intent_state",
        "requested_mode",
        "execution_permission",
        "adapter_status",
        "symbol",
        "side",
        "setup",
        "requested_size_mult",
    }
    missing = sorted(required - c)
    if missing:
        raise RuntimeError(f"INTENT_SCHEMA_MISSING_REQUIRED:{missing}")

    rows = con.execute(
        f"""
        SELECT *
        FROM {qid(INTENT_TABLE)}
        WHERE version LIKE 'V24_%'
          AND intent_state IN ({",".join("?" for _ in PENDING_STATES)})
          AND adapter_status IN ({",".join("?" for _ in PENDING_STATUS)})
          AND requested_mode=?
          AND execution_permission=?
        ORDER BY id ASC
        LIMIT 10
        """,
        list(PENDING_STATES) + list(PENDING_STATUS) + [VALID_MODE, VALID_PERMISSION],
    ).fetchall()

    return [dict(r) for r in rows]


def open_positions(con: sqlite3.Connection) -> List[Dict[str, Any]]:
    if not table_exists(con, POSITION_TABLE):
        return []
    return [
        dict(r)
        for r in con.execute(
            f"""
            SELECT *
            FROM {qid(POSITION_TABLE)}
            WHERE UPPER(status)='OPEN'
            ORDER BY id ASC
            """
        ).fetchall()
    ]


def count_open_global(con: sqlite3.Connection) -> int:
    return int(
        con.execute(
            f"""
            SELECT COUNT(*)
            FROM {qid(POSITION_TABLE)}
            WHERE UPPER(status)='OPEN'
            """
        ).fetchone()[0]
    )


def count_open_symbol(con: sqlite3.Connection, symbol: str) -> int:
    return int(
        con.execute(
            f"""
            SELECT COUNT(*)
            FROM {qid(POSITION_TABLE)}
            WHERE UPPER(status)='OPEN'
              AND UPPER(symbol)=?
            """,
            (str(symbol).upper(),),
        ).fetchone()[0]
    )


def count_open_key(con: sqlite3.Connection, key: str) -> int:
    return int(
        con.execute(
            f"""
            SELECT COUNT(*)
            FROM {qid(POSITION_TABLE)}
            WHERE UPPER(status)='OPEN'
              AND key=?
            """,
            (key,),
        ).fetchone()[0]
    )


def daily_valid_opens(con: sqlite3.Connection) -> int:
    if not table_exists(con, AUDIT_TABLE):
        return 0
    return int(
        con.execute(
            f"""
            SELECT COUNT(*)
            FROM {qid(AUDIT_TABLE)}
            WHERE substr(ts,1,10)=substr(?,1,10)
              AND event_type='OPENED_PAPER_CANARY'
            """,
            (utc_now(),),
        ).fetchone()[0]
    )


def reject_intent(
    con: sqlite3.Connection,
    intent: Dict[str, Any],
    reason: str,
    payload: Optional[Dict[str, Any]] = None,
) -> str:
    iid = int(intent["id"])
    symbol = str(intent.get("symbol") or "").upper()
    side = str(intent.get("side") or "").upper()
    setup = str(intent.get("setup") or "")
    key = str(intent.get("key") or make_key(symbol, side, setup))

    update_dynamic(
        con,
        INTENT_TABLE,
        iid,
        {
            "adapter_status": "REJECTED_BY_V24_4_CANONICAL_ADAPTER",
            "intent_state": "ADAPTER_REJECTED",
        },
    )

    audit(
        con,
        "INTENT_REJECTED",
        intent_id=iid,
        key=key,
        symbol=symbol,
        side=side,
        setup=setup,
        reason=reason,
        payload=payload or {},
    )

    return "rejected"


def quarantine_intent_error(
    con: sqlite3.Connection,
    intent: Dict[str, Any],
    reason: str,
    exc: BaseException,
) -> str:
    iid = int(intent.get("id"))
    symbol = str(intent.get("symbol") or "").upper()
    side = str(intent.get("side") or "").upper()
    setup = str(intent.get("setup") or "")
    key = str(intent.get("key") or make_key(symbol, side, setup))

    payload = {
        "reason": reason,
        "exception": repr(exc),
        "traceback": traceback.format_exc(),
        "intent": intent,
        "policy": "NO_PENDING_INTENT_WITH_UNHANDLED_ERROR",
        "state": "QUARANTINED_NOT_LEARNING_ELIGIBLE",
    }

    update_dynamic(
        con,
        INTENT_TABLE,
        iid,
        {
            "adapter_status": "ERROR_QUARANTINED_BY_V24_6D_CANONICAL_ADAPTER",
            "intent_state": "ADAPTER_ERROR_QUARANTINED",
        },
    )

    audit(
        con,
        "INTENT_ERROR_QUARANTINED",
        intent_id=iid,
        key=key,
        symbol=symbol,
        side=side,
        setup=setup,
        reason=reason,
        payload=payload,
    )

    return "quarantined"


def open_intent(con: sqlite3.Connection, intent: Dict[str, Any]) -> str:
    ensure_runtime_schema(con)
    assert_no_open_key_duplicates(con)

    iid = int(intent["id"])
    symbol = str(intent["symbol"]).upper()
    side = str(intent["side"]).upper()
    setup = str(intent["setup"])
    key = str(intent.get("key") or make_key(symbol, side, setup))
    size_mult = fnum(intent.get("requested_size_mult"), 0.0) or 0.0
    size_usd = round(START_EQUITY * size_mult, 8)

    if count_open_global(con) >= int(ADAPTER_CAPS["max_open_global"]):
        return reject_intent(con, intent, "ADAPTER_GLOBAL_OPEN_CAP", ADAPTER_CAPS)

    if count_open_symbol(con, symbol) >= int(ADAPTER_CAPS["max_open_per_symbol"]):
        return reject_intent(con, intent, "ADAPTER_SYMBOL_OPEN_CAP", ADAPTER_CAPS)

    if count_open_key(con, key) >= int(ADAPTER_CAPS["max_open_per_key"]):
        return reject_intent(con, intent, "ADAPTER_KEY_OPEN_CAP", ADAPTER_CAPS)

    if daily_valid_opens(con) >= int(ADAPTER_CAPS["max_daily_valid_opens"]):
        return reject_intent(con, intent, "ADAPTER_DAILY_VALID_OPEN_CAP", ADAPTER_CAPS)

    price, meta = canonical_price(con, symbol)
    if price is None:
        return reject_intent(con, intent, meta.get("reason", "NO_CANONICAL_PRICE"), meta)

    stop, tp = stop_take_prices(side, price)
    stable_id = "V24_4_" + hashlib.sha256(f"{iid}|{key}|{price}|{utc_now()}".encode()).hexdigest()[:20]

    row = {
        "ts": utc_now(),
        "opened_at": utc_now(),
        "last_managed_at": utc_now(),
        "version": VERSION,
        "adapter_version": VERSION,
        "intent_id": iid,
        "stable_position_id": stable_id,
        "key": key,
        "symbol": symbol,
        "side": side,
        "setup": setup,
        "status": OPEN_STATUS,
        "entry_price": price,
        "stop_loss_price": stop,
        "take_profit_price": tp,
        "size_usd": size_usd,
        "requested_size_mult": size_mult,
        "manager_state": "OPEN_MARKED_TO_MARKET",
        "pnl_usd": 0.0,
        "net_pnl_usd": 0.0,
        "pnl_r": 0.0,
        "net_pnl_r": 0.0,
        "mfe_r": 0.0,
        "mae_r": 0.0,
        "price_meta": safe_json(meta),
    }

    required_position_cols = [
        "intent_id",
        "stable_position_id",
        "key",
        "symbol",
        "side",
        "setup",
        "status",
        "entry_price",
        "stop_loss_price",
        "take_profit_price",
        "size_usd",
    ]

    pos_id = insert_position_row(con, row, required=required_position_cols)

    update_dynamic(
        con,
        INTENT_TABLE,
        iid,
        {
            "adapter_status": "OPENED_PAPER_CANARY_BY_V24_4_CANONICAL_ADAPTER",
            "intent_state": "ADAPTER_BOUND_OPEN_PAPER_CANARY",
            "position_row_id": pos_id,
            "stable_position_id": stable_id,
        },
    )

    audit(
        con,
        "OPENED_PAPER_CANARY",
        intent_id=iid,
        position_row_id=pos_id,
        key=key,
        symbol=symbol,
        side=side,
        setup=setup,
        reason="V24_6D_TRANSACTIONAL_BIND_INTENT_TO_CANONICAL_PAPER_CANARY",
        payload={
            "entry_price": price,
            "stop_loss_price": stop,
            "take_profit_price": tp,
            "size_usd": size_usd,
            "price_meta": meta,
            "adapter_caps": ADAPTER_CAPS,
            "transactional_core": True,
        },
    )

    return "opened"


def manage_position(con: sqlite3.Connection, pos: Dict[str, Any]) -> str:
    ensure_runtime_schema(con)

    pid = int(pos["id"])
    symbol = str(pos.get("symbol") or "").upper()
    side = str(pos.get("side") or "").upper()
    setup = str(pos.get("setup") or "")
    key = str(pos.get("key") or make_key(symbol, side, setup))

    entry = fnum(pos.get("entry_price"))
    stop = fnum(pos.get("stop_loss_price"))
    tp = fnum(pos.get("take_profit_price"))
    size = fnum(pos.get("size_usd"))

    if not entry or not stop or not tp or not size:
        audit(
            con,
            "POSITION_MANAGEMENT_SKIPPED",
            position_row_id=pid,
            key=key,
            symbol=symbol,
            side=side,
            setup=setup,
            reason="POSITION_FIELDS_MISSING",
            payload=pos,
        )
        return "skipped"

    price, meta = canonical_price(con, symbol)
    if price is None:
        audit(
            con,
            "POSITION_MANAGEMENT_SKIPPED",
            position_row_id=pid,
            key=key,
            symbol=symbol,
            side=side,
            setup=setup,
            reason=meta.get("reason", "NO_CANONICAL_PRICE"),
            payload=meta,
        )
        return "skipped"

    trigger = trigger_state(side, price, stop, tp)

    if trigger:
        exit_price = canonical_exit_price(side, trigger, stop, tp)
        pnl = compute_pnl(side, entry, exit_price, stop, size)

        update_dynamic(
            con,
            POSITION_TABLE,
            pid,
            {
                "status": CLOSED_STATUS,
                "exit_price": exit_price,
                "closed_at": utc_now(),
                "last_managed_at": utc_now(),
                "manager_state": trigger,
                "close_reason": trigger,
                "pnl_usd": pnl["gross_usd"],
                "net_pnl_usd": pnl["net_usd"],
                "pnl_r": pnl["gross_r"],
                "net_pnl_r": pnl["net_r"],
                "mae_r": min(fnum(pos.get("mae_r"), 0.0) or 0.0, pnl["gross_r"] or 0.0),
                "mfe_r": max(fnum(pos.get("mfe_r"), 0.0) or 0.0, pnl["gross_r"] or 0.0),
                "price_meta": safe_json(meta),
            },
        )

        audit(
            con,
            "POSITION_CLOSED",
            position_row_id=pid,
            key=key,
            symbol=symbol,
            side=side,
            setup=setup,
            reason=trigger,
            payload={
                "trigger_price": price,
                "canonical_exit_price": exit_price,
                "pnl": pnl,
                "price_meta": meta,
            },
        )

        return "closed"

    pnl = compute_pnl(side, entry, price, stop, size)

    update_dynamic(
        con,
        POSITION_TABLE,
        pid,
        {
            "last_managed_at": utc_now(),
            "manager_state": "OPEN_MARKED_TO_MARKET",
            "pnl_usd": pnl["gross_usd"],
            "net_pnl_usd": pnl["net_usd"],
            "pnl_r": pnl["gross_r"],
            "net_pnl_r": pnl["net_r"],
            "mae_r": min(fnum(pos.get("mae_r"), 0.0) or 0.0, pnl["gross_r"] or 0.0),
            "mfe_r": max(fnum(pos.get("mfe_r"), 0.0) or 0.0, pnl["gross_r"] or 0.0),
            "price_meta": safe_json(meta),
        },
    )

    return "managed"


def write_health(con: sqlite3.Connection, stats: Dict[str, Any]) -> None:
    qc = con.execute("PRAGMA quick_check").fetchone()[0]

    con.execute(
        f"""
        INSERT INTO {qid(HEALTH_TABLE)}
        (ts, version, quick_check, pending_intents, opened_positions, managed_positions,
         closed_positions, rejected_intents, errors, payload)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            utc_now(),
            VERSION,
            qc,
            int(stats.get("pending", 0)),
            int(stats.get("opened", 0)),
            int(stats.get("managed", 0)),
            int(stats.get("closed", 0)),
            int(stats.get("rejected", 0)),
            int(stats.get("errors", 0)),
            safe_json(stats),
        ),
    )


def run_once() -> Dict[str, Any]:
    con = connect()
    create_tables(con)

    stats: Dict[str, Any] = {
        "pending": 0,
        "opened": 0,
        "managed": 0,
        "closed": 0,
        "rejected": 0,
        "quarantined": 0,
        "skipped": 0,
        "errors": 0,
    }

    try:
        con.execute("BEGIN IMMEDIATE")
        ensure_runtime_schema(con)
        assert_no_open_key_duplicates(con)

        intents = pending_intents(con)
        for intent in intents:
            stats["pending"] += 1
            try:
                result = open_intent(con, intent)
                if result == "opened":
                    stats["opened"] += 1
                elif result == "rejected":
                    stats["rejected"] += 1
                elif result == "quarantined":
                    stats["quarantined"] += 1
            except Exception as e:
                stats["errors"] += 1
                stats["quarantined"] += 1
                quarantine_intent_error(con, intent, "OPEN_INTENT_EXCEPTION", e)

        for pos in open_positions(con):
            try:
                result = manage_position(con, pos)
                if result == "managed":
                    stats["managed"] += 1
                elif result == "closed":
                    stats["closed"] += 1
                elif result == "skipped":
                    stats["skipped"] += 1
            except Exception as e:
                stats["errors"] += 1
                audit(
                    con,
                    "POSITION_ERROR",
                    position_row_id=pos.get("id"),
                    key=pos.get("key"),
                    symbol=pos.get("symbol"),
                    side=pos.get("side"),
                    setup=pos.get("setup"),
                    reason="POSITION_EXCEPTION",
                    payload={
                        "exception": repr(e),
                        "traceback": traceback.format_exc(),
                        "position": pos,
                    },
                )

        write_health(con, stats)
        con.execute("COMMIT")

    except Exception as e:
        try:
            con.execute("ROLLBACK")
        except Exception:
            pass

        stats["errors"] += 1
        try:
            con.execute("BEGIN IMMEDIATE")
            audit(
                con,
                "ADAPTER_CYCLE_FATAL_ERROR",
                reason="RUN_ONCE_FATAL_EXCEPTION",
                payload={
                    "exception": repr(e),
                    "traceback": traceback.format_exc(),
                },
            )
            write_health(con, stats)
            con.execute("COMMIT")
        except Exception:
            try:
                con.execute("ROLLBACK")
            except Exception:
                pass
            raise

    finally:
        con.close()

    OUT.joinpath("last_cycle.json").write_text(json.dumps({"ts": utc_now(), "stats": stats}, indent=2, sort_keys=True))
    print(json.dumps(stats, indent=2, sort_keys=True))
    return stats


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--daemon", action="store_true")
    ap.add_argument("--interval", type=int, default=30)
    args = ap.parse_args()

    if args.daemon:
        while True:
            try:
                run_once()
            except Exception as e:
                print("V24_6D_CANONICAL_ADAPTER_FATAL", repr(e), flush=True)
                traceback.print_exc()
            time.sleep(args.interval)
    else:
        run_once()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
