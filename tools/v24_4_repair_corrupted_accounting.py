#!/usr/bin/env python3
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from joanbot.institutional.canonical_paper_accounting_v24_4 import (
    POSITION_TABLE,
    VERSION as ACCOUNTING_VERSION,
    canonical_exit_price,
    cols,
    compute_pnl,
    fnum,
    is_exit_outlier,
    qid,
    safe_json,
    table_exists,
    utc_now,
)

ROOT = Path("/storage/emulated/0/Download/joan_crypto_ai_pro_v14")
DB = ROOT / "data" / "joanbot_v14.sqlite"

REPAIR_TABLE = "institutional_v24_4_accounting_repair_audit"
REPAIR_VERSION = "V24_4_CORRUPTED_ACCOUNTING_REPAIR"


def connect():
    con = sqlite3.connect(str(DB), timeout=30, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=30000")
    con.execute("PRAGMA journal_mode=WAL")
    return con


def create_repair_table(con):
    con.execute(f"""
    CREATE TABLE IF NOT EXISTS {qid(REPAIR_TABLE)} (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT,
        version TEXT,
        table_name TEXT,
        row_id INTEGER,
        action TEXT,
        before_json TEXT,
        after_json TEXT,
        reason TEXT
    )
    """)


def update_dynamic(con, table, row_id, values):
    c = set(cols(con, table))
    data = {k: v for k, v in values.items() if k in c}
    if not data:
        return
    names = list(data.keys())
    con.execute(
        f"""
        UPDATE {qid(table)}
        SET {",".join(qid(k) + "=?" for k in names)}
        WHERE id=?
        """,
        [data[k] for k in names] + [row_id],
    )


def repair():
    con = connect()
    create_repair_table(con)

    if not table_exists(con, POSITION_TABLE):
        print("NO_POSITION_TABLE")
        return

    repaired = 0
    skipped = 0

    rows = con.execute(f"SELECT * FROM {qid(POSITION_TABLE)} ORDER BY id DESC LIMIT 200").fetchall()

    for row in rows:
        d = dict(row)
        pid = d.get("id")
        side = str(d.get("side") or "").upper()
        entry = fnum(d.get("entry_price"))
        stop = fnum(d.get("stop_loss_price"))
        tp = fnum(d.get("take_profit_price"))
        exitp = fnum(d.get("exit_price"))
        size = fnum(d.get("size_usd"))
        status = str(d.get("status") or "").upper()
        manager = str(d.get("manager_state") or d.get("close_reason") or "").upper()
        net_r = fnum(d.get("net_pnl_r"))

        if status != "CLOSED":
            skipped += 1
            continue

        trigger = None
        if "STOP" in manager:
            trigger = "STOP_LOSS_HIT"
        elif "TAKE" in manager or "TP" in manager:
            trigger = "TAKE_PROFIT_HIT"

        if not trigger or not entry or not exitp or not size:
            skipped += 1
            continue

        outlier = is_exit_outlier(entry, exitp, stop, tp, trigger)
        r_outlier = net_r is not None and abs(net_r) > 5.0

        if not outlier and not r_outlier:
            skipped += 1
            continue

        if trigger == "STOP_LOSS_HIT" and not stop:
            skipped += 1
            continue
        if trigger == "TAKE_PROFIT_HIT" and not tp:
            skipped += 1
            continue

        corrected_exit = canonical_exit_price(side, trigger, stop, tp)
        pnl = compute_pnl(side, entry, corrected_exit, stop, size)

        after = {
            "exit_price": corrected_exit,
            "pnl_usd": pnl["gross_usd"],
            "net_pnl_usd": pnl["net_usd"],
            "pnl_r": pnl["gross_r"],
            "net_pnl_r": pnl["net_r"],
            "manager_state": trigger + "_ACCOUNTING_REPAIRED_V24_4",
            "close_reason": trigger,
            "accounting_repair_version": REPAIR_VERSION,
            "accounting_repaired_at": utc_now(),
        }

        con.execute(
            f"""
            INSERT INTO {qid(REPAIR_TABLE)}
            (ts, version, table_name, row_id, action, before_json, after_json, reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                utc_now(), REPAIR_VERSION, POSITION_TABLE, pid,
                "REPAIR_CORRUPTED_EXIT_AND_PNL",
                safe_json(d),
                safe_json(after),
                "EXIT_OR_R_OUTLIER_CANONICAL_TRIGGER_REPAIR",
            ),
        )

        update_dynamic(con, POSITION_TABLE, pid, after)
        repaired += 1

    con.close()
    print(json.dumps({"repaired": repaired, "skipped": skipped}, indent=2, sort_keys=True))


if __name__ == "__main__":
    repair()
