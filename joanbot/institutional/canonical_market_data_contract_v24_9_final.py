#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import sqlite3
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path("/storage/emulated/0/Download/joan_crypto_ai_pro_v14")
DB = ROOT / "data" / "joanbot_v14.sqlite"

VERSION = "V24_9_2_MAX_INSTITUTIONAL_MARKET_DATA_CONTRACT"

SYMBOLS = ("BTCUSDT", "ETHUSDT")

LATEST_TABLE = "institutional_v24_market_price_latest"
STATUS_TABLE = "institutional_v24_9_max_market_data_status"
AUDIT_TABLE = "institutional_v24_9_max_market_data_audit"

BINANCE_FAPI_PREMIUM = "https://fapi.binance.com/fapi/v1/premiumIndex?symbol={symbol}"

MAX_SOURCE_AGE_MIN = 5.0
MAX_STATUS_AGE_MIN = 5.0
MAX_MARK_INDEX_DIVERGENCE = 0.035
MAX_CANONICAL_JUMP_PCT = 0.12


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def qid(x: str) -> str:
    return '"' + str(x).replace('"', '""') + '"'


def parse_ts(x: Any) -> Optional[datetime]:
    if not x:
        return None
    try:
        d = datetime.fromisoformat(str(x).replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.astimezone(timezone.utc)
    except Exception:
        return None


def age_min(x: Any) -> Optional[float]:
    d = parse_ts(x)
    if not d:
        return None
    return round((datetime.now(timezone.utc) - d).total_seconds() / 60.0, 6)


def fnum(x: Any) -> Optional[float]:
    try:
        if x is None:
            return None
        v = float(x)
        if not math.isfinite(v):
            return None
        return v
    except Exception:
        return None


def connect() -> sqlite3.Connection:
    con = sqlite3.connect(str(DB), timeout=30, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=30000")
    con.execute("PRAGMA journal_mode=WAL")
    return con


def table_exists(con: sqlite3.Connection, table: str) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone() is not None


def table_info(con: sqlite3.Connection, table: str) -> List[Dict[str, Any]]:
    if not table_exists(con, table):
        return []
    return [dict(r) for r in con.execute(f"PRAGMA table_info({qid(table)})")]


def cols(con: sqlite3.Connection, table: str) -> List[str]:
    return [r["name"] for r in table_info(con, table)]


def ensure_col(con: sqlite3.Connection, table: str, name: str, typ: str) -> None:
    if name not in cols(con, table):
        con.execute(f"ALTER TABLE {qid(table)} ADD COLUMN {qid(name)} {typ}")


def ensure_tables(con: sqlite3.Connection) -> None:
    con.execute(f"""
    CREATE TABLE IF NOT EXISTS {qid(LATEST_TABLE)} (
        symbol TEXT PRIMARY KEY,
        ts TEXT NOT NULL,
        version TEXT NOT NULL,
        price REAL NOT NULL,
        source TEXT NOT NULL,
        source_table TEXT,
        source_col TEXT,
        source_ts TEXT,
        source_age_min REAL,
        reason TEXT NOT NULL,
        confidence REAL,
        payload TEXT
    )
    """)

    con.execute(f"""
    CREATE TABLE IF NOT EXISTS {qid(STATUS_TABLE)} (
        symbol TEXT PRIMARY KEY,
        ts TEXT NOT NULL,
        version TEXT NOT NULL,
        ok INTEGER NOT NULL,
        price REAL,
        reason TEXT NOT NULL,
        source TEXT,
        source_ts TEXT,
        source_age_min REAL,
        mark_price REAL,
        index_price REAL,
        mark_index_divergence REAL,
        previous_canonical_price REAL,
        previous_canonical_ts TEXT,
        jump_pct REAL,
        payload TEXT
    )
    """)

    con.execute(f"""
    CREATE TABLE IF NOT EXISTS {qid(AUDIT_TABLE)} (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL,
        version TEXT NOT NULL,
        symbol TEXT NOT NULL,
        accepted INTEGER NOT NULL,
        price REAL,
        reason TEXT NOT NULL,
        source TEXT,
        source_ts TEXT,
        source_age_min REAL,
        mark_price REAL,
        index_price REAL,
        mark_index_divergence REAL,
        previous_canonical_price REAL,
        previous_canonical_ts TEXT,
        jump_pct REAL,
        payload TEXT
    )
    """)

    common = {
        "version": "TEXT",
        "source": "TEXT",
        "source_table": "TEXT",
        "source_col": "TEXT",
        "source_ts": "TEXT",
        "source_age_min": "REAL",
        "confidence": "REAL",
        "payload": "TEXT",
        "mark_price": "REAL",
        "index_price": "REAL",
        "mark_index_divergence": "REAL",
        "previous_canonical_price": "REAL",
        "previous_canonical_ts": "TEXT",
        "jump_pct": "REAL",
    }

    for table in (LATEST_TABLE, STATUS_TABLE, AUDIT_TABLE):
        if not table_exists(con, table):
            continue
        for name, typ in common.items():
            if name not in cols(con, table):
                try:
                    ensure_col(con, table, name, typ)
                except Exception:
                    pass


def default_for_column(name: str, typ: str, values: Dict[str, Any]) -> Any:
    n = str(name).lower()
    t = str(typ or "").upper()
    now = values.get("ts") or utc_now()

    if n == "version":
        return VERSION
    if n == "ts":
        return now
    if n == "symbol":
        return values.get("symbol") or "UNKNOWN"
    if n == "ok":
        return 1 if values.get("ok") else 0
    if n == "accepted":
        return 1 if values.get("accepted") else 0
    if n == "price":
        return float(values.get("price") or 0.0)
    if n in {"reason", "status"}:
        return values.get("reason") or "CANONICAL_PRICE_OK"
    if n in {"source", "source_name"}:
        return values.get("source") or "BINANCE_FAPI_PREMIUM_INDEX"
    if n == "source_table":
        return values.get("source_table") or "BINANCE_FAPI_PREMIUM_INDEX"
    if n == "source_col":
        return values.get("source_col") or "markPrice"
    if n == "source_ts":
        return values.get("source_ts") or now
    if n == "source_age_min":
        return float(values.get("source_age_min") or 0.0)
    if n == "confidence":
        return float(values.get("confidence") or 0.0)
    if n == "payload":
        return values.get("payload") or "{}"
    if n in {"mark_price", "index_price", "mark_index_divergence", "previous_canonical_price", "jump_pct"}:
        return float(values.get(n) or 0.0)
    if n == "previous_canonical_ts":
        return values.get("previous_canonical_ts") or ""
    if n in {"created_at", "updated_at", "last_seen_at"}:
        return now
    if n == "id":
        return None

    if "INT" in t:
        return 0
    if any(x in t for x in ("REAL", "FLOA", "DOUB", "NUM", "DEC")):
        return 0.0
    if "BLOB" in t:
        return b""

    return f"V24_9_2_MAX_DEFAULT_{name}"


def row_for_table(con: sqlite3.Connection, table: str, values: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}

    for col in table_info(con, table):
        name = col["name"]
        typ = col.get("type") or ""
        notnull = int(col.get("notnull") or 0) == 1
        pk = int(col.get("pk") or 0) == 1
        dflt = col.get("dflt_value")

        if pk and name.lower() == "id":
            continue

        if name in values:
            out[name] = values[name]
            continue

        if notnull and dflt is None:
            v = default_for_column(name, typ, values)
            if v is not None:
                out[name] = v

    existing = {c["name"] for c in table_info(con, table)}
    for k, v in values.items():
        if k in existing:
            out[k] = v

    return out


def insert_dynamic(con: sqlite3.Connection, table: str, values: Dict[str, Any]) -> None:
    data = row_for_table(con, table, values)
    if not data:
        raise RuntimeError(f"NO_INSERTABLE_COLUMNS:{table}")

    names = list(data.keys())
    con.execute(
        f"""
        INSERT INTO {qid(table)}
        ({",".join(qid(n) for n in names)})
        VALUES ({",".join("?" for _ in names)})
        """,
        [data[n] for n in names],
    )


def replace_by_symbol(con: sqlite3.Connection, table: str, symbol: str, values: Dict[str, Any]) -> None:
    if "symbol" in cols(con, table):
        con.execute(f"DELETE FROM {qid(table)} WHERE UPPER(symbol)=?", (symbol.upper(),))
    insert_dynamic(con, table, values)


def previous_final_canonical(con: sqlite3.Connection, symbol: str) -> Optional[Dict[str, Any]]:
    if not table_exists(con, STATUS_TABLE):
        return None
    r = con.execute(
        f"""
        SELECT *
        FROM {qid(STATUS_TABLE)}
        WHERE UPPER(symbol)=?
          AND ok=1
          AND version=?
          AND source='BINANCE_FAPI_PREMIUM_INDEX'
          AND price IS NOT NULL
        """,
        (symbol.upper(), VERSION),
    ).fetchone()
    if not r:
        return None
    d = dict(r)
    p = fnum(d.get("price"))
    if p is None or p <= 0:
        return None
    return d


def fetch_binance(symbol: str) -> Dict[str, Any]:
    sym = symbol.upper()
    url = BINANCE_FAPI_PREMIUM.format(symbol=sym)

    with urllib.request.urlopen(url, timeout=8) as resp:
        raw = resp.read().decode("utf-8")

    data = json.loads(raw)
    mark = fnum(data.get("markPrice"))
    index = fnum(data.get("indexPrice"))

    if mark is None or mark <= 0:
        price = index
        source_col = "indexPrice"
    else:
        price = mark
        source_col = "markPrice"

    source_ts = utc_now()
    event_ms = data.get("time")
    if event_ms is not None:
        try:
            source_ts = datetime.fromtimestamp(float(event_ms) / 1000.0, tz=timezone.utc).isoformat()
        except Exception:
            source_ts = utc_now()

    div = None
    if mark is not None and index is not None and index > 0:
        div = abs(mark - index) / index

    return {
        "symbol": sym,
        "price": price,
        "mark_price": mark,
        "index_price": index,
        "mark_index_divergence": div,
        "source": "BINANCE_FAPI_PREMIUM_INDEX",
        "source_table": "BINANCE_FAPI_PREMIUM_INDEX",
        "source_col": source_col,
        "source_ts": source_ts,
        "source_age_min": age_min(source_ts),
        "raw": data,
    }


def validate_candidate(con: sqlite3.Connection, c: Dict[str, Any]) -> Dict[str, Any]:
    sym = c["symbol"]
    price = fnum(c.get("price"))
    mark = fnum(c.get("mark_price"))
    index = fnum(c.get("index_price"))
    source_age = c.get("source_age_min")
    div = c.get("mark_index_divergence")

    if price is None or price <= 0:
        return {**c, "ok": False, "reason": "PRIMARY_PRICE_NULL_OR_NON_POSITIVE"}

    if source_age is None or source_age > MAX_SOURCE_AGE_MIN:
        return {**c, "ok": False, "reason": "PRIMARY_PRICE_STALE"}

    if mark is None or index is None or mark <= 0 or index <= 0:
        return {**c, "ok": False, "reason": "PRIMARY_MARK_INDEX_INCOMPLETE"}

    if div is None or div > MAX_MARK_INDEX_DIVERGENCE:
        return {**c, "ok": False, "reason": "PRIMARY_MARK_INDEX_DIVERGENCE_TOO_HIGH"}

    prev = previous_final_canonical(con, sym)
    jump = None

    if prev:
        prev_price = fnum(prev.get("price"))
        if prev_price and prev_price > 0:
            jump = abs(price - prev_price) / prev_price
            if jump > MAX_CANONICAL_JUMP_PCT:
                return {
                    **c,
                    "ok": False,
                    "reason": "PRIMARY_PRICE_JUMP_OUTLIER_FROM_FINAL_CANONICAL",
                    "previous_canonical_price": prev_price,
                    "previous_canonical_ts": prev.get("ts"),
                    "jump_pct": jump,
                }

    return {
        **c,
        "ok": True,
        "reason": "CANONICAL_PRICE_OK",
        "previous_canonical_price": fnum(prev.get("price")) if prev else None,
        "previous_canonical_ts": prev.get("ts") if prev else None,
        "jump_pct": jump,
    }


def reject_result(symbol: str, reason: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "symbol": symbol.upper(),
        "ok": False,
        "accepted": False,
        "price": None,
        "ts": utc_now(),
        "version": VERSION,
        "reason": reason,
        "source": "BINANCE_FAPI_PREMIUM_INDEX",
        "source_table": "BINANCE_FAPI_PREMIUM_INDEX",
        "source_col": None,
        "source_ts": None,
        "source_age_min": None,
        "confidence": 0.0,
        "mark_price": None,
        "index_price": None,
        "mark_index_divergence": None,
        "previous_canonical_price": None,
        "previous_canonical_ts": None,
        "jump_pct": None,
        "payload": json.dumps({"version": VERSION, "payload": payload}, sort_keys=True, default=str),
    }


def evaluate_symbol(con: sqlite3.Connection, symbol: str) -> Dict[str, Any]:
    sym = symbol.upper()
    try:
        candidate = fetch_binance(sym)
    except Exception as e:
        return reject_result(sym, "PRIMARY_BINANCE_FETCH_FAILED", {"error": repr(e)})

    checked = validate_candidate(con, candidate)
    ok = bool(checked.get("ok"))

    payload = json.dumps(
        {"version": VERSION, "candidate": checked},
        sort_keys=True,
        default=str,
    )

    return {
        "symbol": sym,
        "ok": ok,
        "accepted": ok,
        "price": checked.get("price") if ok else None,
        "ts": utc_now(),
        "version": VERSION,
        "reason": checked.get("reason"),
        "source": checked.get("source"),
        "source_table": checked.get("source_table") or "BINANCE_FAPI_PREMIUM_INDEX",
        "source_col": checked.get("source_col"),
        "source_ts": checked.get("source_ts"),
        "source_age_min": checked.get("source_age_min"),
        "confidence": 1.0 if ok else 0.0,
        "mark_price": checked.get("mark_price"),
        "index_price": checked.get("index_price"),
        "mark_index_divergence": checked.get("mark_index_divergence"),
        "previous_canonical_price": checked.get("previous_canonical_price"),
        "previous_canonical_ts": checked.get("previous_canonical_ts"),
        "jump_pct": checked.get("jump_pct"),
        "payload": payload,
    }


def write_status(con: sqlite3.Connection, result: Dict[str, Any]) -> None:
    replace_by_symbol(con, STATUS_TABLE, result["symbol"], result)


def write_latest_if_ok(con: sqlite3.Connection, result: Dict[str, Any]) -> None:
    if not result.get("ok"):
        return
    values = dict(result)
    values["reason"] = "CANONICAL_PRICE_OK"
    values["confidence"] = 1.0
    replace_by_symbol(con, LATEST_TABLE, result["symbol"], values)


def write_audit(con: sqlite3.Connection, result: Dict[str, Any]) -> None:
    values = dict(result)
    values["accepted"] = 1 if result.get("ok") else 0
    insert_dynamic(con, AUDIT_TABLE, values)


def run_once() -> Dict[str, Any]:
    con = connect()
    ensure_tables(con)

    results: Dict[str, Dict[str, Any]] = {}
    for sym in SYMBOLS:
        result = evaluate_symbol(con, sym)
        write_status(con, result)
        write_latest_if_ok(con, result)
        write_audit(con, result)
        results[sym] = result

    con.close()

    summary = {
        "version": VERSION,
        "ts": utc_now(),
        "results": {
            sym: {
                "ok": r["ok"],
                "price": r["price"],
                "reason": r["reason"],
                "source": r["source"],
                "source_age_min": r["source_age_min"],
                "mark_price": r["mark_price"],
                "index_price": r["index_price"],
                "mark_index_divergence": r["mark_index_divergence"],
                "previous_canonical_price": r["previous_canonical_price"],
                "jump_pct": r["jump_pct"],
            }
            for sym, r in results.items()
        },
    }

    out = ROOT / "data" / "v24_1_market_price_contract"
    out.mkdir(parents=True, exist_ok=True)
    (out / "v24_9_2_max_summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True, default=str))
    print(json.dumps(summary, indent=2, sort_keys=True, default=str))
    return summary


def canonical_price_snapshot(con: sqlite3.Connection, symbol: str) -> Dict[str, Any]:
    ensure_tables(con)
    sym = symbol.upper()

    r = con.execute(
        f"SELECT * FROM {qid(STATUS_TABLE)} WHERE UPPER(symbol)=?",
        (sym,),
    ).fetchone()

    if not r:
        return {
            "ok": False,
            "symbol": sym,
            "price": None,
            "age_min": None,
            "reason": "NO_MAX_CANONICAL_PRICE_STATUS",
            "source": None,
            "version": VERSION,
        }

    d = dict(r)
    a = age_min(d.get("ts"))
    p = fnum(d.get("price"))

    ok = (
        int(d.get("ok") or 0) == 1
        and d.get("version") == VERSION
        and p is not None
        and p > 0
        and a is not None
        and a <= MAX_STATUS_AGE_MIN
        and d.get("source") == "BINANCE_FAPI_PREMIUM_INDEX"
        and d.get("reason") == "CANONICAL_PRICE_OK"
    )

    return {
        "ok": bool(ok),
        "symbol": sym,
        "price": p if ok else None,
        "raw_price": p,
        "ts": d.get("ts"),
        "age_min": a,
        "reason": "CANONICAL_PRICE_OK" if ok else d.get("reason") or "MAX_CANONICAL_PRICE_INVALID",
        "source": d.get("source"),
        "source_table": STATUS_TABLE,
        "source_col": "price",
        "source_ts": d.get("source_ts"),
        "source_age_min": d.get("source_age_min"),
        "mark_price": d.get("mark_price"),
        "index_price": d.get("index_price"),
        "mark_index_divergence": d.get("mark_index_divergence"),
        "previous_canonical_price": d.get("previous_canonical_price"),
        "jump_pct": d.get("jump_pct"),
        "version": d.get("version"),
    }


def canonical_market_health(con: sqlite3.Connection) -> Dict[str, Any]:
    details = {}
    ok = True
    for sym in SYMBOLS:
        snap = canonical_price_snapshot(con, sym)
        details[sym] = snap
        if not snap.get("ok"):
            ok = False
    return {
        "ok": ok,
        "reason": "PRICE_OK" if ok else "PRICE_NOT_CANONICAL_OK",
        "details": details,
    }


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
                print("V24_9_2_MAX_MARKET_DATA_FATAL", repr(e), flush=True)
            time.sleep(max(10, int(args.interval)))
    else:
        run_once()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
