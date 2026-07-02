#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sqlite3
import statistics
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path("/storage/emulated/0/Download/joan_crypto_ai_pro_v14")
DB = ROOT / "data/joanbot_v14.sqlite"
OUT = ROOT / "data/v23_4_quant_execution_authority"
STATE = OUT / "state.json"
SUMMARY = OUT / "summary.md"
LOCK = OUT / "lockdir"

VERSION = "V23.4_QUANT_EXECUTION_AUTHORITY_INSTITUTIONAL"

START_EQUITY = 100000.0

# Adapter-compatible risk envelope.
MIN_SIZE_MULT = 0.005411
MAX_SIZE_MULT = 0.008000
MAX_GLOBAL_OPEN = 2
MAX_PER_KEY_OPEN = 1
COOLDOWN_MIN = 90

# Quantitative policy.
MIN_SHADOW_N = 80
MIN_LIVE_N_FOR_SCALE = 20
MIN_PROB_EDGE = 0.52
MIN_EXPECTANCY_R = -0.03
MIN_LCB_R_SOFT = -0.55
MAX_CVAR_R = -1.35
MAX_DRAWDOWN_R = 3.5
MIN_STABILITY = 0.25
MIN_QUALITY_SCORE = 62.0

ALLOWED_SYMBOLS = {"BTCUSDT", "ETHUSDT"}
ALLOWED_SETUPS = {
    "UAL2_SQUEEZE_REVERSAL_LONG",
    "UAL2_SQUEEZE_REVERSAL_SHORT",
    "UAL2_BOUNCE_FADE_LONG",
    "UAL2_BOUNCE_FADE_SHORT",
}

FORBIDDEN_TOKENS = {
    "HARD_VETO",
    "FATAL",
    "TOXIC",
    "DB_NOT_OK",
    "MARKET_DATA_NOT_STABLE",
    "POSITION_LIMIT_BREACH",
    "DRAWDOWN_BREACH",
    "REAL_EXECUTION_ALLOWED",
    "AUTO_ESCALATION_ALLOWED",
}

GOOD_TOKENS = {
    "PROB_EDGE_OK",
    "ROBUST_MEAN_POSITIVE",
    "TRACEABILITY_OK",
    "SHADOW_POWER_AVAILABLE",
    "PF_MINIMAL_ACCEPTABLE_FOR_CANARY_REVIEW",
}

def utc() -> str:
    return datetime.now(timezone.utc).isoformat()

def acquire_lock() -> bool:
    OUT.mkdir(parents=True, exist_ok=True)
    try:
        LOCK.mkdir()
        (LOCK / "pid").write_text(str(os.getpid()))
        (LOCK / "ts").write_text(utc())
        return True
    except FileExistsError:
        try:
            ts = (LOCK / "ts").read_text()
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            if (datetime.now(timezone.utc) - dt).total_seconds() > 600:
                for p in LOCK.glob("*"):
                    p.unlink(missing_ok=True)
                LOCK.rmdir()
                LOCK.mkdir()
                (LOCK / "pid").write_text(str(os.getpid()))
                (LOCK / "ts").write_text(utc())
                return True
        except Exception:
            pass
    return False

def release_lock() -> None:
    try:
        for p in LOCK.glob("*"):
            p.unlink(missing_ok=True)
        LOCK.rmdir()
    except Exception:
        pass

def connect() -> sqlite3.Connection:
    con = sqlite3.connect(str(DB), timeout=30, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=30000")
    return con

def ensure_schema(con: sqlite3.Connection) -> None:
    con.execute("""
    CREATE TABLE IF NOT EXISTS institutional_quant_execution_authority_v23_4 (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT,
        version TEXT,
        action TEXT,
        verdict TEXT,
        symbol TEXT,
        side TEXT,
        setup TEXT,
        key TEXT,
        quality_score REAL,
        confidence_score REAL,
        evidence_score REAL,
        risk_score REAL,
        final_size_mult REAL,
        intent_table TEXT,
        intent_id INTEGER,
        reason TEXT,
        payload TEXT
    )
    """)
    con.execute("""
    CREATE TABLE IF NOT EXISTS institutional_quant_execution_authority_state_v23_4 (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT,
        version TEXT,
        state_json TEXT
    )
    """)

def tables(con: sqlite3.Connection) -> List[str]:
    return [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]

def cols(con: sqlite3.Connection, table: str) -> List[str]:
    return [r[1] for r in con.execute(f'PRAGMA table_info("{table}")')]

def table_info(con: sqlite3.Connection, table: str) -> List[sqlite3.Row]:
    return list(con.execute(f'PRAGMA table_info("{table}")'))

def parse_json_loose(x: Any, depth: int = 0) -> Any:
    if depth > 6:
        return x
    if isinstance(x, (dict, list)):
        return x
    if not isinstance(x, str):
        return x
    s = x.strip()
    if not s:
        return x
    try:
        y = json.loads(s)
        return parse_json_loose(y, depth + 1)
    except Exception:
        return x

def walk(obj: Any):
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from walk(v)

def first_value(obj: Any, names: List[str]) -> Any:
    ns = set(names)
    for d in walk(obj):
        if isinstance(d, dict):
            for k, v in d.items():
                if k in ns:
                    return v
    return None

def collect_values(obj: Any, names: List[str]) -> List[Any]:
    ns = set(names)
    out = []
    for d in walk(obj):
        if isinstance(d, dict):
            for k, v in d.items():
                if k in ns:
                    out.append(v)
    return out

def as_float(x: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if x is None:
            return default
        return float(x)
    except Exception:
        return default

def as_list(x: Any) -> List[str]:
    if x is None:
        return []
    if isinstance(x, list):
        return [str(i) for i in x]
    if isinstance(x, str):
        y = parse_json_loose(x)
        if isinstance(y, list):
            return [str(i) for i in y]
        return [x]
    return [str(x)]

def order_column(cs: List[str]) -> Optional[str]:
    for c in ["id", "ts", "created_at", "updated_at"]:
        if c in cs:
            return c
    return cs[0] if cs else None

def latest_runtime_ok() -> Tuple[bool, str]:
    p = ROOT / "data/v22_1_runtime_manager/runtime_health.json"
    if not p.exists():
        return False, "RUNTIME_HEALTH_MISSING"
    try:
        r = json.loads(p.read_text())
    except Exception as e:
        return False, f"RUNTIME_HEALTH_PARSE_ERROR:{e}"
    if r.get("db", {}).get("quick_check") != "ok":
        return False, "RUNTIME_DB_NOT_OK"
    if r.get("verdict") not in {"OK_RUNTIME_MANAGER_ACTIVE", "DEGRADED"}:
        return False, f"BAD_RUNTIME_VERDICT:{r.get('verdict')}"
    return True, "RUNTIME_OK"

def db_ok(con: sqlite3.Connection) -> Tuple[bool, str]:
    try:
        qc = con.execute("PRAGMA quick_check").fetchone()[0]
        if qc != "ok":
            return False, f"DB_NOT_OK:{qc}"
        return True, "DB_OK"
    except Exception as e:
        return False, f"DB_ERROR:{e}"

def extract_candidate(table: str, row: sqlite3.Row) -> Optional[Dict[str, Any]]:
    cs = row.keys()
    raw_parts = []
    parsed_parts = []

    for c in cs:
        v = row[c]
        if v is None:
            continue
        if isinstance(v, (int, float)):
            continue
        s = str(v)
        if len(s) < 3:
            continue
        if any(k in c.lower() for k in ["payload", "json", "contract", "reason", "flag", "state", "key", "setup", "symbol", "side"]) or "UAL2" in s or "CANARY" in s:
            raw_parts.append(s)
            parsed_parts.append(parse_json_loose(s))

    raw = "\n".join(raw_parts)
    hay = raw.upper()

    if not any(x in hay for x in ["UAL2_", "PAPER_MICRO_CANARY", "MANUAL_REVIEW", "CANARY"]):
        return None

    parsed = {"parts": parsed_parts}

    symbol = first_value(parsed, ["symbol"])
    side = first_value(parsed, ["side"])
    setup = first_value(parsed, ["setup", "setup_key"])
    key = first_value(parsed, ["key"])
    requested_size = first_value(parsed, ["requested_size_mult", "size_mult"])
    quant_score = first_value(parsed, ["quant_score", "q_score", "score"])
    brain_score = first_value(parsed, ["brain_score"])
    prob_edge = first_value(parsed, ["prob_edge_factor", "prob_edge"])
    lcb = first_value(parsed, ["institutional_lcb_r", "posterior_lcb_r", "bootstrap_lcb_r", "lcb_r"])
    cvar = first_value(parsed, ["cvar_5_r", "cvar_r"])
    profit_factor = first_value(parsed, ["profit_factor", "pf"])
    winrate = first_value(parsed, ["winrate"])
    robust_mean = first_value(parsed, ["robust_mean_r", "mean_r", "expectancy_r"])
    shadow_n = first_value(parsed, ["shadow_n"])
    live_n = first_value(parsed, ["live_n"])
    fold_stability = first_value(parsed, ["fold_stability_score", "fold_stability"])
    max_drawdown = first_value(parsed, ["max_drawdown_r", "drawdown_r"])

    if not symbol:
        m = re.search(r'"symbol"\s*:\s*"([A-Z]+USDT)"', raw)
        symbol = m.group(1) if m else None
    if not side:
        m = re.search(r'"side"\s*:\s*"(LONG|SHORT)"', raw)
        side = m.group(1) if m else None
    if not setup:
        m = re.search(r'"setup"\s*:\s*"([^"]+)"', raw)
        setup = m.group(1) if m else None

    symbol = str(symbol).upper() if symbol else None
    side = str(side).upper() if side else None
    setup = str(setup) if setup else None
    if not key and symbol and side and setup:
        key = f"{symbol}|{side}|{setup}"
    key = str(key) if key else None

    flags = []
    for name in ["green_flags", "red_flags", "yellow_flags", "reasons"]:
        for v in collect_values(parsed, [name]):
            flags.extend(as_list(v))

    tokens = sorted(set(str(x) for x in flags))

    rowid = row["id"] if "id" in row.keys() else None

    return {
        "source_table": table,
        "source_rowid": rowid,
        "symbol": symbol,
        "side": side,
        "setup": setup,
        "key": key,
        "requested_size_mult": as_float(requested_size),
        "quant_score_raw": as_float(quant_score),
        "brain_score": as_float(brain_score),
        "prob_edge": as_float(prob_edge),
        "lcb_r": as_float(lcb),
        "cvar_r": as_float(cvar),
        "profit_factor": as_float(profit_factor),
        "winrate": as_float(winrate),
        "expectancy_r": as_float(robust_mean),
        "shadow_n": as_float(shadow_n),
        "live_n": as_float(live_n),
        "fold_stability": as_float(fold_stability),
        "max_drawdown_r": as_float(max_drawdown),
        "tokens": tokens,
        "raw_excerpt": raw[:4000],
    }

def discover_candidates(con: sqlite3.Connection) -> List[Dict[str, Any]]:
    result = []
    for t in tables(con):
        tl = t.lower()
        if any(x in tl for x in ["health", "audit", "map", "market_data", "runtime"]):
            continue
        if not any(x in tl for x in ["promotion", "contract", "governor", "review", "quant", "canary"]):
            continue
        try:
            cs = cols(con, t)
            oc = order_column(cs)
            order = f'ORDER BY "{oc}" DESC' if oc else ""
            rows = con.execute(f'SELECT * FROM "{t}" {order} LIMIT 250').fetchall()
            for r in rows:
                c = extract_candidate(t, r)
                if c:
                    result.append(c)
        except Exception:
            continue
    return result

def bounded(x: Optional[float], lo: float, hi: float, default: float) -> float:
    if x is None or math.isnan(x):
        return default
    return max(lo, min(hi, x))

def score_candidate(c: Dict[str, Any]) -> Dict[str, Any]:
    reasons = []
    warnings = []

    symbol = c.get("symbol")
    side = c.get("side")
    setup = c.get("setup")
    tokens = set(c.get("tokens") or [])

    if symbol not in ALLOWED_SYMBOLS:
        reasons.append("BAD_SYMBOL")
    if side not in {"LONG", "SHORT"}:
        reasons.append("BAD_SIDE")
    if setup not in ALLOWED_SETUPS:
        reasons.append("SETUP_NOT_ALLOWED")

    for t in tokens:
        for bad in FORBIDDEN_TOKENS:
            if bad in t and "MUST_NOT" not in t:
                reasons.append(f"FORBIDDEN_TOKEN:{bad}")

    prob_edge = bounded(c.get("prob_edge"), 0.0, 2.0, 0.50)
    lcb_r = bounded(c.get("lcb_r"), -2.0, 2.0, -0.50)
    cvar_r = bounded(c.get("cvar_r"), -5.0, 2.0, -1.0)
    pf = bounded(c.get("profit_factor"), 0.0, 5.0, 1.0)
    winrate = bounded(c.get("winrate"), 0.0, 1.0, 0.50)
    expectancy = bounded(c.get("expectancy_r"), -2.0, 2.0, 0.0)
    shadow_n = bounded(c.get("shadow_n"), 0.0, 100000.0, 0.0)
    live_n = bounded(c.get("live_n"), 0.0, 100000.0, 0.0)
    stability = bounded(c.get("fold_stability"), 0.0, 1.0, 0.30)
    drawdown = bounded(c.get("max_drawdown_r"), 0.0, 20.0, 3.0)

    green_hits = sum(1 for g in GOOD_TOKENS if any(g in t for t in tokens))

    if shadow_n < MIN_SHADOW_N:
        warnings.append("LOW_SHADOW_N")
    if prob_edge < MIN_PROB_EDGE:
        warnings.append("LOW_PROB_EDGE")
    if expectancy < MIN_EXPECTANCY_R:
        warnings.append("LOW_EXPECTANCY")
    if lcb_r < MIN_LCB_R_SOFT:
        reasons.append("LCB_TOO_NEGATIVE")
    if cvar_r < MAX_CVAR_R:
        reasons.append("CVAR_TOO_NEGATIVE")
    if drawdown > MAX_DRAWDOWN_R:
        reasons.append("DRAWDOWN_TOO_HIGH")
    if stability < MIN_STABILITY:
        warnings.append("LOW_STABILITY")
    if green_hits < 2:
        warnings.append("LOW_GREEN_CONFIRMATION")

    evidence_score = 0.0
    evidence_score += min(25.0, math.log1p(shadow_n) * 4.0)
    evidence_score += min(20.0, math.log1p(live_n) * 5.0)

    edge_score = 0.0
    edge_score += (prob_edge - 0.50) * 100.0
    edge_score += max(-20.0, min(25.0, expectancy * 80.0))
    edge_score += max(-20.0, min(20.0, lcb_r * 35.0))
    edge_score += max(-10.0, min(20.0, (pf - 1.0) * 40.0))
    edge_score += max(-10.0, min(15.0, (winrate - 0.50) * 50.0))

    risk_score = 25.0
    risk_score += max(-20.0, min(0.0, cvar_r * 10.0))
    risk_score -= min(15.0, max(0.0, drawdown - 1.0) * 4.0)
    risk_score += stability * 10.0

    token_score = green_hits * 4.0

    quality = evidence_score + edge_score + risk_score + token_score

    live_penalty = 1.0
    if live_n < MIN_LIVE_N_FOR_SCALE:
        live_penalty = 0.62

    quality *= live_penalty
    quality = max(0.0, min(100.0, quality))

    # Conservative fractional Kelly proxy.
    # Not real Kelly, because distributions are incomplete.
    edge_proxy = max(0.0, expectancy + (prob_edge - 0.50) * 0.8)
    variance_proxy = max(0.80, abs(cvar_r) + 0.35)
    kelly_raw = edge_proxy / variance_proxy
    kelly_capped = max(0.0, min(0.010, kelly_raw * 0.25))

    size = max(MIN_SIZE_MULT, min(MAX_SIZE_MULT, kelly_capped if kelly_capped > 0 else MIN_SIZE_MULT))

    if quality < MIN_QUALITY_SCORE:
        reasons.append(f"QUALITY_TOO_LOW:{quality:.2f}<{MIN_QUALITY_SCORE}")

    decision = "APPROVE" if not reasons else "REJECT"

    return {
        **c,
        "decision": decision,
        "reject_reasons": reasons,
        "warnings": warnings,
        "quality_score": quality,
        "evidence_score": evidence_score,
        "edge_score": edge_score,
        "risk_score": risk_score,
        "token_score": token_score,
        "green_hits": green_hits,
        "live_penalty": live_penalty,
        "final_size_mult": size,
        "metrics_norm": {
            "prob_edge": prob_edge,
            "lcb_r": lcb_r,
            "cvar_r": cvar_r,
            "profit_factor": pf,
            "winrate": winrate,
            "expectancy_r": expectancy,
            "shadow_n": shadow_n,
            "live_n": live_n,
            "fold_stability": stability,
            "max_drawdown_r": drawdown,
            "kelly_raw": kelly_raw,
            "kelly_capped": kelly_capped,
        }
    }

def find_intent_table(con: sqlite3.Connection) -> Optional[str]:
    for t in [
        "institutional_quant_canary_execution_intents_v17_7_2",
        "institutional_quant_canary_execution_intents"
    ]:
        if t in tables(con):
            return t
    for t in tables(con):
        tl = t.lower()
        if "intent" in tl and "execution" in tl and "canary" in tl:
            return t
    return None

def template_row(con: sqlite3.Connection, table: str) -> Optional[sqlite3.Row]:
    cs = cols(con, table)
    if "adapter_status" in cs:
        for pat in ["%OPENED%", "%PENDING%", "%BIND%"]:
            try:
                r = con.execute(f'SELECT * FROM "{table}" WHERE adapter_status LIKE ? ORDER BY id ASC LIMIT 1', (pat,)).fetchone()
                if r:
                    return r
            except Exception:
                pass
    try:
        return con.execute(f'SELECT * FROM "{table}" ORDER BY id ASC LIMIT 1').fetchone()
    except Exception:
        return None

def open_exposure(con: sqlite3.Connection) -> Tuple[int, set]:
    total = 0
    keys = set()
    for t in tables(con):
        if "paper_micro_canary_positions" not in t.lower() and "paper_canary" not in t.lower():
            continue
        try:
            cs = cols(con, t)
            status_col = next((x for x in ["status", "state", "position_state", "trade_state"] if x in cs), None)
            if not status_col:
                continue
            rows = con.execute(f'''
                SELECT * FROM "{t}"
                WHERE lower(CAST("{status_col}" AS TEXT)) IN ('open','opened','active','running')
            ''').fetchall()
            for r in rows:
                total += 1
                sym = r["symbol"] if "symbol" in r.keys() else None
                side = r["side"] if "side" in r.keys() else None
                setup = r["setup"] if "setup" in r.keys() else (r["setup_key"] if "setup_key" in r.keys() else None)
                if sym and side and setup:
                    keys.add(f"{sym}|{side}|{setup}")
        except Exception:
            continue
    return total, keys

def recent_emission(con: sqlite3.Connection, key: str) -> bool:
    since = (datetime.now(timezone.utc) - timedelta(minutes=COOLDOWN_MIN)).isoformat()
    n = con.execute("""
        SELECT COUNT(*) FROM institutional_quant_execution_authority_v23_4
        WHERE key=? AND action='EMIT_INTENT' AND ts>=?
    """, (key, since)).fetchone()[0]
    return n > 0

def audit(con: sqlite3.Connection, action: str, verdict: str, item: Optional[Dict[str, Any]], reason: str, intent_table: Optional[str] = None, intent_id: Optional[int] = None) -> None:
    item = item or {}
    con.execute("""
    INSERT INTO institutional_quant_execution_authority_v23_4
    (ts, version, action, verdict, symbol, side, setup, key, quality_score,
     confidence_score, evidence_score, risk_score, final_size_mult,
     intent_table, intent_id, reason, payload)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        utc(), VERSION, action, verdict,
        item.get("symbol"), item.get("side"), item.get("setup"), item.get("key"),
        item.get("quality_score"),
        item.get("edge_score"),
        item.get("evidence_score"),
        item.get("risk_score"),
        item.get("final_size_mult"),
        intent_table, intent_id, reason,
        json.dumps(item, sort_keys=True, default=str)[:20000],
    ))

def emit_intent(con: sqlite3.Connection, table: str, item: Dict[str, Any]) -> int:
    info = table_info(con, table)
    cs = [x["name"] for x in info]
    pk = {x["name"] for x in info if x["pk"]}
    tmpl = template_row(con, table)

    base = {}
    if tmpl:
        for c in cs:
            base[c] = tmpl[c]

    ihash = hashlib.sha256(
        f"{VERSION}|{item['key']}|{item['source_table']}|{item.get('source_rowid')}|{utc()}".encode()
    ).hexdigest()[:40]

    payload = {
        "authority": VERSION,
        "paper_only": True,
        "real_execution_allowed": False,
        "source_candidate": item,
        "risk_policy": {
            "min_size_mult": MIN_SIZE_MULT,
            "max_size_mult": MAX_SIZE_MULT,
            "max_global_open": MAX_GLOBAL_OPEN,
            "max_per_key_open": MAX_PER_KEY_OPEN,
            "cooldown_min": COOLDOWN_MIN,
        }
    }

    overrides = {
        "ts": utc(),
        "created_at": utc(),
        "updated_at": utc(),
        "version": VERSION,
        "intent_hash": ihash,
        "key": item["key"],
        "symbol": item["symbol"],
        "side": item["side"],
        "setup": item["setup"],
        "setup_key": item["setup"],
        "requested_size_mult": item["final_size_mult"],
        "size_mult": item["final_size_mult"],
        "risk_usd": START_EQUITY * item["final_size_mult"],
        "requested_mode": "PAPER_MICRO_CANARY_ONLY",
        "allowed_mode": "PAPER_MICRO_CANARY_ONLY",
        "intent_state": "MAX_QUANT_MANUAL_APPROVED_PENDING_PAPER_ADAPTER",
        "execution_permission": "PAPER_ONLY_NO_REAL_EXECUTION",
        "adapter_status": "PENDING_ADAPTER_BINDING",
        "reason": "V23_4_QUANT_EXECUTION_AUTHORITY_APPROVED",
        "source": VERSION,
        "payload": json.dumps(payload, sort_keys=True, default=str),
        "contract_json": json.dumps(payload, sort_keys=True, default=str),
        "metadata": json.dumps(payload, sort_keys=True, default=str),
        "extra": json.dumps(payload, sort_keys=True, default=str),
    }

    row = {}
    for ci in info:
        c = ci["name"]
        if c in pk and c.lower() == "id":
            continue

        val = base.get(c)
        if c in overrides:
            val = overrides[c]

        if val is None and ci["notnull"]:
            typ = (ci["type"] or "").upper()
            if "INT" in typ:
                val = 0
            elif "REAL" in typ or "FLOA" in typ or "DOUB" in typ:
                val = 0.0
            else:
                val = f"{VERSION}_DEFAULT"

        row[c] = val

    cols2 = list(row.keys())
    q = f'INSERT INTO "{table}" ({",".join([f""" "{c}" """ for c in cols2])}) VALUES ({",".join(["?"] * len(cols2))})'
    con.execute(q, [row[c] for c in cols2])
    return con.execute("SELECT last_insert_rowid()").fetchone()[0]

def run_once() -> Dict[str, Any]:
    if not DB.exists():
        return {"action": "BLOCK", "reason": "DB_MISSING", "ts": utc()}

    if not acquire_lock():
        return {"action": "SKIP", "reason": "LOCKED", "ts": utc()}

    con = None
    try:
        con = connect()
        ensure_schema(con)

        ok, reason = db_ok(con)
        if not ok:
            audit(con, "BLOCK", "DB_NOT_OK", None, reason)
            return {"action": "BLOCK", "reason": reason, "ts": utc()}

        ok, reason = latest_runtime_ok()
        if not ok:
            audit(con, "BLOCK", "RUNTIME_NOT_OK", None, reason)
            return {"action": "BLOCK", "reason": reason, "ts": utc()}

        open_n, open_keys = open_exposure(con)
        if open_n >= MAX_GLOBAL_OPEN:
            audit(con, "SKIP", "GLOBAL_CAP", None, f"open_n={open_n}")
            return {"action": "SKIP", "reason": "GLOBAL_CAP", "open_n": open_n, "ts": utc()}

        intent_table = find_intent_table(con)
        if not intent_table:
            audit(con, "BLOCK", "NO_INTENT_TABLE", None, "adapter intent table missing")
            return {"action": "BLOCK", "reason": "NO_INTENT_TABLE", "ts": utc()}

        raw_candidates = discover_candidates(con)
        scored = [score_candidate(c) for c in raw_candidates]
        scored.sort(key=lambda x: x.get("quality_score", 0), reverse=True)

        approved = []
        rejected_top = []
        for x in scored:
            if x["decision"] == "APPROVE":
                approved.append(x)
            else:
                if len(rejected_top) < 8:
                    rejected_top.append({
                        "key": x.get("key"),
                        "quality_score": x.get("quality_score"),
                        "reasons": x.get("reject_reasons"),
                        "warnings": x.get("warnings"),
                    })

        chosen = None
        chosen_block = None
        for x in approved:
            if x["key"] in open_keys:
                chosen_block = "SAME_KEY_OPEN"
                continue
            if recent_emission(con, x["key"]):
                chosen_block = "COOLDOWN"
                continue
            chosen = x
            break

        if not chosen:
            reason = chosen_block or "NO_APPROVED_CANDIDATE"
            audit(con, "SKIP", reason, approved[0] if approved else None, f"raw={len(raw_candidates)} approved={len(approved)} rejected_top={rejected_top}")
            return {
                "action": "SKIP",
                "reason": reason,
                "raw_candidates": len(raw_candidates),
                "approved": len(approved),
                "top_rejected": rejected_top,
                "ts": utc(),
            }

        intent_id = emit_intent(con, intent_table, chosen)
        audit(con, "EMIT_INTENT", "OK", chosen, "AUTHORIZED_CONTROLLED_PAPER_CANARY", intent_table, intent_id)

        return {
            "action": "EMIT_INTENT",
            "intent_table": intent_table,
            "intent_id": intent_id,
            "symbol": chosen["symbol"],
            "side": chosen["side"],
            "setup": chosen["setup"],
            "key": chosen["key"],
            "quality_score": chosen["quality_score"],
            "size_mult": chosen["final_size_mult"],
            "raw_candidates": len(raw_candidates),
            "approved": len(approved),
            "ts": utc(),
        }

    finally:
        try:
            if con:
                con.close()
        except Exception:
            pass
        release_lock()

def persist(result: Dict[str, Any]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(result, indent=2, sort_keys=True, default=str))

    lines = [
        f"# {VERSION}",
        f"- UTC: `{utc()}`",
        f"- Action: `{result.get('action')}`",
        f"- Reason: `{result.get('reason')}`",
        f"- Intent table: `{result.get('intent_table')}`",
        f"- Intent id: `{result.get('intent_id')}`",
        f"- Symbol: `{result.get('symbol')}`",
        f"- Side: `{result.get('side')}`",
        f"- Setup: `{result.get('setup')}`",
        f"- Key: `{result.get('key')}`",
        f"- Quality score: `{result.get('quality_score')}`",
        f"- Size mult: `{result.get('size_mult')}`",
        f"- Raw candidates: `{result.get('raw_candidates')}`",
        f"- Approved: `{result.get('approved')}`",
        "",
        "## Top rejected",
    ]
    for x in result.get("top_rejected", []) or []:
        lines.append(f"- `{x}`")

    SUMMARY.write_text("\n".join(lines))

    try:
        con = connect()
        ensure_schema(con)
        con.execute("""
        INSERT INTO institutional_quant_execution_authority_state_v23_4
        (ts, version, state_json)
        VALUES (?, ?, ?)
        """, (utc(), VERSION, json.dumps(result, sort_keys=True, default=str)))
        con.close()
    except Exception:
        pass

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    args = ap.parse_args()

    result = run_once()
    persist(result)
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
