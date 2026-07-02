#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

VERSION = "V24_4_CANONICAL_PAPER_ACCOUNTING"

PRICE_TABLE = "institutional_v24_market_price_latest"
POSITION_TABLE = "paper_micro_canary_positions_v11"

START_EQUITY = 100000.0
MAX_PRICE_AGE_MIN = 5.0

DEFAULT_STOP_RISK_PCT = 0.0045
DEFAULT_TP_REWARD_PCT = 0.0065
STOP_SLIPPAGE = 0.0006
TP_SLIPPAGE = 0.0003
ROUNDTRIP_FEE_RATE = 0.0014

SYMBOL_BOUNDS = {
    "BTCUSDT": (10000.0, 350000.0),
    "ETHUSDT": (300.0, 30000.0),
}


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


def age_min(ts: Any) -> Optional[float]:
    d = parse_ts(ts)
    if not d:
        return None
    return max(0.0, (datetime.now(timezone.utc) - d).total_seconds() / 60.0)


def table_exists(con: sqlite3.Connection, table: str) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone() is not None


def cols(con: sqlite3.Connection, table: str):
    if not table_exists(con, table):
        return []
    return [r[1] for r in con.execute(f"PRAGMA table_info({qid(table)})")]


def canonical_price(con: sqlite3.Connection, symbol: str) -> Tuple[Optional[float], Dict[str, Any]]:
    symbol = str(symbol or "").upper().strip()

    if symbol not in SYMBOL_BOUNDS:
        return None, {"reason": "SYMBOL_NOT_ALLOWED", "symbol": symbol}

    if not table_exists(con, PRICE_TABLE):
        return None, {"reason": "CANONICAL_PRICE_TABLE_MISSING", "symbol": symbol}

    c = cols(con, PRICE_TABLE)
    required = {"symbol", "price", "ts"}
    if not required.issubset(set(c)):
        return None, {"reason": "CANONICAL_PRICE_SCHEMA_INVALID", "columns": c}

    r = con.execute(
        f"""
        SELECT *
        FROM {qid(PRICE_TABLE)}
        WHERE UPPER(symbol)=?
        ORDER BY ts DESC
        LIMIT 1
        """,
        (symbol,),
    ).fetchone()

    if not r:
        return None, {"reason": "CANONICAL_PRICE_ROW_MISSING", "symbol": symbol}

    d = dict(r)
    price = fnum(d.get("price"))
    a = age_min(d.get("ts"))
    lo, hi = SYMBOL_BOUNDS[symbol]

    if price is None or price <= 0:
        return None, {"reason": "CANONICAL_PRICE_INVALID", "row": d}

    if not (lo <= price <= hi):
        return None, {
            "reason": "CANONICAL_PRICE_OUT_OF_BOUNDS",
            "symbol": symbol,
            "price": price,
            "bounds": [lo, hi],
            "row": d,
        }

    if a is None or a > MAX_PRICE_AGE_MIN:
        return None, {
            "reason": "CANONICAL_PRICE_STALE",
            "symbol": symbol,
            "price": price,
            "age_min": a,
            "row": d,
        }

    return float(price), {
        "reason": "CANONICAL_PRICE_OK",
        "symbol": symbol,
        "price": float(price),
        "age_min": round(a, 4),
        "source_table": PRICE_TABLE,
        "source_ts": d.get("ts"),
    }


def stop_take_prices(side: str, entry: float) -> Tuple[float, float]:
    side = str(side).upper()
    if side == "LONG":
        return entry * (1.0 - DEFAULT_STOP_RISK_PCT), entry * (1.0 + DEFAULT_TP_REWARD_PCT)
    if side == "SHORT":
        return entry * (1.0 + DEFAULT_STOP_RISK_PCT), entry * (1.0 - DEFAULT_TP_REWARD_PCT)
    raise ValueError("BAD_SIDE")


def trigger_state(side: str, price: float, stop: float, tp: float) -> Optional[str]:
    side = str(side).upper()
    if side == "LONG":
        if price <= stop:
            return "STOP_LOSS_HIT"
        if price >= tp:
            return "TAKE_PROFIT_HIT"
    elif side == "SHORT":
        if price >= stop:
            return "STOP_LOSS_HIT"
        if price <= tp:
            return "TAKE_PROFIT_HIT"
    return None


def canonical_exit_price(side: str, trigger: str, stop: float, tp: float) -> float:
    side = str(side).upper()
    trigger = str(trigger).upper()

    if trigger == "STOP_LOSS_HIT":
        if side == "LONG":
            return stop * (1.0 - STOP_SLIPPAGE)
        return stop * (1.0 + STOP_SLIPPAGE)

    if trigger == "TAKE_PROFIT_HIT":
        if side == "LONG":
            return tp * (1.0 - TP_SLIPPAGE)
        return tp * (1.0 + TP_SLIPPAGE)

    raise ValueError("BAD_TRIGGER")


def gross_return(side: str, entry: float, exit_price: float) -> float:
    side = str(side).upper()
    if side == "LONG":
        return (exit_price - entry) / entry
    if side == "SHORT":
        return (entry - exit_price) / entry
    raise ValueError("BAD_SIDE")


def compute_pnl(side: str, entry: float, exit_price: float, stop: float, size_usd: float) -> Dict[str, Any]:
    ret = gross_return(side, entry, exit_price)
    gross_usd = ret * size_usd
    fee_usd = abs(size_usd) * ROUNDTRIP_FEE_RATE
    net_usd = gross_usd - fee_usd

    risk_pct = abs(stop - entry) / entry if entry else None
    gross_r = ret / risk_pct if risk_pct and risk_pct > 0 else None
    net_r = (net_usd / size_usd) / risk_pct if size_usd and risk_pct and risk_pct > 0 else None

    return {
        "gross_return": ret,
        "gross_usd": gross_usd,
        "fee_usd": fee_usd,
        "net_usd": net_usd,
        "risk_pct": risk_pct,
        "gross_r": gross_r,
        "net_r": net_r,
    }


def is_exit_outlier(entry: float, exit_price: float, stop: Optional[float], tp: Optional[float], trigger: str) -> bool:
    if not entry or not exit_price:
        return True

    ratio = exit_price / entry
    if ratio > 1.15 or ratio < 0.85:
        return True

    trigger = str(trigger or "").upper()
    if "STOP" in trigger and stop:
        expected_mid = stop
        if abs(exit_price - expected_mid) / expected_mid > 0.02:
            return True

    if "TAKE" in trigger and tp:
        expected_mid = tp
        if abs(exit_price - expected_mid) / expected_mid > 0.02:
            return True

    return False


def safe_json(x: Any) -> str:
    return json.dumps(x, sort_keys=True, default=str)
