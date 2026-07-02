#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

VERSION = "V24_5_CANONICAL_EQUITY"
START_EQUITY = 100000.0

DB_PATH = Path("/storage/emulated/0/Download/joan_crypto_ai_pro_v14/data/joanbot_v14.sqlite")
POSITION_TABLE = "paper_micro_canary_positions_v11"
PRICE_TABLE = "institutional_v24_market_price_latest"
SNAPSHOT_TABLE = "institutional_v24_5_canonical_equity_snapshots"

ROUNDTRIP_FEE_RATE = 0.0014


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def qid(x: str) -> str:
    return '"' + str(x).replace('"', '""') + '"'


def fnum(x: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if x is None:
            return default
        v = float(x)
        if math.isnan(v) or math.isinf(v):
            return default
        return v
    except Exception:
        return default


def table_exists(con: sqlite3.Connection, t: str) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (t,),
    ).fetchone() is not None


def cols(con: sqlite3.Connection, t: str):
    if not table_exists(con, t):
        return []
    return [r[1] for r in con.execute(f"PRAGMA table_info({qid(t)})")]


def canonical_price(con: sqlite3.Connection, symbol: str) -> Optional[float]:
    if not table_exists(con, PRICE_TABLE):
        return None

    c = cols(con, PRICE_TABLE)
    if not {"symbol", "price", "ts"}.issubset(set(c)):
        return None

    r = con.execute(
        f"""
        SELECT price
        FROM {qid(PRICE_TABLE)}
        WHERE UPPER(symbol)=?
        ORDER BY ts DESC
        LIMIT 1
        """,
        (str(symbol).upper(),),
    ).fetchone()

    if not r:
        return None

    return fnum(r[0])


def gross_return(side: str, entry: float, exit_price: float) -> float:
    side = str(side).upper()
    if side == "LONG":
        return (exit_price - entry) / entry
    if side == "SHORT":
        return (entry - exit_price) / entry
    return 0.0


def risk_pct(entry: Optional[float], stop: Optional[float]) -> Optional[float]:
    if not entry or not stop:
        return None
    return abs(stop - entry) / entry


def pnl_for_price(side: str, entry: float, price: float, stop: Optional[float], size_usd: float) -> Dict[str, Any]:
    ret = gross_return(side, entry, price)
    gross_usd = ret * size_usd
    fee_usd = abs(size_usd) * ROUNDTRIP_FEE_RATE
    net_usd = gross_usd - fee_usd

    rp = risk_pct(entry, stop)
    gross_r = ret / rp if rp and rp > 0 else None
    net_r = (net_usd / size_usd) / rp if size_usd and rp and rp > 0 else None

    return {
        "gross_usd": gross_usd,
        "net_usd": net_usd,
        "gross_r": gross_r,
        "net_r": net_r,
        "risk_pct": rp,
    }


def create_snapshot_table(con: sqlite3.Connection):
    con.execute(f"""
    CREATE TABLE IF NOT EXISTS {qid(SNAPSHOT_TABLE)} (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT,
        version TEXT,
        start_equity REAL,
        closed_net_pnl_usd REAL,
        open_net_pnl_usd REAL,
        total_equity REAL,
        closed_trades INTEGER,
        open_positions INTEGER,
        suspicious_positions INTEGER,
        payload TEXT
    )
    """)


def snapshot(con: sqlite3.Connection) -> Dict[str, Any]:
    create_snapshot_table(con)

    out = {
        "version": VERSION,
        "ts": utc_now(),
        "start_equity": START_EQUITY,
        "closed_net_pnl_usd": 0.0,
        "open_net_pnl_usd": 0.0,
        "total_equity": START_EQUITY,
        "closed_trades": 0,
        "open_positions": 0,
        "suspicious_positions": 0,
        "positions": [],
        "warnings": [],
    }

    if not table_exists(con, POSITION_TABLE):
        out["warnings"].append("NO_POSITION_TABLE")
        return out

    c = cols(con, POSITION_TABLE)
    wanted = [
        "id", "symbol", "side", "setup", "status", "entry_price", "stop_loss_price",
        "take_profit_price", "exit_price", "size_usd", "pnl_usd", "net_pnl_usd",
        "pnl_r", "net_pnl_r", "mfe_r", "mae_r", "manager_state", "close_reason",
        "closed_at", "last_managed_at",
    ]
    select_cols = [x for x in wanted if x in c]
    if not select_cols:
        out["warnings"].append("POSITION_SCHEMA_EMPTY")
        return out

    rows = con.execute(
        f"""
        SELECT {",".join(qid(x) for x in select_cols)}
        FROM {qid(POSITION_TABLE)}
        ORDER BY id ASC
        """
    ).fetchall()

    for row in rows:
        d = dict(row)
        status = str(d.get("status") or "").upper()
        symbol = str(d.get("symbol") or "").upper()
        side = str(d.get("side") or "").upper()
        entry = fnum(d.get("entry_price"))
        stop = fnum(d.get("stop_loss_price"))
        exit_price = fnum(d.get("exit_price"))
        size = fnum(d.get("size_usd"), 0.0) or 0.0
        db_net = fnum(d.get("net_pnl_usd"), 0.0) or 0.0
        db_net_r = fnum(d.get("net_pnl_r"))

        flags = []

        if db_net_r is not None and abs(db_net_r) > 5:
            flags.append("NET_R_OUTLIER")

        if exit_price and entry:
            ratio = exit_price / entry
            if ratio > 1.15 or ratio < 0.85:
                flags.append("EXIT_PRICE_OUTLIER")

        item = {
            "id": d.get("id"),
            "symbol": symbol,
            "side": side,
            "setup": d.get("setup"),
            "status": status,
            "entry_price": entry,
            "stop_loss_price": stop,
            "take_profit_price": fnum(d.get("take_profit_price")),
            "exit_price": exit_price,
            "size_usd": size,
            "db_net_pnl_usd": db_net,
            "db_net_r": db_net_r,
            "manager_state": d.get("manager_state"),
            "close_reason": d.get("close_reason"),
            "flags": flags,
        }

        if status == "CLOSED":
            out["closed_trades"] += 1
            out["closed_net_pnl_usd"] += db_net

        elif status == "OPEN":
            out["open_positions"] += 1
            px = canonical_price(con, symbol)
            item["canonical_current_price"] = px

            if px and entry and size:
                p = pnl_for_price(side, entry, px, stop, size)
                item["float_net_pnl_usd"] = p["net_usd"]
                item["float_net_r"] = p["net_r"]
                out["open_net_pnl_usd"] += p["net_usd"]
            else:
                flags.append("OPEN_POSITION_NO_CANONICAL_FLOATING_PNL")

        if flags:
            out["suspicious_positions"] += 1

        out["positions"].append(item)

    out["closed_net_pnl_usd"] = round(out["closed_net_pnl_usd"], 8)
    out["open_net_pnl_usd"] = round(out["open_net_pnl_usd"], 8)
    out["total_equity"] = round(START_EQUITY + out["closed_net_pnl_usd"] + out["open_net_pnl_usd"], 8)

    con.execute(
        f"""
        INSERT INTO {qid(SNAPSHOT_TABLE)}
        (ts, version, start_equity, closed_net_pnl_usd, open_net_pnl_usd,
         total_equity, closed_trades, open_positions, suspicious_positions, payload)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            out["ts"],
            VERSION,
            out["start_equity"],
            out["closed_net_pnl_usd"],
            out["open_net_pnl_usd"],
            out["total_equity"],
            out["closed_trades"],
            out["open_positions"],
            out["suspicious_positions"],
            json.dumps(out, sort_keys=True),
        ),
    )

    return out


def connect() -> sqlite3.Connection:
    con = sqlite3.connect(str(DB_PATH), timeout=30, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=30000")
    con.execute("PRAGMA journal_mode=WAL")
    return con


def main() -> int:
    con = connect()
    snap = snapshot(con)
    con.close()
    print(json.dumps(snap, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
