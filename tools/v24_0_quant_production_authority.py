#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sqlite3
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path("/storage/emulated/0/Download/joan_crypto_ai_pro_v14")
DB = ROOT / "data" / "joanbot_v14.sqlite"
OUT = ROOT / "data" / "v24_0_quant_authority"
OUT.mkdir(parents=True, exist_ok=True)

VERSION = "V24_0_INSTITUTIONAL_QUANT_PRODUCTION_AUTHORITY"

BRAIN_TABLE = "institutional_quant_brain_v17_5_1"
INTENT_TABLE = "institutional_quant_canary_execution_intents_v17_7_2"
QUEUE_TABLE = "institutional_micro_canary_contract_queue_v17_6_1"
POSITION_TABLE = "paper_micro_canary_positions_v11"

V24_DECISION_TABLE = "institutional_v24_quant_decisions"
V24_EVIDENCE_TABLE = "institutional_v24_edge_evidence"

START_EQUITY = 100000.0

BRAIN_TTL_MIN = 20.0
PRICE_TTL_MIN = 12.0
MAX_DAILY_V24_ATTEMPTS = 4
MAX_ACTIVE_V24_INTENTS = 2
KEY_COOLDOWN_MIN = 90.0
MAX_OPEN_GLOBAL = 2
MAX_OPEN_PER_KEY = 1

MIN_SIZE_MULT = 0.005
MAX_SIZE_MULT = 0.012
DISCOVERY_SIZE_MULT = 0.005

VALID_INTENT_STATE = "MAX_QUANT_MANUAL_APPROVED_PENDING_PAPER_ADAPTER"
VALID_ADAPTER_STATUS = "PENDING_ADAPTER_BINDING"
VALID_MODE = "PAPER_MICRO_CANARY_ONLY"
VALID_PERMISSION = "PAPER_ONLY_NO_REAL_EXECUTION"

BAD_AUTH_TOKENS = ("HARD", "FATAL", "BLOCKED", "QUARANTINE")
PRICE_COLS = ("price", "last_price", "mark_price", "close", "c", "mid", "mid_price")
R_COLS = ("net_pnl_r", "pnl_r", "gross_pnl_r", "realized_r", "r")
USD_COLS = ("net_pnl_usd", "pnl_usd", "realized_pnl_usd", "gross_pnl_usd")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def utc_day() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def qid(x: str) -> str:
    return '"' + str(x).replace('"', '""') + '"'


def parse_dt(x: Any) -> Optional[datetime]:
    if not x:
        return None
    s = str(x).strip().replace("Z", "+00:00")
    try:
        d = datetime.fromisoformat(s)
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.astimezone(timezone.utc)
    except Exception:
        return None


def age_min(ts: Any) -> Optional[float]:
    d = parse_dt(ts)
    if not d:
        return None
    return round(max(0.0, (datetime.now(timezone.utc) - d).total_seconds() / 60.0), 3)


def fnum(x: Any, default: Optional[float] = None) -> Optional[float]:
    if x is None:
        return default
    try:
        if isinstance(x, str) and x.strip().lower() in ("", "none", "nan", "null"):
            return default
        v = float(x)
        if math.isnan(v) or math.isinf(v):
            return default
        return v
    except Exception:
        return default


def inum(x: Any, default: int = 0) -> int:
    try:
        return int(float(x))
    except Exception:
        return default


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def safe_json(x: Any, fallback: Any = None) -> Any:
    if x is None:
        return fallback
    if isinstance(x, (dict, list)):
        return x
    try:
        return json.loads(str(x))
    except Exception:
        return fallback


def sha256_obj(obj: Any) -> str:
    raw = json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


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


def columns(con: sqlite3.Connection, table: str) -> List[str]:
    if not table_exists(con, table):
        return []
    return [r[1] for r in con.execute(f"PRAGMA table_info({qid(table)})")]


def insert_dynamic(con: sqlite3.Connection, table: str, values: Dict[str, Any]) -> int:
    cols = set(columns(con, table))
    data = {k: v for k, v in values.items() if k in cols}
    if not data:
        raise RuntimeError(f"NO_MATCHING_COLUMNS_FOR_INSERT:{table}")
    names = list(data.keys())
    sql = f'''
    INSERT OR IGNORE INTO {qid(table)}
    ({",".join(qid(c) for c in names)})
    VALUES ({",".join("?" for _ in names)})
    '''
    cur = con.execute(sql, [data[c] for c in names])
    return int(cur.lastrowid or 0)


def update_dynamic(con: sqlite3.Connection, table: str, values: Dict[str, Any], where: str, params: List[Any]) -> None:
    cols = set(columns(con, table))
    data = {k: v for k, v in values.items() if k in cols}
    if not data:
        return
    names = list(data.keys())
    sql = f'''
    UPDATE {qid(table)}
    SET {",".join(qid(c) + "=?" for c in names)}
    WHERE {where}
    '''
    con.execute(sql, [data[c] for c in names] + params)


def create_v24_tables(con: sqlite3.Connection) -> None:
    con.execute(f'''
    CREATE TABLE IF NOT EXISTS {qid(V24_EVIDENCE_TABLE)} (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL,
        version TEXT NOT NULL,
        key TEXT NOT NULL,
        symbol TEXT,
        side TEXT,
        setup TEXT,
        sample_n INTEGER,
        mean_r REAL,
        lcb95_r REAL,
        cvar5_r REAL,
        winrate REAL,
        profit_factor REAL,
        worst_r REAL,
        best_r REAL,
        evidence_state TEXT,
        payload TEXT
    )
    ''')

    con.execute(f'''
    CREATE TABLE IF NOT EXISTS {qid(V24_DECISION_TABLE)} (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL,
        version TEXT NOT NULL,
        decision_hash TEXT NOT NULL UNIQUE,
        key TEXT NOT NULL,
        symbol TEXT,
        side TEXT,
        setup TEXT,
        decision TEXT,
        action TEXT,
        quality_score REAL,
        brain_score REAL,
        size_mult REAL,
        brain_age_min REAL,
        price_age_min REAL,
        evidence_state TEXT,
        reasons TEXT,
        emitted_intent_id INTEGER,
        payload TEXT
    )
    ''')

    con.execute(f"CREATE INDEX IF NOT EXISTS idx_v24_dec_ts ON {qid(V24_DECISION_TABLE)}(ts)")
    con.execute(f"CREATE INDEX IF NOT EXISTS idx_v24_dec_key ON {qid(V24_DECISION_TABLE)}(key, ts)")
    con.execute(f"CREATE INDEX IF NOT EXISTS idx_v24_evd_key ON {qid(V24_EVIDENCE_TABLE)}(key, ts)")


def latest_price(con: sqlite3.Connection, symbol: str) -> Dict[str, Any]:
    try:
        from joanbot.institutional.canonical_market_data_contract_v24_9_final import canonical_price_snapshot
        snap = canonical_price_snapshot(con, symbol)
        return {
            "ok": bool(snap.get("ok")),
            "symbol": symbol,
            "price": snap.get("price"),
            "ts": snap.get("ts"),
            "age_min": snap.get("age_min"),
            "source_table": snap.get("source_table"),
            "source_col": snap.get("source_col"),
            "reason": snap.get("reason"),
            "canonical": True,
            "source": snap.get("source"),
            "version": snap.get("version"),
        }
    except Exception as e:
        return {
            "ok": False,
            "symbol": symbol,
            "price": None,
            "ts": None,
            "age_min": None,
            "source_table": None,
            "source_col": None,
            "reason": "MAX_CANONICAL_PRICE_GATE_EXCEPTION:" + repr(e),
            "canonical": True,
        }


def brain_candidates(con: sqlite3.Connection) -> List[Dict[str, Any]]:
    if not table_exists(con, BRAIN_TABLE):
        return []

    cols = columns(con, BRAIN_TABLE)
    if "ts" not in cols:
        return []

    rows = con.execute(f"SELECT * FROM {qid(BRAIN_TABLE)} ORDER BY ts DESC LIMIT 80").fetchall()
    out = []

    seen = set()
    for r in rows:
        d = dict(r)
        symbol = str(d.get("symbol") or "").upper()
        side = str(d.get("side") or "").upper()
        setup = str(d.get("setup") or "")
        key = str(d.get("key") or f"{symbol}|{side}|{setup}")
        if not symbol or not side or not setup:
            continue
        if key in seen:
            continue
        seen.add(key)

        score = fnum(d.get("brain_score"), fnum(d.get("score"), 0.0)) or 0.0
        auth = str(d.get("authority_state") or d.get("brain_state") or "")
        out.append({
            "row": d,
            "ts": d.get("ts"),
            "age_min": age_min(d.get("ts")),
            "key": key,
            "symbol": symbol,
            "side": side,
            "setup": setup,
            "brain_score": score,
            "authority_state": auth,
        })

    out.sort(key=lambda x: (x["age_min"] is not None and x["age_min"] <= BRAIN_TTL_MIN, x["brain_score"]), reverse=True)
    return out


def read_r_values(con: sqlite3.Connection, key: str, symbol: str, side: str, setup: str) -> List[float]:
    """V24.6B: evidence uses only memory-hygiene approved outcomes."""
    from joanbot.institutional.kernel_contract_v24_6 import clean_r_values
    return clean_r_values(con, key, symbol, side, setup)


def evidence_for(con: sqlite3.Connection, c: Dict[str, Any]) -> Dict[str, Any]:
    vals = read_r_values(con, c["key"], c["symbol"], c["side"], c["setup"])
    n = len(vals)

    if n == 0:
        e = {
            "sample_n": 0,
            "mean_r": None,
            "lcb95_r": None,
            "cvar5_r": None,
            "winrate": None,
            "profit_factor": None,
            "worst_r": None,
            "best_r": None,
            "evidence_state": "NO_LIVE_OUTCOME_SAMPLE",
        }
    else:
        mean = statistics.mean(vals)
        std = statistics.pstdev(vals) if n > 1 else 0.0
        lcb = mean - 1.96 * std / math.sqrt(max(1, n))
        sorted_vals = sorted(vals)
        tail_n = max(1, math.ceil(n * 0.05))
        cvar = statistics.mean(sorted_vals[:tail_n])
        wins = [x for x in vals if x > 0]
        losses = [x for x in vals if x < 0]
        gp = sum(wins)
        gl = abs(sum(losses))
        pf = 99.0 if gp > 0 and gl == 0 else (gp / gl if gl > 0 else None)
        wr = len(wins) / n if n else None

        if n >= 30 and lcb > 0 and pf and pf >= 1.15:
            state = "LIVE_EDGE_VALIDATED"
        elif n >= 10 and mean > 0 and pf and pf >= 1.05:
            state = "LIVE_EDGE_PROMISING"
        elif mean > 0:
            state = "LIVE_EDGE_WEAK_SAMPLE"
        else:
            state = "LIVE_EDGE_NEGATIVE_OR_UNPROVEN"

        e = {
            "sample_n": n,
            "mean_r": mean,
            "lcb95_r": lcb,
            "cvar5_r": cvar,
            "winrate": wr,
            "profit_factor": pf,
            "worst_r": min(vals),
            "best_r": max(vals),
            "evidence_state": state,
        }

    con.execute(
        f'''
        INSERT INTO {qid(V24_EVIDENCE_TABLE)}
        (ts, version, key, symbol, side, setup, sample_n, mean_r, lcb95_r,
         cvar5_r, winrate, profit_factor, worst_r, best_r, evidence_state, payload)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''',
        (
            utc_now(), VERSION, c["key"], c["symbol"], c["side"], c["setup"],
            e["sample_n"], e["mean_r"], e["lcb95_r"], e["cvar5_r"],
            e["winrate"], e["profit_factor"], e["worst_r"], e["best_r"],
            e["evidence_state"], json.dumps({"r_values_tail": vals[-20:]}, sort_keys=True)
        ),
    )

    return e


def open_positions_count(con: sqlite3.Connection, key: Optional[str] = None) -> int:
    if not table_exists(con, POSITION_TABLE):
        return 0

    cols = columns(con, POSITION_TABLE)
    rows = con.execute(f"SELECT * FROM {qid(POSITION_TABLE)}").fetchall()
    n = 0

    for row in rows:
        d = dict(row)
        status = str(d.get("status") or d.get("state") or "").upper()
        if status not in ("OPEN", "OPENED", "ACTIVE"):
            continue
        if key and "key" in cols and d.get("key") and str(d.get("key")) != key:
            continue
        n += 1

    return n


# === V24.2_CANONICAL_ATTEMPT_ACCOUNTING_START ===
# Institutional accounting rule:
# Rejections caused by infrastructure/data freshness must not consume alpha budget.
# They must not consume daily attempt cap and must not trigger key cooldown.
# Risk/edge rejections still count normally.

V24_INFRA_REJECT_REASONS = {
    "NO_FRESH_MARKET_PRICE",
    "NO_PRICE_FOUND",
    "PRICE_STALE",
    "PRICE_TOO_OLD",
    "MARKET_DATA_STALE",
    "DATA_PLANE_STALE",
    "DB_LOCKED",
    "SQLITE_BUSY",
}

def _v24_2_row_get(row, key, idx=None):
    try:
        if hasattr(row, "keys") and key in row.keys():
            return row[key]
    except Exception:
        pass
    if idx is not None:
        try:
            return row[idx]
        except Exception:
            return None
    return None

def _v24_2_json_load(x):
    import json
    if x is None:
        return {}
    if isinstance(x, dict):
        return x
    try:
        return json.loads(str(x))
    except Exception:
        return {}

def _v24_2_parse_ts(ts):
    from datetime import datetime, timezone
    if ts is None:
        return None
    s = str(ts).strip()
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None

def _v24_2_age_min(ts):
    from datetime import datetime, timezone
    d = _v24_2_parse_ts(ts)
    if d is None:
        return None
    return max(0.0, (datetime.now(timezone.utc) - d).total_seconds() / 60.0)

def _v24_2_audit_reason_for_intent(con, intent_id):
    table = "institutional_paper_canary_adapter_audit_v17_8_1"
    if intent_id is None or not table_exists(con, table):
        return None

    try:
        r = con.execute(
            f"""
            SELECT payload
            FROM {qid(table)}
            WHERE intent_id=?
            ORDER BY id DESC
            LIMIT 1
            """,
            (intent_id,),
        ).fetchone()
    except Exception:
        return None

    if not r:
        return None

    payload = _v24_2_row_get(r, "payload", 0)
    j = _v24_2_json_load(payload)
    extra = j.get("extra") if isinstance(j.get("extra"), dict) else {}

    for box in (extra, j):
        if not isinstance(box, dict):
            continue
        for k in ("error", "reason", "adapter_reject_reason"):
            v = box.get(k)
            if v:
                return str(v)

    return None

def _v24_2_is_infra_rejected(con, row):
    status = str(_v24_2_row_get(row, "adapter_status", 2) or "").upper()
    state = str(_v24_2_row_get(row, "intent_state", 3) or "").upper()

    if "REJECT" not in status and "REJECT" not in state:
        return False

    intent_id = _v24_2_row_get(row, "id", 0)
    reason = str(_v24_2_audit_reason_for_intent(con, intent_id) or "").upper()

    if not reason:
        return False

    return any(x in reason for x in V24_INFRA_REJECT_REASONS)

def v24_attempts_today(con: sqlite3.Connection) -> int:
    if not table_exists(con, INTENT_TABLE):
        return 0

    try:
        rows = list(con.execute(
            f"""
            SELECT id, ts, adapter_status, intent_state, version
            FROM {qid(INTENT_TABLE)}
            WHERE substr(ts,1,10)=?
              AND version=?
            ORDER BY id DESC
            """,
            (utc_day(), VERSION),
        ))
    except Exception:
        return 0

    n = 0
    for row in rows:
        if _v24_2_is_infra_rejected(con, row):
            continue
        n += 1
    return n

def active_v24_intents(con: sqlite3.Connection) -> int:
    if not table_exists(con, INTENT_TABLE):
        return 0

    try:
        rows = list(con.execute(
            f"""
            SELECT id, ts, adapter_status, intent_state, version
            FROM {qid(INTENT_TABLE)}
            WHERE version=?
            ORDER BY id DESC
            LIMIT 200
            """,
            (VERSION,),
        ))
    except Exception:
        return 0

    n = 0
    for row in rows:
        if _v24_2_is_infra_rejected(con, row):
            continue

        age = _v24_2_age_min(_v24_2_row_get(row, "ts", 1))
        if age is None or age > 360:
            continue

        status = str(_v24_2_row_get(row, "adapter_status", 2) or "").upper()
        state = str(_v24_2_row_get(row, "intent_state", 3) or "").upper()

        if "REJECT" in status or "REJECT" in state:
            continue

        if (
            "PENDING" in status
            or "OPEN" in status
            or "ACCEPT" in status
            or "PENDING" in state
            or "APPROVED" in state
            or "BOUND" in state
        ):
            n += 1

    return n

def recent_key_attempts(con: sqlite3.Connection, key: str) -> int:
    if not table_exists(con, INTENT_TABLE):
        return 0

    cols = columns(con, INTENT_TABLE)
    if "key" not in cols or "ts" not in cols:
        return 0

    try:
        rows = list(con.execute(
            f"""
            SELECT id, ts, adapter_status, intent_state, version, key
            FROM {qid(INTENT_TABLE)}
            WHERE key=?
              AND version=?
            ORDER BY ts DESC
            LIMIT 50
            """,
            (key, VERSION),
        ))
    except Exception:
        return 0

    n = 0
    for row in rows:
        if _v24_2_is_infra_rejected(con, row):
            continue

        a = _v24_2_age_min(_v24_2_row_get(row, "ts", 1))
        if a is not None and a <= KEY_COOLDOWN_MIN:
            n += 1

    return n
# === V24.2_CANONICAL_ATTEMPT_ACCOUNTING_END ===


def size_model(candidate: Dict[str, Any], evidence: Dict[str, Any], quality: float) -> float:
    n = evidence["sample_n"] or 0
    mean = evidence["mean_r"]
    lcb = evidence["lcb95_r"]
    pf = evidence["profit_factor"]

    if n >= 30 and mean is not None and lcb is not None and lcb > 0 and pf and pf >= 1.15:
        raw = 0.008 + 0.004 * clamp(quality / 100.0, 0.0, 1.0)
    elif n >= 10 and mean is not None and mean > 0 and pf and pf >= 1.05:
        raw = 0.006
    else:
        raw = DISCOVERY_SIZE_MULT

    return round(clamp(raw, MIN_SIZE_MULT, MAX_SIZE_MULT), 6)


def quality_score(candidate: Dict[str, Any], evidence: Dict[str, Any], market: Dict[str, Any]) -> float:
    score = candidate["brain_score"] or 0.0
    n = evidence["sample_n"] or 0
    mean = evidence["mean_r"] or 0.0
    lcb = evidence["lcb95_r"]
    pf = evidence["profit_factor"] or 0.0

    q = 0.0
    q += 0.45 * score
    q += 15.0 if market.get("ok") else 0.0
    q += min(15.0, n / 2.0)
    q += 12.0 * clamp((mean + 0.05) / 0.20, 0.0, 1.0)
    q += 10.0 * clamp(((lcb if lcb is not None else -0.10) + 0.10) / 0.20, 0.0, 1.0)
    q += 8.0 * clamp((pf - 1.0) / 1.5, 0.0, 1.0)

    return round(clamp(q, 0.0, 100.0), 4)


def decide(con: sqlite3.Connection, candidate: Dict[str, Any]) -> Dict[str, Any]:
    evidence = evidence_for(con, candidate)
    market = latest_price(con, candidate["symbol"])
    reasons: List[str] = []

    brain_age = candidate["age_min"]
    auth = str(candidate.get("authority_state") or "").upper()
    bscore = candidate["brain_score"] or 0.0

    if brain_age is None or brain_age > BRAIN_TTL_MIN:
        reasons.append("BRAIN_STALE")
    if any(tok in auth for tok in BAD_AUTH_TOKENS):
        reasons.append("BRAIN_AUTHORITY_BLOCKED")
    if bscore < 52:
        reasons.append("BRAIN_SCORE_TOO_LOW")
    if not market.get("ok"):
        reasons.append("NO_FRESH_MARKET_PRICE")
    if open_positions_count(con) >= MAX_OPEN_GLOBAL:
        reasons.append("GLOBAL_OPEN_CAP")
    if open_positions_count(con, candidate["key"]) >= MAX_OPEN_PER_KEY:
        reasons.append("KEY_ALREADY_OPEN")
    if v24_attempts_today(con) >= MAX_DAILY_V24_ATTEMPTS:
        reasons.append("V24_DAILY_ATTEMPT_CAP")
    if active_v24_intents(con) >= MAX_ACTIVE_V24_INTENTS:
        reasons.append("V24_ACTIVE_INTENT_CAP")
    if recent_key_attempts(con, candidate["key"]) > 0:
        reasons.append("V24_KEY_COOLDOWN")

    n = evidence["sample_n"] or 0
    mean = evidence["mean_r"]
    pf = evidence["profit_factor"]
    cvar = evidence["cvar5_r"]

    if n >= 5 and mean is not None and mean < -0.02:
        reasons.append("LIVE_EXPECTANCY_NEGATIVE")
    if n >= 5 and pf is not None and pf < 0.75:
        reasons.append("LIVE_PF_TOO_LOW")
    if cvar is not None and cvar <= -1.5:
        reasons.append("TAIL_RISK_TOO_HIGH")

    q = quality_score(candidate, evidence, market)
    size = size_model(candidate, evidence, q)

    if reasons:
        action = "BLOCK"
        decision = "NO_EMIT"
        size = 0.0
    elif n >= 30 and evidence["lcb95_r"] is not None and evidence["lcb95_r"] > 0:
        action = "EMIT_PAPER_CANARY_VALIDATED_EDGE"
        decision = "APPROVE"
    else:
        action = "EMIT_PAPER_CANARY_DISCOVERY"
        decision = "APPROVE"

    payload = {
        "candidate": candidate,
        "evidence": evidence,
        "market": market,
        "risk": {
            "min_size_mult": MIN_SIZE_MULT,
            "max_size_mult": MAX_SIZE_MULT,
            "daily_attempt_cap": MAX_DAILY_V24_ATTEMPTS,
            "active_intent_cap": MAX_ACTIVE_V24_INTENTS,
            "key_cooldown_min": KEY_COOLDOWN_MIN,
            "paper_only": True,
            "real_execution_allowed": False,
        },
        "reasons": reasons,
    }

    return {
        "key": candidate["key"],
        "symbol": candidate["symbol"],
        "side": candidate["side"],
        "setup": candidate["setup"],
        "decision": decision,
        "action": action,
        "quality_score": q,
        "brain_score": bscore,
        "size_mult": size,
        "brain_age_min": brain_age,
        "price_age_min": market.get("age_min"),
        "evidence_state": evidence["evidence_state"],
        "reasons": reasons,
        "payload": payload,
    }


def create_queue_contract(con: sqlite3.Connection, d: Dict[str, Any]) -> Optional[int]:
    if not table_exists(con, QUEUE_TABLE):
        return None

    decision_hash = sha256_obj({
        "version": VERSION,
        "day": utc_day(),
        "key": d["key"],
        "action": d["action"],
    })

    contract = {
        "authority": VERSION,
        "contract_type": "V24_PAPER_MICRO_CANARY_CONTRACT",
        "allowed_mode": VALID_MODE,
        "execution_permission": VALID_PERMISSION,
        "manual_activation_required": 1,
        "paper_only": True,
        "real_execution_allowed": False,
        "symbol": d["symbol"],
        "side": d["side"],
        "setup": d["setup"],
        "requested_size_mult": d["size_mult"],
        "decision_hash": decision_hash,
        "payload": d["payload"],
    }

    row = {
        "ts": utc_now(),
        "version": VERSION,
        "decision_hash": decision_hash,
        "key": d["key"],
        "symbol": d["symbol"],
        "side": d["side"],
        "setup": d["setup"],
        "queue_state": "V24_APPROVED_PENDING_ADAPTER",
        "requested_mode": VALID_MODE,
        "requested_size_mult": d["size_mult"],
        "institutional_priority": d["quality_score"],
        "manual_activation_required": 1,
        "execution_permission": VALID_PERMISSION,
        "source_action": d["action"],
        "source_tier": "V24_QUANT_AUTHORITY",
        "contract_json": json.dumps(contract, sort_keys=True),
        "payload": json.dumps(contract, sort_keys=True),
    }

    qid_new = insert_dynamic(con, QUEUE_TABLE, row)
    if qid_new:
        return qid_new

    cols = columns(con, QUEUE_TABLE)
    if "decision_hash" in cols:
        r = con.execute(
            f"SELECT id FROM {qid(QUEUE_TABLE)} WHERE decision_hash=? ORDER BY id DESC LIMIT 1",
            (decision_hash,),
        ).fetchone()
        if r:
            return int(r["id"])

    return None


def emit_intent(con: sqlite3.Connection, d: Dict[str, Any], queue_id: Optional[int]) -> int:
    intent_hash = sha256_obj({
        "version": VERSION,
        "day": utc_day(),
        "key": d["key"],
        "queue_id": queue_id,
        "size": d["size_mult"],
    })

    contract_hash = sha256_obj(d["payload"])

    payload = {
        "authority": VERSION,
        "source": "V24_CANONICAL_QUANT_AUTHORITY",
        "paper_only": True,
        "real_execution_allowed": False,
        "queue_id": queue_id,
        "decision": d,
    }

    data = {
        "ts": utc_now(),
        "version": VERSION,
        "intent_hash": intent_hash,
        "queue_id": queue_id,
        "key": d["key"],
        "symbol": d["symbol"],
        "side": d["side"],
        "setup": d["setup"],
        "intent_state": VALID_INTENT_STATE,
        "requested_mode": VALID_MODE,
        "allowed_mode": VALID_MODE,
        "execution_permission": VALID_PERMISSION,
        "adapter_status": VALID_ADAPTER_STATUS,
        "requested_size_mult": d["size_mult"],
        "size_mult": d["size_mult"],
        "risk_usd": round(START_EQUITY * d["size_mult"] * 0.0045, 6),
        "contract_hash": contract_hash,
        "contract_json": json.dumps(payload, sort_keys=True),
        "payload": json.dumps(payload, sort_keys=True),
        "metadata": json.dumps(payload, sort_keys=True),
        "extra": json.dumps(payload, sort_keys=True),
    }

    intent_id = insert_dynamic(con, INTENT_TABLE, data)
    if intent_id:
        return intent_id

    cols = columns(con, INTENT_TABLE)
    if "intent_hash" in cols:
        r = con.execute(
            f"SELECT id FROM {qid(INTENT_TABLE)} WHERE intent_hash=? ORDER BY id DESC LIMIT 1",
            (intent_hash,),
        ).fetchone()
        if r:
            return int(r["id"])

    return 0


def record_decision(con: sqlite3.Connection, d: Dict[str, Any], emitted_id: Optional[int]) -> None:
    dh = sha256_obj({
        "version": VERSION,
        "ts_bucket": utc_now()[:16],
        "key": d["key"],
        "action": d["action"],
    })

    con.execute(
        f'''
        INSERT OR IGNORE INTO {qid(V24_DECISION_TABLE)}
        (ts, version, decision_hash, key, symbol, side, setup, decision, action,
         quality_score, brain_score, size_mult, brain_age_min, price_age_min,
         evidence_state, reasons, emitted_intent_id, payload)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''',
        (
            utc_now(), VERSION, dh, d["key"], d["symbol"], d["side"], d["setup"],
            d["decision"], d["action"], d["quality_score"], d["brain_score"],
            d["size_mult"], d["brain_age_min"], d["price_age_min"],
            d["evidence_state"], ",".join(d["reasons"]), emitted_id,
            json.dumps(d["payload"], sort_keys=True),
        ),
    )


def equity_snapshot(con: sqlite3.Connection) -> Dict[str, Any]:
    """V24.6B: V24.0 equity is delegated to V24.5 canonical equity."""
    from joanbot.institutional.kernel_contract_v24_6 import canonical_equity_snapshot
    return canonical_equity_snapshot(con)


def run_once() -> Dict[str, Any]:
    con = connect()
    create_v24_tables(con)
    quick = con.execute("PRAGMA quick_check").fetchone()[0]

    candidates = brain_candidates(con)
    decisions = []

    for c in candidates[:12]:
        decisions.append(decide(con, c))

    decisions.sort(
        key=lambda x: (
            1 if x["decision"] == "APPROVE" else 0,
            x["quality_score"],
            x["brain_score"],
        ),
        reverse=True,
    )

    emitted_id = None
    emitted_queue = None

    for d in decisions:
        if d["decision"] != "APPROVE":
            continue
        emitted_queue = create_queue_contract(con, d)
        emitted_id = emit_intent(con, d, emitted_queue)
        d["emitted_intent_id"] = emitted_id
        d["emitted_queue_id"] = emitted_queue
        break

    for d in decisions:
        record_decision(con, d, d.get("emitted_intent_id"))

    equity = equity_snapshot(con)

    report = {
        "version": VERSION,
        "utc": utc_now(),
        "quick_check": quick,
        "candidates": len(candidates),
        "decisions": len(decisions),
        "emitted_intent_id": emitted_id,
        "emitted_queue_id": emitted_queue,
        "equity": equity,
        "top_decisions": decisions[:12],
    }

    (OUT / "last_cycle.json").write_text(json.dumps(report, indent=2, sort_keys=True, default=str))

    lines = []
    lines.append("# V24.0 INSTITUTIONAL QUANT PRODUCTION AUTHORITY")
    lines.append(f"- UTC: {report['utc']}")
    lines.append(f"- DB quick_check: {quick}")
    lines.append(f"- Candidates: {len(candidates)}")
    lines.append(f"- Decisions: {len(decisions)}")
    lines.append(f"- Emitted intent: {emitted_id}")
    lines.append(f"- Emitted queue: {emitted_queue}")
    lines.append("")
    lines.append("## Equity")
    lines.append(f"- SIM BALANCE: {equity['balance']}$")
    lines.append(f"- PnL: {equity['pnl_usd']}$")
    lines.append(f"- Return: {equity['return_pct']}%")
    lines.append(f"- Closed trades: {equity['closed_trades']}")
    lines.append(f"- Open positions: {equity['open_positions']}")
    lines.append("")
    lines.append("## Top decisions")
    for d in decisions[:12]:
        lines.append(
            f"- {d['decision']} | {d['action']} | q={d['quality_score']} | "
            f"{d['symbol']} {d['side']} {d['setup']} | size={d['size_mult']} | "
            f"brain={d['brain_score']} age={d['brain_age_min']}m | "
            f"price_age={d['price_age_min']}m | evidence={d['evidence_state']} | "
            f"reasons={','.join(d['reasons']) if d['reasons'] else 'OK'}"
        )

    summary = "\n".join(lines) + "\n"
    (OUT / "summary.md").write_text(summary)
    print(summary, end="")

    con.close()
    return report


def status() -> int:
    p = OUT / "summary.md"
    if p.exists():
        print(p.read_text())
    else:
        print("NO_V24_SUMMARY_YET")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--daemon", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--interval", type=int, default=90)
    args = ap.parse_args()

    if args.status:
        return status()

    if args.daemon:
        while True:
            try:
                run_once()
            except Exception as e:
                print("V24_AUTHORITY_ERROR:", repr(e), flush=True)
            time.sleep(args.interval)
        return 0

    run_once()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
