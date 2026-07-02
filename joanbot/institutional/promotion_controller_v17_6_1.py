#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path.cwd()
DB = ROOT / "data" / "joanbot_v14.sqlite"
OUT = ROOT / "data" / "v17_6_1"

VERSION = "V17.6.1_INSTITUTIONAL_EVIDENCE_PROMOTION_CONTROLLER_PRO"
BRAIN_TABLE = "institutional_quant_brain_v17_5_1"

MAX_REVIEW_CONTRACTS_PER_CYCLE = 2
MAX_WATCHLIST_PER_CYCLE = 6
MAX_OPEN_CANARIES_GLOBAL = 2
MAX_OPEN_CANARIES_PER_KEY = 1
DAILY_REVIEW_CONTRACT_CAP = 4

HARD_BLOCK_FLAGS = {
    "HARD_VETO_PRESENT",
    "CVaR_TOO_NEGATIVE",
    "FAT_TAIL_WORST_R",
    "DRAWDOWN_R_TOO_HIGH",
    "NO_CLEAN_EVIDENCE",
}

STRUCTURAL_NEGATIVE_FLAGS = {
    "LCB_STRUCTURALLY_NEGATIVE",
    "INSTITUTIONAL_LCB_NOT_POSITIVE",
    "ROBUST_MEAN_NOT_POSITIVE",
    "PF_WEAK",
    "PROB_EDGE_WEAK",
    "FOLD_STABILITY_WEAK",
    "LOW_LIVE_EVIDENCE",
}

TOXIC_ACTIONS = {
    "QUARANTINE_HARD_BLOCK",
    "QUARANTINE_TAIL_RISK",
    "QUARANTINE_NEGATIVE_EDGE",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def utc_day() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def qid(x: str) -> str:
    return '"' + str(x).replace('"', '""') + '"'


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


def clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
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


def connect_ro() -> sqlite3.Connection:
    con = sqlite3.connect("file:" + str(DB.resolve()) + "?mode=ro", uri=True, timeout=10)
    con.row_factory = sqlite3.Row
    return con


def connect_rw() -> sqlite3.Connection:
    con = sqlite3.connect(str(DB), timeout=20)
    con.row_factory = sqlite3.Row
    return con


def table_exists(con: sqlite3.Connection, table: str) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,)
    ).fetchone() is not None


def columns(con: sqlite3.Connection, table: str) -> List[str]:
    if not table_exists(con, table):
        return []
    return [r[1] for r in con.execute(f"PRAGMA table_info({qid(table)})").fetchall()]


def latest_brain_rows(con: sqlite3.Connection) -> List[Dict[str, Any]]:
    if not table_exists(con, BRAIN_TABLE):
        raise RuntimeError(f"Missing required table: {BRAIN_TABLE}")

    ts = con.execute(f"SELECT MAX(ts) FROM {qid(BRAIN_TABLE)}").fetchone()[0]
    if not ts:
        return []

    rows = con.execute(
        f"SELECT * FROM {qid(BRAIN_TABLE)} WHERE ts=?",
        (ts,)
    ).fetchall()

    return [dict(r) for r in rows]


def latest_brain_ts(con: sqlite3.Connection) -> Optional[str]:
    if not table_exists(con, BRAIN_TABLE):
        return None
    return con.execute(f"SELECT MAX(ts) FROM {qid(BRAIN_TABLE)}").fetchone()[0]


def open_canaries_global(con: sqlite3.Connection) -> int:
    table = "paper_micro_canary_positions_v11"
    if not table_exists(con, table):
        return 0

    try:
        return int(con.execute("""
            SELECT COUNT(*)
            FROM paper_micro_canary_positions_v11
            WHERE UPPER(COALESCE(status,'')) IN ('OPEN','ACTIVE','RUNNING')
               OR (closed_at IS NULL AND opened_at IS NOT NULL)
        """).fetchone()[0])
    except Exception:
        return 0


def open_canaries_for_key(con: sqlite3.Connection, symbol: str, side: str, setup: str) -> int:
    table = "paper_micro_canary_positions_v11"
    if not table_exists(con, table):
        return 0

    try:
        return int(con.execute("""
            SELECT COUNT(*)
            FROM paper_micro_canary_positions_v11
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


def key_activity(con: sqlite3.Connection, symbol: str, side: str, setup: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {}

    tables = {
        "outcome": ("outcome_provenance_v1", ["pnl_r", "pnl_usd"]),
        "canary": ("paper_micro_canary_positions_v11", ["net_pnl_r", "pnl_r", "gross_pnl_r"]),
        "positions": ("positions", ["net_pnl_r", "pnl_r", "pnl_usd"]),
        "trades": ("trades", ["net_pnl_r", "pnl_r", "pnl_usd"]),
    }

    all_r: List[float] = []

    for name, (table, rcols) in tables.items():
        if not table_exists(con, table):
            out[f"{name}_n"] = 0
            continue

        cols = columns(con, table)
        usable_r = [c for c in rcols if c in cols]
        if not usable_r:
            out[f"{name}_n"] = 0
            continue

        try:
            rows = con.execute(
                f"""
                SELECT *
                FROM {qid(table)}
                WHERE UPPER(COALESCE(symbol,''))=?
                  AND UPPER(COALESCE(side,''))=?
                  AND COALESCE(setup,'')=?
                """,
                (symbol.upper(), side.upper(), setup)
            ).fetchall()
        except Exception:
            out[f"{name}_n"] = 0
            continue

        vals = []
        for r in rows:
            d = dict(r)
            v = None
            for c in usable_r:
                v = fnum(d.get(c), None)
                if v is not None:
                    break
            if v is not None:
                vals.append(v)
                all_r.append(v)

        out[f"{name}_n"] = len(vals)
        out[f"{name}_mean_r"] = sum(vals) / len(vals) if vals else None

    wins = [x for x in all_r if x > 0]
    losses = [x for x in all_r if x < 0]
    gp = sum(wins)
    gl = abs(sum(losses))

    out["realized_n"] = len(all_r)
    out["realized_mean_r"] = sum(all_r) / len(all_r) if all_r else None
    out["realized_pf"] = gp / gl if gl > 0 else None
    out["realized_worst_r"] = min(all_r) if all_r else None
    out["realized_best_r"] = max(all_r) if all_r else None

    return out


def daily_contract_count(con: sqlite3.Connection) -> int:
    table = "institutional_micro_canary_contract_queue_v17_6_1"
    if not table_exists(con, table):
        return 0

    day = utc_day()
    try:
        return int(con.execute(
            f"""
            SELECT COUNT(*)
            FROM {qid(table)}
            WHERE substr(ts, 1, 10)=?
              AND queue_state IN ('MANUAL_REVIEW_REQUIRED','PENDING_CANARY_REVIEW')
            """,
            (day,)
        ).fetchone()[0])
    except Exception:
        return 0




# STRUCTURAL_CONTRACT_QUEUE_REFRESH_V2: allow refresh of same-day/key/action/tier contracts without consuming new daily slots.
def same_day_review_contract_exists(con: sqlite3.Connection, key: str, action: str, tier: str) -> bool:
    table = "institutional_micro_canary_contract_queue_v17_6_1"
    if not table_exists(con, table):
        return False
    day = utc_day()
    h = decision_hash(day, key, action, tier)
    try:
        row = con.execute(
            f"""
            SELECT 1
            FROM {qid(table)}
            WHERE decision_hash=?
              AND substr(ts, 1, 10)=?
              AND queue_state IN ('MANUAL_REVIEW_REQUIRED','PENDING_CANARY_REVIEW','QUANT_REJECTED')
            LIMIT 1
            """,
            (h, day)
        ).fetchone()
        return row is not None
    except Exception:
        return False

def parse_flags(row: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, List[str]]:
    red = safe_json(row.get("red_flags"), [])
    yellow = safe_json(row.get("yellow_flags"), [])
    green = safe_json(row.get("green_flags"), [])

    if not isinstance(red, list):
        red = payload.get("red_flags") or []
    if not isinstance(yellow, list):
        yellow = payload.get("yellow_flags") or []
    if not isinstance(green, list):
        green = payload.get("green_flags") or []

    return {
        "red": [str(x) for x in red],
        "yellow": [str(x) for x in yellow],
        "green": [str(x) for x in green],
    }


def metric(row: Dict[str, Any], payload: Dict[str, Any], name: str, default=None):
    if name in row and row.get(name) is not None:
        return row.get(name)
    return payload.get(name, default)


def decision_hash(day: str, key: str, action: str, tier: str) -> str:
    raw = f"{VERSION}|{day}|{key}|{action}|{tier}"
    return hashlib.sha256(raw.encode()).hexdigest()


def build_contract_payload(decision: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "contract_type": "PAPER_MICRO_CANARY_REVIEW_CONTRACT",
        "execution_permission": "MANUAL_REVIEW_REQUIRED",
        "allowed_mode": "PAPER_MICRO_CANARY_ONLY",
        "symbol": decision["symbol"],
        "side": decision["side"],
        "setup": decision["setup"],
        "requested_size_mult": decision["requested_size_mult"],
        "max_parallel_for_key": MAX_OPEN_CANARIES_PER_KEY,
        "global_open_canary_cap": MAX_OPEN_CANARIES_GLOBAL,
        "mandatory_pre_trade_checks": [
            "latest_quant_brain_state_must_not_be_BLOCKED_BY_HARD_VETO",
            "db_quick_check_must_be_ok",
            "no_open_canary_same_key",
            "global_open_canaries_below_cap",
            "market_data_not_stale",
            "final_gate_must_not_emit_hard_veto",
            "position_must_write_outcome_provenance_v1_after_close",
            "position_must_write_paper_micro_canary_positions_v11_with_net_pnl_r",
        ],
        "promotion_success_criteria": {
            "min_live_n": 20,
            "min_profit_factor": 1.10,
            "institutional_lcb_r_must_improve": True,
            "max_drawdown_r_after_contract": 3.0,
            "no_fat_tail_loss_below_r": -1.5,
        },
        "auto_escalation_allowed": False,
        "manual_confirmation_required": True,
    }


def classify(row: Dict[str, Any], con_ro: sqlite3.Connection, open_global: int, daily_count: int) -> Dict[str, Any]:
    payload = safe_json(row.get("payload"), {})
    if not isinstance(payload, dict):
        payload = {}

    flags = parse_flags(row, payload)

    key = str(row.get("key") or payload.get("key") or "")
    symbol = str(row.get("symbol") or payload.get("symbol") or "").upper()
    side = str(row.get("side") or payload.get("side") or "").upper()
    setup = str(row.get("setup") or payload.get("setup") or "")

    brain_state = str(row.get("authority_state") or payload.get("authority_state") or "")
    score = fnum(metric(row, payload, "brain_score"), 0.0) or 0.0

    clean_n = inum(metric(row, payload, "clean_n"), 0)
    live_n = inum(metric(row, payload, "live_n"), 0)
    shadow_n = inum(metric(row, payload, "shadow_n"), 0)

    robust_mean = fnum(metric(row, payload, "robust_mean_r"), None)
    inst_lcb = fnum(metric(row, payload, "institutional_lcb_r"), None)
    prob = fnum(metric(row, payload, "prob_edge_gt_zero"), None)
    pf = fnum(metric(row, payload, "profit_factor"), None)
    cvar = fnum(metric(row, payload, "cvar_5_r"), None)
    dd = fnum(metric(row, payload, "max_drawdown_r"), None)
    trace = fnum(metric(row, payload, "traceability_score"), 0.0) or 0.0
    fold = fnum(metric(row, payload, "fold_stability_score"), None)

    red_set = set(flags["red"])
    yellow_set = set(flags["yellow"])

    hard_hits = sorted(list(red_set & HARD_BLOCK_FLAGS))
    structural_hits = sorted(list((red_set | yellow_set) & STRUCTURAL_NEGATIVE_FLAGS))

    activity = key_activity(con_ro, symbol, side, setup)
    open_key = open_canaries_for_key(con_ro, symbol, side, setup)

    # STRUCTURAL_CONTRACT_QUEUE_REFRESH_V2:
    # Daily cap blocks only NEW review slots. Refreshing the same contract key/day
    # is allowed so governance does not consume expired/stale contracts.
    review_action_name = "REVIEW_MICRO_CANARY_CONTRACT"
    review_tier_name = "POSITIVE_EDGE_NEEDS_LIVE_EVIDENCE"
    refresh_existing_review_slot = same_day_review_contract_exists(
        con_ro, key, review_action_name, review_tier_name
    )
    daily_review_slot_available = (
        daily_count < DAILY_REVIEW_CONTRACT_CAP or refresh_existing_review_slot
    )

    reasons: List[str] = []
    action = "RESEARCH_ONLY"
    tier = "NO_ACTION"
    queue_state = "NONE"

    mean_positive = robust_mean is not None and robust_mean > 0
    lcb_positive = inst_lcb is not None and inst_lcb > 0
    lcb_not_disastrous = inst_lcb is not None and inst_lcb > -0.55
    pf_minimal = pf is not None and pf >= 0.50
    pf_canary_ok = pf is not None and pf >= 1.05
    prob_minimal = prob is not None and prob >= 0.50
    trace_ok = trace >= 0.75
    shadow_power = shadow_n >= 150
    clean_power = clean_n >= 150
    live_seed = live_n >= 3

    tail_block = False
    if cvar is not None and cvar <= -1.50:
        tail_block = True
    if dd is not None and dd >= 5.0:
        tail_block = True

    negative_edge_block = False
    if robust_mean is not None and robust_mean < -0.025:
        negative_edge_block = True
    if pf is not None and pf < 0.35:
        negative_edge_block = True

    if hard_hits:
        action = "QUARANTINE_HARD_BLOCK"
        tier = "HARD_VETO_OR_FATAL_FLAG"
        queue_state = "NONE"
        reasons.extend(hard_hits)

    elif tail_block:
        action = "QUARANTINE_TAIL_RISK"
        tier = "TAIL_OR_DRAWDOWN_TOO_HIGH"
        queue_state = "NONE"
        reasons.append("TAIL_OR_DRAWDOWN_BLOCK")

    elif negative_edge_block:
        action = "QUARANTINE_NEGATIVE_EDGE"
        tier = "NEGATIVE_EXPECTANCY_OR_BROKEN_PF"
        queue_state = "NONE"
        reasons.append("NEGATIVE_EDGE_BLOCK")

    elif (
        mean_positive
        and lcb_not_disastrous
        and pf_minimal
        and prob_minimal
        and trace_ok
        and clean_power
        and shadow_power
        and open_global < MAX_OPEN_CANARIES_GLOBAL
        and open_key < MAX_OPEN_CANARIES_PER_KEY
        and daily_review_slot_available
    ):
        action = "REVIEW_MICRO_CANARY_CONTRACT"
        tier = "POSITIVE_EDGE_NEEDS_LIVE_EVIDENCE"
        queue_state = "MANUAL_REVIEW_REQUIRED"
        reasons.extend([
            "ROBUST_MEAN_POSITIVE",
            "LCB_NEGATIVE_BUT_NOT_DISASTROUS",
            "PF_MINIMAL_ACCEPTABLE_FOR_CANARY_REVIEW",
            "TRACEABILITY_OK",
            "SHADOW_POWER_AVAILABLE",
            "NEEDS_LIVE_EVIDENCE",
        ])

    elif mean_positive and clean_n >= 80 and shadow_n >= 80 and trace_ok:
        action = "EVIDENCE_PUSH_WATCHLIST"
        tier = "PROMISING_BUT_NOT_READY_FOR_CANARY"
        queue_state = "WATCHLIST_ONLY"
        reasons.extend([
            "ROBUST_MEAN_POSITIVE",
            "NOT_ENOUGH_FOR_CANARY_REVIEW",
            "KEEP_COLLECTING_EVIDENCE",
        ])

    elif mean_positive and clean_n >= 20:
        action = "RESEARCH_POSITIVE_WEAK"
        tier = "LOW_POWER_POSITIVE"
        queue_state = "NONE"
        reasons.append("POSITIVE_BUT_LOW_POWER")

    else:
        action = "RESEARCH_ONLY"
        tier = "NO_PROMOTION_EDGE"
        queue_state = "NONE"
        reasons.append("NO_ACTIONABLE_PROMOTION_CASE")

    if open_global >= MAX_OPEN_CANARIES_GLOBAL and action == "REVIEW_MICRO_CANARY_CONTRACT":
        action = "EVIDENCE_PUSH_WATCHLIST"
        tier = "GLOBAL_CANARY_BUDGET_FULL"
        queue_state = "WATCHLIST_ONLY"
        reasons.append("GLOBAL_CANARY_BUDGET_FULL")

    if open_key >= MAX_OPEN_CANARIES_PER_KEY and action == "REVIEW_MICRO_CANARY_CONTRACT":
        action = "EVIDENCE_PUSH_WATCHLIST"
        tier = "KEY_CANARY_ALREADY_OPEN"
        queue_state = "WATCHLIST_ONLY"
        reasons.append("KEY_CANARY_ALREADY_OPEN")

    priority = 0.0
    priority += 24.0 * clamp(((robust_mean or 0.0) + 0.02) / 0.12)
    priority += 18.0 * clamp(((inst_lcb or -0.50) + 0.55) / 0.60)
    priority += 16.0 * clamp(((prob or 0.50) - 0.45) / 0.35)
    priority += 14.0 * clamp(((pf or 0.50) - 0.35) / 0.90)
    priority += 10.0 * clamp(score / 100.0)
    priority += 8.0 * clamp(trace)
    priority += 6.0 * clamp(live_n / 20.0)
    priority += 4.0 * clamp((fold or 0.0))

    if action in TOXIC_ACTIONS:
        priority *= 0.10

    priority = round(clamp(priority, 0.0, 100.0), 4)

    requested_size_mult = 0.0
    if action == "REVIEW_MICRO_CANARY_CONTRACT":
        if live_n == 0:
            requested_size_mult = 0.010
        elif live_n < 5:
            requested_size_mult = 0.015
        else:
            requested_size_mult = 0.020
        requested_size_mult *= clamp(priority / 65.0, 0.50, 1.00)
        requested_size_mult = round(clamp(requested_size_mult, 0.005, 0.025), 6)

    decision = {
        "key": key,
        "symbol": symbol,
        "side": side,
        "setup": setup,
        "brain_state": brain_state,
        "brain_score": score,
        "action": action,
        "tier": tier,
        "queue_state": queue_state,
        "manual_activation_required": 1,
        "execution_permission": "NO_AUTO_EXECUTION",
        "requested_mode": "PAPER_MICRO_CANARY_ONLY" if action == "REVIEW_MICRO_CANARY_CONTRACT" else "NONE",
        "requested_size_mult": requested_size_mult,
        "institutional_priority": priority,
        "clean_n": clean_n,
        "live_n": live_n,
        "shadow_n": shadow_n,
        "robust_mean_r": robust_mean,
        "institutional_lcb_r": inst_lcb,
        "prob_edge_gt_zero": prob,
        "profit_factor": pf,
        "cvar_5_r": cvar,
        "max_drawdown_r": dd,
        "traceability_score": trace,
        "fold_stability_score": fold,
        "red_flags": flags["red"],
        "yellow_flags": flags["yellow"],
        "green_flags": flags["green"],
        "hard_hits": hard_hits,
        "structural_hits": structural_hits,
        "key_activity": activity,
        "open_canaries_global": open_global,
        "open_canaries_key": open_key,
        "daily_review_contracts_existing": daily_count,
        "reasons": reasons,
    }

    decision["contract"] = build_contract_payload(decision) if action == "REVIEW_MICRO_CANARY_CONTRACT" else None

    return decision


def create_tables(con: sqlite3.Connection) -> None:
    con.execute("""
        CREATE TABLE IF NOT EXISTS institutional_promotion_controller_v17_6_1 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            version TEXT NOT NULL,
            brain_ts TEXT,
            key TEXT NOT NULL,
            symbol TEXT,
            side TEXT,
            setup TEXT,
            brain_state TEXT,
            brain_score REAL,
            action TEXT,
            tier TEXT,
            queue_state TEXT,
            institutional_priority REAL,
            requested_size_mult REAL,
            clean_n INTEGER,
            live_n INTEGER,
            shadow_n INTEGER,
            robust_mean_r REAL,
            institutional_lcb_r REAL,
            prob_edge_gt_zero REAL,
            profit_factor REAL,
            cvar_5_r REAL,
            max_drawdown_r REAL,
            traceability_score REAL,
            reasons TEXT,
            payload TEXT
        )
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS institutional_micro_canary_contract_queue_v17_6_1 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            version TEXT NOT NULL,
            decision_hash TEXT NOT NULL UNIQUE,
            key TEXT NOT NULL,
            symbol TEXT,
            side TEXT,
            setup TEXT,
            queue_state TEXT NOT NULL,
            requested_mode TEXT NOT NULL,
            requested_size_mult REAL,
            institutional_priority REAL,
            manual_activation_required INTEGER NOT NULL,
            execution_permission TEXT NOT NULL,
            source_action TEXT,
            source_tier TEXT,
            contract_json TEXT,
            payload TEXT
        )
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS institutional_promotion_health_v17_6_1 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            version TEXT NOT NULL,
            brain_ts TEXT,
            candidates INTEGER,
            action_counts TEXT,
            queue_counts TEXT,
            open_canaries_global INTEGER,
            daily_review_contracts_existing INTEGER,
            payload TEXT
        )
    """)

    con.execute("CREATE INDEX IF NOT EXISTS idx_promo_v17_6_1_ts_action ON institutional_promotion_controller_v17_6_1(ts, action)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_promo_v17_6_1_key_ts ON institutional_promotion_controller_v17_6_1(key, ts)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_queue_v17_6_1_state_ts ON institutional_micro_canary_contract_queue_v17_6_1(queue_state, ts)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_queue_v17_6_1_key_ts ON institutional_micro_canary_contract_queue_v17_6_1(key, ts)")


def build_report() -> Dict[str, Any]:
    con = connect_ro()
    quick = con.execute("PRAGMA quick_check").fetchone()[0]
    brain_ts = latest_brain_ts(con)
    rows = latest_brain_rows(con)

    open_global = open_canaries_global(con)
    daily_count = daily_contract_count(con)

    decisions = [
        classify(row, con, open_global=open_global, daily_count=daily_count)
        for row in rows
    ]

    decisions.sort(
        key=lambda d: (
            1 if d["action"] == "REVIEW_MICRO_CANARY_CONTRACT" else 0,
            1 if d["action"] == "EVIDENCE_PUSH_WATCHLIST" else 0,
            d["institutional_priority"],
            d["brain_score"],
        ),
        reverse=True,
    )

    review_seen = 0
    watch_seen = 0
    final = []

    for d in decisions:
        d = dict(d)

        if d["action"] == "REVIEW_MICRO_CANARY_CONTRACT":
            review_seen += 1
            if review_seen > MAX_REVIEW_CONTRACTS_PER_CYCLE:
                d["action"] = "EVIDENCE_PUSH_WATCHLIST"
                d["tier"] = "REVIEW_CONTRACT_BUDGET_EXCEEDED"
                d["queue_state"] = "WATCHLIST_ONLY"
                d["requested_size_mult"] = 0.0
                d["contract"] = None
                d["reasons"] = list(d["reasons"]) + ["REVIEW_CONTRACT_BUDGET_EXCEEDED"]

        if d["action"] == "EVIDENCE_PUSH_WATCHLIST":
            watch_seen += 1
            if watch_seen > MAX_WATCHLIST_PER_CYCLE:
                d["action"] = "RESEARCH_ONLY"
                d["tier"] = "WATCHLIST_BUDGET_EXCEEDED"
                d["queue_state"] = "NONE"
                d["reasons"] = list(d["reasons"]) + ["WATCHLIST_BUDGET_EXCEEDED"]

        final.append(d)

    action_counts = defaultdict(int)
    queue_counts = defaultdict(int)

    for d in final:
        action_counts[d["action"]] += 1
        queue_counts[d["queue_state"]] += 1

    con.close()

    return {
        "version": VERSION,
        "generated_utc": utc_now(),
        "quick_check": quick,
        "brain_ts": brain_ts,
        "candidate_count": len(final),
        "open_canaries_global": open_global,
        "daily_review_contracts_existing": daily_count,
        "action_counts": dict(action_counts),
        "queue_counts": dict(queue_counts),
        "decisions": final,
    }


def write_outputs(report: Dict[str, Any], write_db: bool, emit_review_queue: bool) -> None:
    OUT.mkdir(parents=True, exist_ok=True)

    (OUT / "promotion_controller_latest.json").write_text(
        json.dumps(report, indent=2, sort_keys=True)
    )

    lines = []
    lines.append("# V17.6.1 Institutional Evidence Promotion Controller PRO")
    lines.append("")
    lines.append(f"- UTC: `{report['generated_utc']}`")
    lines.append(f"- DB quick_check: `{report['quick_check']}`")
    lines.append(f"- Brain latest ts: `{report['brain_ts']}`")
    lines.append(f"- Candidates: `{report['candidate_count']}`")
    lines.append(f"- Action counts: `{report['action_counts']}`")
    lines.append(f"- Queue counts: `{report['queue_counts']}`")
    lines.append(f"- Open canaries global: `{report['open_canaries_global']}`")
    lines.append(f"- Daily review contracts existing: `{report['daily_review_contracts_existing']}`")
    lines.append(f"- Emit review queue: `{emit_review_queue}`")
    lines.append("")

    lines.append("## Decisions")
    lines.append("| rank | action | tier | queue | priority | symbol | side | setup | brain | score | clean | live | shadow | mean | LCB | PF | DD | reasons |")
    lines.append("|---:|---|---|---|---:|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|")

    for i, d in enumerate(report["decisions"][:100], 1):
        lines.append(
            "| {i} | {action} | {tier} | {queue} | {prio:.2f} | {symbol} | {side} | {setup} | {brain} | {score:.2f} | {clean} | {live} | {shadow} | {mean} | {lcb} | {pf} | {dd} | {reasons} |".format(
                i=i,
                action=d["action"],
                tier=d["tier"],
                queue=d["queue_state"],
                prio=d["institutional_priority"],
                symbol=d["symbol"] or "-",
                side=d["side"] or "-",
                setup=(d["setup"] or "-")[:32],
                brain=d["brain_state"],
                score=d["brain_score"],
                clean=d["clean_n"],
                live=d["live_n"],
                shadow=d["shadow_n"],
                mean=fmt(d["robust_mean_r"]),
                lcb=fmt(d["institutional_lcb_r"]),
                pf=fmt(d["profit_factor"]),
                dd=fmt(d["max_drawdown_r"]),
                reasons=",".join(d["reasons"])[:160],
            )
        )

    lines.append("")
    lines.append("## Review contracts")
    contracts = [d for d in report["decisions"] if d["action"] == "REVIEW_MICRO_CANARY_CONTRACT"]

    if not contracts:
        lines.append("- none")
    else:
        for d in contracts:
            lines.append(
                f"- `{d['symbol']} {d['side']} {d['setup']}` "
                f"priority={d['institutional_priority']} size={d['requested_size_mult']} "
                f"manual=1 reasons={d['reasons']}"
            )

    lines.append("")
    lines.append("## Watchlist")
    watch = [d for d in report["decisions"] if d["action"] == "EVIDENCE_PUSH_WATCHLIST"]

    if not watch:
        lines.append("- none")
    else:
        for d in watch[:20]:
            lines.append(
                f"- `{d['symbol']} {d['side']} {d['setup']}` "
                f"priority={d['institutional_priority']} reasons={d['reasons']}"
            )

    (OUT / "promotion_controller_summary.md").write_text("\n".join(lines))

    with (OUT / "promotion_controller_ledger.jsonl").open("a") as f:
        f.write(json.dumps({
            "ts": report["generated_utc"],
            "brain_ts": report["brain_ts"],
            "action_counts": report["action_counts"],
            "queue_counts": report["queue_counts"],
            "top": report["decisions"][:12],
        }, sort_keys=True) + "\n")

    if not write_db:
        return

    con = connect_rw()
    create_tables(con)

    ts = report["generated_utc"]
    day = utc_day()

    for d in report["decisions"]:
        con.execute("""
            INSERT INTO institutional_promotion_controller_v17_6_1
            (ts, version, brain_ts, key, symbol, side, setup, brain_state, brain_score,
             action, tier, queue_state, institutional_priority, requested_size_mult,
             clean_n, live_n, shadow_n, robust_mean_r, institutional_lcb_r,
             prob_edge_gt_zero, profit_factor, cvar_5_r, max_drawdown_r,
             traceability_score, reasons, payload)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            ts,
            VERSION,
            report["brain_ts"],
            d["key"],
            d["symbol"],
            d["side"],
            d["setup"],
            d["brain_state"],
            d["brain_score"],
            d["action"],
            d["tier"],
            d["queue_state"],
            d["institutional_priority"],
            d["requested_size_mult"],
            d["clean_n"],
            d["live_n"],
            d["shadow_n"],
            d["robust_mean_r"],
            d["institutional_lcb_r"],
            d["prob_edge_gt_zero"],
            d["profit_factor"],
            d["cvar_5_r"],
            d["max_drawdown_r"],
            d["traceability_score"],
            json.dumps(d["reasons"], sort_keys=True),
            json.dumps(d, sort_keys=True),
        ))

    if emit_review_queue:
        for d in report["decisions"]:
            if d["action"] != "REVIEW_MICRO_CANARY_CONTRACT":
                continue

            h = decision_hash(day, d["key"], d["action"], d["tier"])

            con.execute("""
                /* STRUCTURAL_CONTRACT_QUEUE_REFRESH_V2: refresh existing same-day/key/action/tier contract instead of ignoring it */
            INSERT INTO institutional_micro_canary_contract_queue_v17_6_1
            (ts, version, decision_hash, key, symbol, side, setup, queue_state,
                 requested_mode, requested_size_mult, institutional_priority,
                 manual_activation_required, execution_permission, source_action,
                 source_tier, contract_json, payload)
            VALUES
            (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(decision_hash) DO UPDATE SET
        ts=excluded.ts,\n        version=excluded.version,\n        key=excluded.key,\n        symbol=excluded.symbol,\n        side=excluded.side,\n        setup=excluded.setup,\n        queue_state=excluded.queue_state,\n        requested_mode=excluded.requested_mode,\n        requested_size_mult=excluded.requested_size_mult,\n        institutional_priority=excluded.institutional_priority,\n        manual_activation_required=excluded.manual_activation_required,\n        execution_permission=excluded.execution_permission,\n        source_action=excluded.source_action,\n        source_tier=excluded.source_tier,\n        contract_json=excluded.contract_json,\n        payload=excluded.payload
            WHERE excluded.ts >= institutional_micro_canary_contract_queue_v17_6_1.ts
            """, (
                ts,
                VERSION,
                h,
                d["key"],
                d["symbol"],
                d["side"],
                d["setup"],
                "MANUAL_REVIEW_REQUIRED",
                d["requested_mode"],
                d["requested_size_mult"],
                d["institutional_priority"],
                1,
                "NO_AUTO_EXECUTION",
                d["action"],
                d["tier"],
                json.dumps(d["contract"], sort_keys=True),
                json.dumps(d, sort_keys=True),
            ))

    con.execute("""
        INSERT INTO institutional_promotion_health_v17_6_1
        (ts, version, brain_ts, candidates, action_counts, queue_counts,
         open_canaries_global, daily_review_contracts_existing, payload)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        ts,
        VERSION,
        report["brain_ts"],
        report["candidate_count"],
        json.dumps(report["action_counts"], sort_keys=True),
        json.dumps(report["queue_counts"], sort_keys=True),
        report["open_canaries_global"],
        report["daily_review_contracts_existing"],
        json.dumps({
            "emit_review_queue": emit_review_queue,
            "top": report["decisions"][:12],
        }, sort_keys=True),
    ))

    con.commit()
    con.close()


def fmt(x: Any) -> str:
    v = fnum(x, None)
    return "-" if v is None else f"{v:.4f}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write-db", action="store_true")
    ap.add_argument("--emit-review-queue", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    report = build_report()
    write_outputs(
        report,
        write_db=args.write_db,
        emit_review_queue=args.emit_review_queue,
    )

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print("===== V17.6.1 PROMOTION CONTROLLER PRO =====")
        print("quick_check:", report["quick_check"])
        print("brain_ts:", report["brain_ts"])
        print("candidate_count:", report["candidate_count"])
        print("action_counts:", report["action_counts"])
        print("queue_counts:", report["queue_counts"])
        print("open_canaries_global:", report["open_canaries_global"])
        print("daily_review_contracts_existing:", report["daily_review_contracts_existing"])

        for i, d in enumerate(report["decisions"][:30], 1):
            print(
                f"#{i:02d} {d['action']} tier={d['tier']} queue={d['queue_state']} "
                f"prio={d['institutional_priority']:.2f} {d['symbol']} {d['side']} {d['setup'][:28]} "
                f"brain={d['brain_state']} score={d['brain_score']:.2f} clean={d['clean_n']} "
                f"live={d['live_n']} shadow={d['shadow_n']} mean={fmt(d['robust_mean_r'])} "
                f"lcb={fmt(d['institutional_lcb_r'])} pf={fmt(d['profit_factor'])} "
                f"reasons={','.join(d['reasons'])[:140]}"
            )

    return 0 if report.get("quick_check") == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
