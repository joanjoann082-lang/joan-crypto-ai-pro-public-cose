#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path.cwd()
DB = ROOT / "data" / "joanbot_v14.sqlite"
OUT = ROOT / "data" / "v17_8_1"
LOCK = OUT / "adapter.lock"

VERSION = "V17.8.1_INSTITUTIONAL_PAPER_CANARY_ADAPTER_PRO_MAX"

INTENT_TABLE = "institutional_quant_canary_execution_intents_v17_7_2"
POSITION_TABLE = "paper_micro_canary_positions_v11"
OUTCOME_TABLE = "outcome_provenance_v1"

ADAPTER_TABLE = "institutional_paper_canary_adapter_v17_8_1"
MAP_TABLE = "institutional_paper_canary_intent_map_v17_8_1"
HEALTH_TABLE = "institutional_paper_canary_adapter_health_v17_8_1"
AUDIT_TABLE = "institutional_paper_canary_adapter_audit_v17_8_1"

DEFAULT_EQUITY_USD = 100000.0
MAX_OPEN_GLOBAL = 2
MAX_OPEN_PER_KEY = 1
MAX_NEW_OPENS_PER_CYCLE = 1
MAX_SIZE_MULT = 0.025
MIN_SIZE_MULT = 0.005

MAX_PRICE_STALENESS_MIN = 12
DEFAULT_HORIZON_MIN = 360
FEE_RATE_ROUNDTRIP = 0.0008
SLIPPAGE_RATE_ROUNDTRIP = 0.0006

VALID_INTENT_STATE = "MAX_QUANT_MANUAL_APPROVED_PENDING_PAPER_ADAPTER"
VALID_ADAPTER_STATUS = "PENDING_ADAPTER_BINDING"
VALID_MODE = "PAPER_MICRO_CANARY_ONLY"
VALID_PERMISSION = "PAPER_ONLY_NO_REAL_EXECUTION"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def qid(x: str) -> str:
    return '"' + str(x).replace('"', '""') + '"'


def parse_dt(x: Any) -> Optional[datetime]:
    if x is None or x == "":
        return None
    if isinstance(x, (int, float)):
        v = float(x)
        if v > 1e12:
            v /= 1000.0
        if v > 1e9:
            return datetime.fromtimestamp(v, timezone.utc)
        return None
    s = str(x).strip().replace("Z", "+00:00")
    if not s:
        return None
    if s.replace(".", "", 1).isdigit():
        return parse_dt(float(s))
    for cand in (s, s.replace(" ", "T")):
        try:
            d = datetime.fromisoformat(cand)
            if d.tzinfo is None:
                d = d.replace(tzinfo=timezone.utc)
            return d.astimezone(timezone.utc)
        except Exception:
            pass
    return None


def age_minutes(ts: Any) -> Optional[float]:
    d = parse_dt(ts)
    if not d:
        return None
    return max(0.0, (datetime.now(timezone.utc) - d).total_seconds() / 60.0)


def fnum(x: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if x is None:
            return default
        s = str(x).strip()
        if s == "" or s.lower() in {"none", "nan", "inf", "-inf"}:
            return default
        v = float(x)
        if math.isnan(v) or math.isinf(v):
            return default
        return v
    except Exception:
        return default


def inum(x: Any, default: int = 0) -> int:
    v = fnum(x, None)
    return default if v is None else int(v)


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def safe_json(x: Any, fallback: Any) -> Any:
    if x is None:
        return fallback
    if isinstance(x, (dict, list)):
        return x
    try:
        return json.loads(str(x))
    except Exception:
        return fallback


def sha256_obj(obj: Any) -> str:
    raw = json.dumps(obj, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


class AdapterLock:
    def __enter__(self):
        OUT.mkdir(parents=True, exist_ok=True)

        if LOCK.exists():
            try:
                age = time.time() - LOCK.stat().st_mtime
                if age > 600:
                    LOCK.unlink()
            except Exception:
                pass

        try:
            fd = os.open(str(LOCK), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
        except FileExistsError:
            raise RuntimeError("ADAPTER_LOCKED_ALREADY_RUNNING")

        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            LOCK.unlink()
        except Exception:
            pass


def connect_rw() -> sqlite3.Connection:
    con = sqlite3.connect(str(DB), timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=OFF")
    con.execute("PRAGMA journal_mode=WAL")
    return con


def exists(con: sqlite3.Connection, table: str) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,)
    ).fetchone() is not None


def columns(con: sqlite3.Connection, table: str) -> List[str]:
    if not exists(con, table):
        return []
    return [r[1] for r in con.execute(f"PRAGMA table_info({qid(table)})").fetchall()]


def insert_dynamic(con: sqlite3.Connection, table: str, values: Dict[str, Any]) -> int:
    cols = set(columns(con, table))
    data = {k: v for k, v in values.items() if k in cols}

    if not data:
        raise RuntimeError(f"NO_MATCHING_COLUMNS_FOR_INSERT:{table}")

    names = list(data.keys())
    sql = f"""
        INSERT INTO {qid(table)}
        ({','.join(qid(c) for c in names)})
        VALUES ({','.join(['?'] * len(names))})
    """
    cur = con.execute(sql, [data[c] for c in names])
    return int(cur.lastrowid)


def update_dynamic(con: sqlite3.Connection, table: str, values: Dict[str, Any], where: str, params: Tuple[Any, ...]) -> None:
    cols = set(columns(con, table))
    data = {k: v for k, v in values.items() if k in cols}
    if not data:
        return

    names = list(data.keys())
    sql = f"""
        UPDATE {qid(table)}
        SET {','.join(qid(c) + '=?' for c in names)}
        WHERE {where}
    """
    con.execute(sql, [data[c] for c in names] + list(params))


def create_tables(con: sqlite3.Connection) -> None:
    con.execute(f"""
        CREATE TABLE IF NOT EXISTS {qid(ADAPTER_TABLE)} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            version TEXT NOT NULL,
            event_type TEXT NOT NULL,
            intent_id INTEGER,
            position_row_id INTEGER,
            stable_position_id TEXT,
            key TEXT,
            symbol TEXT,
            side TEXT,
            setup TEXT,
            adapter_state TEXT,
            entry_price REAL,
            exit_price REAL,
            requested_size_mult REAL,
            size_usd REAL,
            pnl_r REAL,
            net_pnl_r REAL,
            reason TEXT,
            payload TEXT
        )
    """)

    con.execute(f"""
        CREATE TABLE IF NOT EXISTS {qid(MAP_TABLE)} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            version TEXT NOT NULL,
            intent_id INTEGER NOT NULL UNIQUE,
            intent_hash TEXT,
            key TEXT,
            symbol TEXT,
            side TEXT,
            setup TEXT,
            position_row_id INTEGER,
            stable_position_id TEXT,
            map_state TEXT NOT NULL,
            payload TEXT
        )
    """)

    con.execute(f"""
        CREATE TABLE IF NOT EXISTS {qid(HEALTH_TABLE)} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            version TEXT NOT NULL,
            quick_check TEXT,
            pending_intents INTEGER,
            opened_positions INTEGER,
            managed_positions INTEGER,
            closed_positions INTEGER,
            rejected_intents INTEGER,
            reconciled_items INTEGER,
            errors INTEGER,
            payload TEXT
        )
    """)

    con.execute(f"""
        CREATE TABLE IF NOT EXISTS {qid(AUDIT_TABLE)} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            version TEXT NOT NULL,
            event_type TEXT NOT NULL,
            key TEXT,
            intent_id INTEGER,
            position_row_id INTEGER,
            stable_position_id TEXT,
            payload TEXT
        )
    """)

    con.execute(f"CREATE INDEX IF NOT EXISTS idx_adapter_v17_8_1_ts_event ON {qid(ADAPTER_TABLE)}(ts,event_type)")
    con.execute(f"CREATE INDEX IF NOT EXISTS idx_adapter_v17_8_1_position ON {qid(ADAPTER_TABLE)}(position_row_id)")
    con.execute(f"CREATE INDEX IF NOT EXISTS idx_map_v17_8_1_intent ON {qid(MAP_TABLE)}(intent_id)")
    con.execute(f"CREATE INDEX IF NOT EXISTS idx_map_v17_8_1_key ON {qid(MAP_TABLE)}(key,ts)")



def latest_market_price(con: sqlite3.Connection, symbol: str):
    """
    Institutional canonical market-price resolver.

    Priority:
      1) institutional_v24_market_price_latest
      2) legacy V17.8.1 adapter resolver

    Contract:
      return (price: float|None, meta: dict)

    This is not a new decision layer. It only binds the existing paper adapter
    to the canonical V24 price contract so valid intents are not rejected with
    NO_FRESH_MARKET_PRICE while V24.1 has fresh BTC/ETH prices.
    """
    sym = str(symbol or "").upper().strip()

    def _table_cols(table: str):
        try:
            return [r[1] for r in con.execute(f"PRAGMA table_info({qid(table)})").fetchall()]
        except Exception:
            return []

    def _pick(cols, names):
        for n in names:
            if n in cols:
                return n
        return None

    def _row_dict(row):
        if row is None:
            return None
        try:
            return dict(row)
        except Exception:
            return {k: row[k] for k in row.keys()}

    canonical_table = "institutional_v24_market_price_latest"

    try:
        cols = _table_cols(canonical_table)
        if cols and "symbol" in cols:
            price_col = _pick(cols, [
                "price", "mark_price", "last_price", "mid_price",
                "close", "value", "last"
            ])
            ts_col = _pick(cols, [
                "ts", "generated_utc", "updated_utc", "updated_at",
                "created_at", "time", "datetime"
            ])
            age_col = _pick(cols, ["age_min", "age_minutes", "staleness_min"])

            if price_col:
                order_col = ts_col if ts_col else ("id" if "id" in cols else "rowid")
                row = con.execute(
                    f"""
                    SELECT *
                    FROM {qid(canonical_table)}
                    WHERE UPPER(symbol)=?
                    ORDER BY {qid(order_col)} DESC
                    LIMIT 1
                    """,
                    (sym,)
                ).fetchone()

                d = _row_dict(row)
                if d:
                    price = fnum(d.get(price_col), None)
                    age_min = None

                    if age_col and d.get(age_col) is not None:
                        age_min = fnum(d.get(age_col), None)
                    elif ts_col and d.get(ts_col):
                        dt = parse_dt(d.get(ts_col))
                        if dt:
                            age_min = (datetime.now(timezone.utc) - dt).total_seconds() / 60.0

                    fresh = (
                        price is not None
                        and price > 0
                        and (
                            age_min is None
                            or age_min <= MAX_PRICE_STALENESS_MIN
                        )
                    )

                    if fresh:
                        return float(price), {
                            "source": canonical_table,
                            "source_col": price_col,
                            "ts_col": ts_col,
                            "age_col": age_col,
                            "age_min": round(age_min, 4) if age_min is not None else None,
                            "reason": "V24_CANONICAL_PRICE_ACCEPTED",
                            "symbol": sym,
                        }

                    canonical_meta = {
                        "canonical_table": canonical_table,
                        "canonical_price": price,
                        "canonical_age_min": age_min,
                        "canonical_reason": "V24_CANONICAL_PRICE_STALE_OR_INVALID",
                    }
                else:
                    canonical_meta = {
                        "canonical_table": canonical_table,
                        "canonical_reason": "V24_CANONICAL_PRICE_ROW_MISSING",
                    }
            else:
                canonical_meta = {
                    "canonical_table": canonical_table,
                    "canonical_reason": "V24_CANONICAL_PRICE_COLUMN_MISSING",
                    "columns": cols,
                }
        else:
            canonical_meta = {
                "canonical_table": canonical_table,
                "canonical_reason": "V24_CANONICAL_TABLE_MISSING_OR_NO_SYMBOL",
                "columns": cols,
            }

    except Exception as e:
        canonical_meta = {
            "canonical_table": canonical_table,
            "canonical_reason": "V24_CANONICAL_PRICE_EXCEPTION",
            "error": repr(e),
        }

    try:
        price, meta = latest_market_price_legacy(con, symbol)
        if isinstance(meta, dict):
            meta = dict(meta)
            meta["v24_canonical_checked"] = canonical_meta
        else:
            meta = {"legacy_meta": meta, "v24_canonical_checked": canonical_meta}
        return price, meta
    except Exception as e:
        return None, {
            "reason": "NO_FRESH_MARKET_PRICE",
            "legacy_error": repr(e),
            "v24_canonical_checked": canonical_meta,
        }



def latest_market_price_legacy(con: sqlite3.Connection, symbol: str) -> Tuple[Optional[float], Dict[str, Any]]:
    tables = [
        "market_snapshots",
        "features",
        "decisions",
    ]

    price_cols = ["price", "last_price", "mark_price", "mid_price", "close", "c", "entry_price"]
    ts_cols = ["ts", "timestamp", "time", "created_at", "updated_at"]

    for table in tables:
        if not exists(con, table):
            continue

        cols = columns(con, table)
        if "symbol" not in cols:
            continue

        pc = next((c for c in price_cols if c in cols), None)
        tc = next((c for c in ts_cols if c in cols), None)

        if not pc:
            continue

        order = f"{qid(tc)} DESC" if tc else "rowid DESC"

        try:
            rows = con.execute(
                f"""
                SELECT *
                FROM {qid(table)}
                WHERE UPPER(COALESCE(symbol,''))=?
                ORDER BY {order}
                LIMIT 10
                """,
                (symbol.upper(),)
            ).fetchall()
        except Exception:
            continue

        for rr in rows:
            r = dict(rr)
            price = fnum(r.get(pc), None)
            row_ts = r.get(tc) if tc else None
            stale = age_minutes(row_ts) if row_ts else None

            if price and price > 0:
                if stale is not None and stale > MAX_PRICE_STALENESS_MIN:
                    continue
                return price, {
                    "table": table,
                    "price_column": pc,
                    "ts_column": tc,
                    "row_ts": row_ts,
                    "age_min": stale,
                }

    return None, {"error": "NO_FRESH_MARKET_PRICE"}


def recent_prices(con: sqlite3.Connection, symbol: str, limit: int = 150) -> List[float]:
    table = "market_snapshots"
    if not exists(con, table):
        return []

    cols = columns(con, table)
    if "symbol" not in cols:
        return []

    pc = next((c for c in ["close", "price", "last_price", "mark_price", "mid_price", "c"] if c in cols), None)
    tc = next((c for c in ["ts", "timestamp", "time", "created_at"] if c in cols), None)

    if not pc:
        return []

    order = f"{qid(tc)} DESC" if tc else "rowid DESC"

    try:
        rows = con.execute(
            f"""
            SELECT {qid(pc)}
            FROM {qid(table)}
            WHERE UPPER(COALESCE(symbol,''))=?
            ORDER BY {order}
            LIMIT ?
            """,
            (symbol.upper(), limit)
        ).fetchall()
    except Exception:
        return []

    vals = [fnum(r[0], None) for r in rows]
    vals = [v for v in vals if v and v > 0]
    return list(reversed(vals))


def risk_model(con: sqlite3.Connection, symbol: str) -> Dict[str, Any]:
    prices = recent_prices(con, symbol)

    if len(prices) < 30:
        risk_pct = 0.008
        source = "fallback_0_8pct"
    else:
        rets = []
        for a, b in zip(prices[:-1], prices[1:]):
            if a > 0 and b > 0:
                rets.append(abs(math.log(b / a)))

        if len(rets) < 20:
            risk_pct = 0.008
            source = "fallback_insufficient_returns"
        else:
            rets = sorted(rets)
            q75 = rets[int(0.75 * (len(rets) - 1))]
            q90 = rets[int(0.90 * (len(rets) - 1))]
            risk_pct = clamp(2.2 * q75 + 0.6 * q90, 0.0045, 0.018)
            source = "realized_vol_q75_q90"

    return {
        "risk_pct": risk_pct,
        "rr": 1.45,
        "source": source,
        "prices_n": len(prices),
        "fee_rate_roundtrip": FEE_RATE_ROUNDTRIP,
        "slippage_rate_roundtrip": SLIPPAGE_RATE_ROUNDTRIP,
    }


def open_count_global(con: sqlite3.Connection) -> int:
    if not exists(con, POSITION_TABLE):
        return 0
    try:
        return int(con.execute(f"""
            SELECT COUNT(*)
            FROM {qid(POSITION_TABLE)}
            WHERE UPPER(COALESCE(status,'')) IN ('OPEN','ACTIVE','RUNNING')
               OR (closed_at IS NULL AND opened_at IS NOT NULL)
        """).fetchone()[0])
    except Exception:
        return 0


def open_count_key(con: sqlite3.Connection, symbol: str, side: str, setup: str) -> int:
    if not exists(con, POSITION_TABLE):
        return 0
    try:
        return int(con.execute(f"""
            SELECT COUNT(*)
            FROM {qid(POSITION_TABLE)}
            WHERE UPPER(COALESCE(symbol,''))=?
              AND UPPER(COALESCE(side,''))=?
              AND COALESCE(setup,'')=?
              AND (
                UPPER(COALESCE(status,'')) IN ('OPEN','ACTIVE','RUNNING')
                OR (closed_at IS NULL AND opened_at IS NOT NULL)
              )
        """, (symbol.upper(), side.upper(), setup)).fetchone()[0])
    except Exception:
        return 0


def pending_intents(con: sqlite3.Connection) -> List[Dict[str, Any]]:
    if not exists(con, INTENT_TABLE):
        return []

    rows = con.execute(f"""
        SELECT *
        FROM {qid(INTENT_TABLE)}
        WHERE intent_state=?
          AND adapter_status=?
        ORDER BY id ASC
    """, (VALID_INTENT_STATE, VALID_ADAPTER_STATUS)).fetchall()

    return [dict(r) for r in rows]


def existing_map_for_intent(con: sqlite3.Connection, intent_id: int) -> Optional[Dict[str, Any]]:
    if not exists(con, MAP_TABLE):
        return None
    row = con.execute(
        f"SELECT * FROM {qid(MAP_TABLE)} WHERE intent_id=? ORDER BY id DESC LIMIT 1",
        (intent_id,)
    ).fetchone()
    return dict(row) if row else None


def reject_intent(con: sqlite3.Connection, intent: Dict[str, Any], reason: str, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    now = utc_now()
    intent_id = int(intent.get("id"))
    payload = safe_json(intent.get("payload"), {})
    payload["adapter_reject_reason"] = reason
    payload["adapter_rejected_at"] = now
    if extra:
        payload["adapter_reject_extra"] = extra

    update_dynamic(
        con,
        INTENT_TABLE,
        {
            "adapter_status": "REJECTED_BY_V17_8_1",
            "intent_state": "ADAPTER_REJECTED",
            "payload": json.dumps(payload, sort_keys=True),
        },
        "id=?",
        (intent_id,)
    )

    con.execute(f"""
        INSERT INTO {qid(AUDIT_TABLE)}
        (ts, version, event_type, key, intent_id, payload)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        now,
        VERSION,
        "INTENT_REJECTED",
        str(intent.get("key") or ""),
        intent_id,
        json.dumps({"reason": reason, "extra": extra or {}}, sort_keys=True),
    ))

    return {"intent_id": intent_id, "reason": reason, "extra": extra or {}}


def stable_position_id(intent: Dict[str, Any]) -> str:
    return "V17_8_1_" + sha256_obj({
        "intent_id": intent.get("id"),
        "intent_hash": intent.get("intent_hash"),
        "key": intent.get("key"),
        "version": VERSION,
    })[:20]


def bind_intent(con: sqlite3.Connection, intent: Dict[str, Any]) -> Dict[str, Any]:
    intent_id = int(intent.get("id"))
    payload = safe_json(intent.get("payload"), {})
    contract = safe_json(intent.get("contract_json"), {})

    if existing_map_for_intent(con, intent_id):
        return reject_intent(con, intent, "INTENT_ALREADY_MAPPED")

    symbol = str(intent.get("symbol") or "").upper()
    side = str(intent.get("side") or "").upper()
    setup = str(intent.get("setup") or "")
    key = str(intent.get("key") or f"{symbol}|{side}|{setup}")

    if intent.get("requested_mode") != VALID_MODE:
        return reject_intent(con, intent, "INVALID_REQUESTED_MODE")

    if intent.get("execution_permission") != VALID_PERMISSION:
        return reject_intent(con, intent, "INVALID_EXECUTION_PERMISSION")

    size_mult = fnum(intent.get("requested_size_mult"), 0.0) or 0.0
    if size_mult < MIN_SIZE_MULT or size_mult > MAX_SIZE_MULT:
        return reject_intent(con, intent, "SIZE_MULT_OUT_OF_BOUNDS", {"size_mult": size_mult})

    if open_count_global(con) >= MAX_OPEN_GLOBAL:
        return reject_intent(con, intent, "GLOBAL_OPEN_CAP_REACHED")

    if open_count_key(con, symbol, side, setup) >= MAX_OPEN_PER_KEY:
        return reject_intent(con, intent, "KEY_OPEN_CAP_REACHED")

    entry_price, price_meta = latest_market_price(con, symbol)
    if not entry_price or entry_price <= 0:
        return reject_intent(con, intent, "NO_FRESH_MARKET_PRICE", price_meta)

    risk = risk_model(con, symbol)
    risk_pct = float(risk["risk_pct"])
    rr = float(risk["rr"])
    risk_abs = entry_price * risk_pct

    if side == "LONG":
        stop = entry_price - risk_abs
        tp = entry_price + rr * risk_abs
    elif side == "SHORT":
        stop = entry_price + risk_abs
        tp = entry_price - rr * risk_abs
    else:
        return reject_intent(con, intent, "INVALID_SIDE", {"side": side})

    now = utc_now()
    spid = stable_position_id(intent)
    size_usd = DEFAULT_EQUITY_USD * size_mult
    risk_usd = size_usd * risk_pct

    pos_payload = {
        "adapter_version": VERSION,
        "stable_position_id": spid,
        "source": "MAX_QUANT_MANUAL_APPROVED_INTENT",
        "intent_id": intent_id,
        "intent_hash": intent.get("intent_hash"),
        "contract_hash": intent.get("contract_hash"),
        "key": key,
        "price_meta": price_meta,
        "risk_model": risk,
        "contract_json": contract,
        "governance_payload": payload,
        "paper_only": True,
        "real_execution_allowed": False,
    }

    position_values = {
        "opened_at": now,
        "closed_at": None,
        "symbol": symbol,
        "side": side,
        "family_name": "V17_8_1_MAX_QUANT_CANARY",
        "setup": setup,
        "profile": "MAX_QUANT_CANARY",
        "horizon_min": DEFAULT_HORIZON_MIN,
        "status": "OPEN",
        "entry_price": entry_price,
        "exit_price": None,
        "stop_price": stop,
        "stop_loss_price": stop,
        "take_profit_price": tp,
        "initial_risk_pct": risk_pct,
        "size_usd": size_usd,
        "pnl_usd": 0.0,
        "pnl_r": 0.0,
        "net_pnl_usd": 0.0,
        "net_pnl_r": 0.0,
        "mfe_r": 0.0,
        "mae_r": 0.0,
        "fee_usd_est": size_usd * FEE_RATE_ROUNDTRIP,
        "slippage_usd_est": size_usd * SLIPPAGE_RATE_ROUNDTRIP,
        "control_id": str(intent_id),
        "source_edge_id": str(intent.get("intent_hash") or ""),
        "reason": "V17_8_1_BIND_INTENT_TO_PAPER_CANARY",
        "payload": json.dumps(pos_payload, sort_keys=True),
        "risk_usd": risk_usd,
        "fees_usd": size_usd * FEE_RATE_ROUNDTRIP,
        "gross_usd": 0.0,
        "net_usd": 0.0,
        "gross_pnl_r": 0.0,
        "last_managed_at": now,
        "manager_version": VERSION,
        "manager_state": "OPEN_BY_V17_8_1_ADAPTER",
    }

    position_row_id = insert_dynamic(con, POSITION_TABLE, position_values)

    con.execute(f"""
        INSERT INTO {qid(MAP_TABLE)}
        (ts, version, intent_id, intent_hash, key, symbol, side, setup,
         position_row_id, stable_position_id, map_state, payload)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        now,
        VERSION,
        intent_id,
        str(intent.get("intent_hash") or ""),
        key,
        symbol,
        side,
        setup,
        position_row_id,
        spid,
        "OPEN_POSITION_CREATED",
        json.dumps(position_values, sort_keys=True),
    ))

    payload["adapter_bound_at"] = now
    payload["adapter_position_row_id"] = position_row_id
    payload["stable_position_id"] = spid

    update_dynamic(
        con,
        INTENT_TABLE,
        {
            "intent_state": "ADAPTER_BOUND_OPEN_PAPER_CANARY",
            "adapter_status": "OPENED_PAPER_CANARY_BY_V17_8_1",
            "payload": json.dumps(payload, sort_keys=True),
        },
        "id=?",
        (intent_id,)
    )

    con.execute(f"""
        INSERT INTO {qid(ADAPTER_TABLE)}
        (ts, version, event_type, intent_id, position_row_id, stable_position_id,
         key, symbol, side, setup, adapter_state, entry_price, requested_size_mult,
         size_usd, reason, payload)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        now,
        VERSION,
        "OPENED_PAPER_CANARY",
        intent_id,
        position_row_id,
        spid,
        key,
        symbol,
        side,
        setup,
        "OPEN",
        entry_price,
        size_mult,
        size_usd,
        "INTENT_BOUND_TO_POSITION",
        json.dumps(position_values, sort_keys=True),
    ))

    con.execute(f"""
        INSERT INTO {qid(AUDIT_TABLE)}
        (ts, version, event_type, key, intent_id, position_row_id, stable_position_id, payload)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        now,
        VERSION,
        "OPENED_PAPER_CANARY",
        key,
        intent_id,
        position_row_id,
        spid,
        json.dumps(position_values, sort_keys=True),
    ))

    return {
        "intent_id": intent_id,
        "position_row_id": position_row_id,
        "stable_position_id": spid,
        "symbol": symbol,
        "side": side,
        "setup": setup,
        "entry_price": entry_price,
        "stop": stop,
        "tp": tp,
        "size_usd": size_usd,
        "risk_pct": risk_pct,
        "price_meta": price_meta,
    }


def adapter_positions_open(con: sqlite3.Connection) -> List[Dict[str, Any]]:
    if not exists(con, POSITION_TABLE):
        return []

    rows = con.execute(f"""
        SELECT *
        FROM {qid(POSITION_TABLE)}
        WHERE (
            UPPER(COALESCE(status,'')) IN ('OPEN','ACTIVE','RUNNING')
            OR (closed_at IS NULL AND opened_at IS NOT NULL)
        )
    """).fetchall()

    out = []
    for r in rows:
        d = dict(r)
        p = safe_json(d.get("payload"), {})
        if isinstance(p, dict) and p.get("adapter_version") == VERSION:
            out.append(d)

    return out


def pnl_calc(side: str, entry: float, price: float, size_usd: float) -> Tuple[float, float]:
    if side == "LONG":
        ret = (price - entry) / entry
    else:
        ret = (entry - price) / entry
    return ret, size_usd * ret


def close_position(con: sqlite3.Connection, pos: Dict[str, Any], exit_price: float, reason: str) -> Dict[str, Any]:
    now = utc_now()

    position_row_id = int(pos.get("id"))
    payload = safe_json(pos.get("payload"), {})
    key = str(payload.get("key") or "")
    spid = str(payload.get("stable_position_id") or "")

    symbol = str(pos.get("symbol") or "").upper()
    side = str(pos.get("side") or "").upper()
    setup = str(pos.get("setup") or "")

    entry = fnum(pos.get("entry_price"), None)
    size_usd = fnum(pos.get("size_usd"), 0.0) or 0.0
    risk_pct = fnum(pos.get("initial_risk_pct"), 0.008) or 0.008

    if not entry or entry <= 0:
        raise RuntimeError("BAD_ENTRY_PRICE_ON_CLOSE")

    ret, gross_usd = pnl_calc(side, entry, exit_price, size_usd)
    gross_r = ret / risk_pct if risk_pct > 0 else 0.0

    fees = size_usd * FEE_RATE_ROUNDTRIP
    slip = size_usd * SLIPPAGE_RATE_ROUNDTRIP
    net_usd = gross_usd - fees - slip
    net_r = net_usd / max(size_usd * risk_pct, 1e-9)

    mfe_r = max(fnum(pos.get("mfe_r"), 0.0) or 0.0, gross_r)
    mae_r = min(fnum(pos.get("mae_r"), 0.0) or 0.0, gross_r)

    update_dynamic(
        con,
        POSITION_TABLE,
        {
            "closed_at": now,
            "status": "CLOSED",
            "exit_price": exit_price,
            "pnl_usd": gross_usd,
            "pnl_r": gross_r,
            "net_pnl_usd": net_usd,
            "net_pnl_r": net_r,
            "mfe_r": mfe_r,
            "mae_r": mae_r,
            "fee_usd_est": fees,
            "slippage_usd_est": slip,
            "fees_usd": fees,
            "gross_usd": gross_usd,
            "net_usd": net_usd,
            "gross_pnl_r": gross_r,
            "last_managed_at": now,
            "manager_version": VERSION,
            "manager_state": reason,
            "reason": reason,
        },
        "id=?",
        (position_row_id,)
    )

    outcome_payload = {
        "adapter_version": VERSION,
        "stable_position_id": spid,
        "position_row_id": position_row_id,
        "key": key,
        "exit_reason": reason,
        "entry": entry,
        "exit": exit_price,
        "gross_r": gross_r,
        "net_r": net_r,
        "fees": fees,
        "slippage": slip,
        "paper_only": True,
        "clean_for_evidence": True,
    }

    if exists(con, OUTCOME_TABLE):
        insert_dynamic(con, OUTCOME_TABLE, {
            "position_id": str(position_row_id),
            "symbol": symbol,
            "side": side,
            "setup": setup,
            "opened_at": pos.get("opened_at"),
            "closed_at": now,
            "status": "CLOSED",
            "pnl_usd": net_usd,
            "pnl_r": net_r,
            "provenance": "V17_8_1_MAX_QUANT_PAPER_CANARY_ADAPTER",
            "clean_for_evidence": 1,
            "evidence_weight": 1.0,
            "excluded_reason": "",
            "updated_at": now,
            "payload": json.dumps(outcome_payload, sort_keys=True),
        })

    con.execute(f"""
        INSERT INTO {qid(ADAPTER_TABLE)}
        (ts, version, event_type, position_row_id, stable_position_id, key,
         symbol, side, setup, adapter_state, entry_price, exit_price, size_usd,
         pnl_r, net_pnl_r, reason, payload)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        now,
        VERSION,
        "CLOSED_PAPER_CANARY",
        position_row_id,
        spid,
        key,
        symbol,
        side,
        setup,
        "CLOSED",
        entry,
        exit_price,
        size_usd,
        gross_r,
        net_r,
        reason,
        json.dumps(outcome_payload, sort_keys=True),
    ))

    return {
        "position_row_id": position_row_id,
        "stable_position_id": spid,
        "symbol": symbol,
        "side": side,
        "setup": setup,
        "exit_price": exit_price,
        "gross_r": gross_r,
        "net_r": net_r,
        "reason": reason,
    }


def manage_position(con: sqlite3.Connection, pos: Dict[str, Any]) -> Dict[str, Any]:
    position_row_id = int(pos.get("id"))
    symbol = str(pos.get("symbol") or "").upper()
    side = str(pos.get("side") or "").upper()

    entry = fnum(pos.get("entry_price"), None)
    stop = fnum(pos.get("stop_price"), fnum(pos.get("stop_loss_price"), None))
    tp = fnum(pos.get("take_profit_price"), None)
    size_usd = fnum(pos.get("size_usd"), 0.0) or 0.0
    risk_pct = fnum(pos.get("initial_risk_pct"), 0.008) or 0.008
    horizon = inum(pos.get("horizon_min"), DEFAULT_HORIZON_MIN)

    if not entry or not stop or not tp:
        return {"position_row_id": position_row_id, "state": "SKIPPED_BAD_POSITION_FIELDS"}

    price, meta = latest_market_price(con, symbol)
    if not price:
        return {"position_row_id": position_row_id, "state": "SKIPPED_NO_FRESH_PRICE", "meta": meta}

    ret, gross_usd = pnl_calc(side, entry, price, size_usd)
    gross_r = ret / risk_pct if risk_pct > 0 else 0.0

    mfe_r = max(fnum(pos.get("mfe_r"), 0.0) or 0.0, gross_r)
    mae_r = min(fnum(pos.get("mae_r"), 0.0) or 0.0, gross_r)

    update_dynamic(
        con,
        POSITION_TABLE,
        {
            "mfe_r": mfe_r,
            "mae_r": mae_r,
            "pnl_usd": gross_usd,
            "pnl_r": gross_r,
            "last_managed_at": utc_now(),
            "manager_version": VERSION,
            "manager_state": "OPEN_MARKED_TO_MARKET",
        },
        "id=?",
        (position_row_id,)
    )

    exit_reason = None

    if side == "LONG":
        if price <= stop:
            exit_reason = "STOP_LOSS_HIT"
        elif price >= tp:
            exit_reason = "TAKE_PROFIT_HIT"
    else:
        if price >= stop:
            exit_reason = "STOP_LOSS_HIT"
        elif price <= tp:
            exit_reason = "TAKE_PROFIT_HIT"

    age = age_minutes(pos.get("opened_at"))
    if exit_reason is None and age is not None and age >= horizon:
        exit_reason = "TIME_STOP_HIT"

    if exit_reason:
        return close_position(con, pos, price, exit_reason)

    return {
        "position_row_id": position_row_id,
        "symbol": symbol,
        "side": side,
        "price": price,
        "gross_r": gross_r,
        "state": "OPEN_MANAGED",
        "price_meta": meta,
    }


def reconcile(con: sqlite3.Connection) -> List[Dict[str, Any]]:
    items = []

    if not exists(con, MAP_TABLE):
        return items

    rows = con.execute(f"""
        SELECT *
        FROM {qid(MAP_TABLE)}
        WHERE map_state='OPEN_POSITION_CREATED'
        ORDER BY id DESC
        LIMIT 50
    """).fetchall()

    for rr in rows:
        m = dict(rr)
        pos_id = m.get("position_row_id")
        if pos_id is None:
            continue

        pos = None
        if exists(con, POSITION_TABLE):
            row = con.execute(
                f"SELECT * FROM {qid(POSITION_TABLE)} WHERE id=?",
                (pos_id,)
            ).fetchone()
            pos = dict(row) if row else None

        if not pos:
            update_dynamic(
                con,
                MAP_TABLE,
                {"map_state": "BROKEN_POSITION_MISSING"},
                "id=?",
                (m.get("id"),)
            )
            items.append({"map_id": m.get("id"), "state": "BROKEN_POSITION_MISSING"})

        elif str(pos.get("status") or "").upper() == "CLOSED":
            update_dynamic(
                con,
                MAP_TABLE,
                {"map_state": "POSITION_CLOSED"},
                "id=?",
                (m.get("id"),)
            )
            items.append({"map_id": m.get("id"), "state": "POSITION_CLOSED"})

    return items


def run_once(open_only: bool = False, manage_only: bool = False, dry_run: bool = False) -> Dict[str, Any]:
    OUT.mkdir(parents=True, exist_ok=True)

    with AdapterLock():
        con = connect_rw()
        create_tables(con)

        quick = con.execute("PRAGMA quick_check").fetchone()[0]

        pending = [] if manage_only else pending_intents(con)
        opened = []
        managed = []
        closed = []
        rejected = []
        errors = []
        reconciled = []

        try:
            con.execute("BEGIN IMMEDIATE")

            opened_this_cycle = 0

            for intent in pending:
                if opened_this_cycle >= MAX_NEW_OPENS_PER_CYCLE:
                    break

                try:
                    if dry_run:
                        rejected.append({"intent_id": intent.get("id"), "reason": "DRY_RUN_NO_BIND"})
                    else:
                        result = bind_intent(con, intent)
                        if "position_row_id" in result:
                            opened.append(result)
                            opened_this_cycle += 1
                        else:
                            rejected.append(result)
                except Exception as e:
                    errors.append({"phase": "bind", "intent_id": intent.get("id"), "error": repr(e)})

            if not open_only:
                for pos in adapter_positions_open(con):
                    try:
                        result = manage_position(con, pos)
                        managed.append(result)
                        if result.get("reason"):
                            closed.append(result)
                    except Exception as e:
                        errors.append({"phase": "manage", "position_id": pos.get("id"), "error": repr(e)})

            reconciled = reconcile(con)

            report = {
                "version": VERSION,
                "generated_utc": utc_now(),
                "quick_check": quick,
                "dry_run": dry_run,
                "pending_intents": len(pending),
                "opened_positions": opened,
                "managed_positions": managed,
                "closed_positions": closed,
                "rejected_intents": rejected,
                "reconciled_items": reconciled,
                "errors": errors,
            }

            con.execute(f"""
                INSERT INTO {qid(HEALTH_TABLE)}
                (ts, version, quick_check, pending_intents, opened_positions,
                 managed_positions, closed_positions, rejected_intents,
                 reconciled_items, errors, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                report["generated_utc"],
                VERSION,
                quick,
                len(pending),
                len(opened),
                len(managed),
                len(closed),
                len(rejected),
                len(reconciled),
                len(errors),
                json.dumps(report, sort_keys=True),
            ))

            if dry_run:
                con.rollback()
            else:
                con.commit()

        except Exception as e:
            con.rollback()
            report = {
                "version": VERSION,
                "generated_utc": utc_now(),
                "quick_check": quick,
                "dry_run": dry_run,
                "pending_intents": len(pending),
                "opened_positions": opened,
                "managed_positions": managed,
                "closed_positions": closed,
                "rejected_intents": rejected,
                "reconciled_items": reconciled,
                "errors": errors + [{"phase": "transaction", "error": repr(e)}],
            }

        finally:
            con.close()

    (OUT / "adapter_latest.json").write_text(json.dumps(report, indent=2, sort_keys=True))
    write_summary(report)
    return report


def write_summary(report: Dict[str, Any]) -> None:
    lines = []

    lines.append("# V17.8.1 Institutional Paper Canary Adapter PRO/MAX")
    lines.append("")
    lines.append(f"- UTC: `{report['generated_utc']}`")
    lines.append(f"- DB quick_check: `{report['quick_check']}`")
    lines.append(f"- Dry run: `{report['dry_run']}`")
    lines.append(f"- Pending intents: `{report['pending_intents']}`")
    lines.append(f"- Opened positions: `{len(report['opened_positions'])}`")
    lines.append(f"- Managed positions: `{len(report['managed_positions'])}`")
    lines.append(f"- Closed positions: `{len(report['closed_positions'])}`")
    lines.append(f"- Rejected intents: `{len(report['rejected_intents'])}`")
    lines.append(f"- Reconciled items: `{len(report['reconciled_items'])}`")
    lines.append(f"- Errors: `{len(report['errors'])}`")
    lines.append("")

    lines.append("## Opened")
    if not report["opened_positions"]:
        lines.append("- none")
    else:
        for r in report["opened_positions"]:
            lines.append(
                f"- `{r['symbol']} {r['side']} {r['setup']}` "
                f"position_row_id={r['position_row_id']} "
                f"entry={fmt(r['entry_price'])} stop={fmt(r['stop'])} tp={fmt(r['tp'])} "
                f"size_usd={fmt(r['size_usd'])} risk_pct={fmt(r['risk_pct'])}"
            )

    lines.append("")
    lines.append("## Managed / closed")
    if not report["managed_positions"]:
        lines.append("- none")
    else:
        for r in report["managed_positions"][:30]:
            lines.append(
                f"- `{r.get('symbol')} {r.get('side')}` "
                f"position={r.get('position_row_id')} "
                f"state={r.get('state') or r.get('reason')} "
                f"r={fmt(r.get('gross_r'))} net_r={fmt(r.get('net_r'))}"
            )

    lines.append("")
    lines.append("## Rejected")
    if not report["rejected_intents"]:
        lines.append("- none")
    else:
        for r in report["rejected_intents"]:
            lines.append(f"- `{r}`")

    lines.append("")
    lines.append("## Reconciled")
    if not report["reconciled_items"]:
        lines.append("- none")
    else:
        for r in report["reconciled_items"]:
            lines.append(f"- `{r}`")

    lines.append("")
    lines.append("## Errors")
    if not report["errors"]:
        lines.append("- none")
    else:
        for e in report["errors"]:
            lines.append(f"- `{e}`")

    (OUT / "adapter_summary.md").write_text("\n".join(lines))


def fmt(x: Any) -> str:
    v = fnum(x, None)
    return "-" if v is None else f"{v:.6f}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--open-only", action="store_true")
    ap.add_argument("--manage-only", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    report = run_once(
        open_only=args.open_only,
        manage_only=args.manage_only,
        dry_run=args.dry_run,
    )

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print("===== V17.8.1 INSTITUTIONAL PAPER CANARY ADAPTER PRO/MAX =====")
        print("quick_check:", report["quick_check"])
        print("dry_run:", report["dry_run"])
        print("pending_intents:", report["pending_intents"])
        print("opened_positions:", len(report["opened_positions"]))
        print("managed_positions:", len(report["managed_positions"]))
        print("closed_positions:", len(report["closed_positions"]))
        print("rejected_intents:", len(report["rejected_intents"]))
        print("reconciled_items:", len(report["reconciled_items"]))
        print("errors:", len(report["errors"]))

        for r in report["opened_positions"]:
            print("OPENED", r)
        for r in report["managed_positions"][:10]:
            print("MANAGED", r)
        for r in report["rejected_intents"]:
            print("REJECTED", r)
        for e in report["errors"]:
            print("ERROR", e)

    return 0 if report.get("quick_check") == "ok" and not report.get("errors") else 2


if __name__ == "__main__":
    raise SystemExit(main())
