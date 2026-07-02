#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

DB = Path("data/joanbot_v14.sqlite")
OUT = Path("data/v19_1_research_governor")
VERSION = "V19.1_INSTITUTIONAL_RESEARCH_GOVERNOR"

BRAIN = "institutional_quant_brain_v17_5_1"
PROMO = "institutional_promotion_controller_v17_6_1"
QUEUE = "institutional_micro_canary_contract_queue_v17_6_1"
INTENTS = "institutional_quant_canary_execution_intents_v17_7_2"
POSITIONS = "paper_micro_canary_positions_v11"
PAYOFF = "institutional_payoff_snapshot_v18_6"

POST_TABLE = "institutional_posterior_research_v19_1"
GOV_TABLE = "institutional_research_governor_v19_1"
HEALTH_TABLE = "institutional_research_governor_health_v19_1"

MAX_DAILY_EMITS = 2
MAX_OPEN_CANARIES = 1
GLOBAL_COOLDOWN_MIN = 30
ALPHA_COOLDOWN_H = 8

MIN_BRAIN_SCORE = 48.0
MIN_BRAIN_MEAN_R = 0.010
MIN_BRAIN_LCB_R = -0.42

MIN_PROB_EDGE = 0.57
MAX_Q_VALUE = 0.55
MIN_ALLOC_SCORE = 52.0
MIN_POST_LCB95 = -0.32
MIN_CVAR10 = -1.80
MIN_PF_CONS_IF_AVAILABLE = 0.70

BASE_SIZE = 0.00085
MAX_SIZE = 0.00250

ADAPTER_INTENT_STATE = "MAX_QUANT_MANUAL_APPROVED_PENDING_PAPER_ADAPTER"
ADAPTER_STATUS = "PENDING_ADAPTER_BINDING"

HARD_TOKENS = [
    "HARD_VETO", "FATAL", "SYSTEM_BLOCK", "RISK_KILL", "DB_NOT_OK",
    "ADAPTER_ERROR", "DRAWDOWN_R_TOO_HIGH", "DRAWDOWN_TOO_HIGH",
]

SAMPLE_TOKENS = [
    "LOW_SAMPLE", "LOW_LIVE", "NEEDS_LIVE_EVIDENCE", "WATCHLIST",
    "MANUAL_REVIEW_REQUIRED", "PROMISING_BUT_NOT_READY",
    "LCB_NOT_POSITIVE", "LCB95_NOT_POSITIVE",
]

STRUCTURAL_BAD_PAYOFF = [
    "expectancy_not_positive",
    "profit_factor_below_1",
    "winrate_below_breakeven",
    "payoff_ratio_too_low",
    "hard_loss_rate_too_high",
]


def utc() -> datetime:
    return datetime.now(timezone.utc)


def iso() -> str:
    return utc().isoformat()


def qid(x: str) -> str:
    return '"' + str(x).replace('"', '""') + '"'


def fnum(x: Any, default=None):
    try:
        if x is None:
            return default
        v = float(x)
        if math.isnan(v) or math.isinf(v):
            return default
        return v
    except Exception:
        return default


def clamp(x: float, lo=0.0, hi=1.0) -> float:
    return max(lo, min(hi, x))


def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def exists(con, table: str) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone() is not None


def cols(con, table: str) -> List[str]:
    if not exists(con, table):
        return []
    return [r[1] for r in con.execute(f"PRAGMA table_info({qid(table)})")]


def one(con, sql: str, args=()):
    con.row_factory = sqlite3.Row
    try:
        r = con.execute(sql, args).fetchone()
        return dict(r) if r else None
    except Exception:
        return None


def many(con, sql: str, args=()):
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute(sql, args).fetchall()]
    except Exception:
        return []


def parse_payload(x):
    if isinstance(x, dict):
        return x
    if not x:
        return {}
    try:
        v = json.loads(str(x))
        return v if isinstance(v, dict) else {}
    except Exception:
        return {}


def reason_list(x) -> List[str]:
    if x is None:
        return []
    if isinstance(x, list):
        return [str(v) for v in x]
    if isinstance(x, dict):
        return [str(k) for k, v in x.items() if v]
    s = str(x)
    try:
        return reason_list(json.loads(s))
    except Exception:
        pass
    out = []
    for p in s.replace("[", "").replace("]", "").replace("'", "").replace('"', "").split(","):
        p = p.strip()
        if p:
            out.append(p)
    return out or ([s] if s.strip() else [])


def latest_ts(con, table):
    c = cols(con, table)
    for tc in ["ts", "created_at", "updated_at", "opened_at", "closed_at"]:
        if tc in c:
            r = one(con, f"SELECT MAX({qid(tc)}) AS mx FROM {qid(table)}")
            if r and r.get("mx"):
                return tc, r["mx"]
    return None, None


@dataclass
class Candidate:
    symbol: str
    side: str
    setup: str
    source: str
    score: float
    mean_r: float
    lcb_r: float
    pf: Optional[float]
    reasons: List[str]
    payload: Dict[str, Any]

    @property
    def alpha_key(self):
        return f"{self.symbol}|{self.side}|{self.setup}"


def pick(row, payload, metrics, *names):
    for n in names:
        if n in row and row.get(n) is not None:
            return row.get(n)
        if isinstance(metrics, dict) and n in metrics and metrics.get(n) is not None:
            return metrics.get(n)
        if isinstance(payload, dict) and n in payload and payload.get(n) is not None:
            return payload.get(n)
    return None


def canonical(row, source) -> Optional[Candidate]:
    payload = parse_payload(row.get("payload"))
    metrics = payload.get("metrics") if isinstance(payload.get("metrics"), dict) else {}

    sym = pick(row, payload, metrics, "symbol", "edge_symbol", "selected_symbol")
    side = pick(row, payload, metrics, "side", "edge_side", "selected_side")
    setup = pick(row, payload, metrics, "setup", "edge_setup", "selected_setup", "family_name")

    if not sym or not side or not setup:
        return None

    score = fnum(pick(row, payload, metrics, "brain_score", "score", "quant_score", "priority", "institutional_priority"), 0.0) or 0.0
    mean_r = fnum(pick(row, payload, metrics, "robust_mean_r", "edge_avg_r", "mean_r", "expectancy_r", "posterior_mean_r"), 0.0) or 0.0
    lcb_r = fnum(pick(row, payload, metrics, "institutional_lcb_r", "edge_lcb_r", "lcb_r", "posterior_lcb_r"), -1.0) or -1.0
    pf = fnum(pick(row, payload, metrics, "profit_factor", "pf"), None)

    rs = []
    for k in ["reasons", "hard_vetoes", "red_flags", "tier", "action", "queue_state", "source_tier"]:
        if row.get(k):
            rs += reason_list(row.get(k))

    return Candidate(str(sym).upper(), str(side).upper(), str(setup), source, score, mean_r, lcb_r, pf, rs, payload)


def load_candidates(con) -> List[Candidate]:
    raw = []

    for table, source in [(BRAIN, "brain"), (PROMO, "promotion"), (QUEUE, "queue")]:
        if not exists(con, table):
            continue

        c = cols(con, table)
        tc, mx = latest_ts(con, table)

        where = ""
        args = ()
        if tc and mx:
            where = f"WHERE {qid(tc)}=?"
            args = (mx,)

        order = "priority" if "priority" in c else "score" if "score" in c else "brain_score" if "brain_score" in c else "rowid"

        rows = many(con, f"""
            SELECT *
            FROM {qid(table)}
            {where}
            ORDER BY {qid(order) if order != 'rowid' else 'rowid'} DESC
            LIMIT 80
        """, args)

        for r in rows:
            cc = canonical(r, source)
            if cc:
                raw.append(cc)

    best: Dict[str, Candidate] = {}
    for c in raw:
        old = best.get(c.alpha_key)
        if old is None or (c.score, c.mean_r, c.lcb_r) > (old.score, old.mean_r, old.lcb_r):
            best[c.alpha_key] = c

    return sorted(best.values(), key=lambda x: (x.score, x.mean_r, x.lcb_r), reverse=True)


def add_sample(out, source, r, w):
    rv = fnum(r)
    if rv is not None:
        out.append({"r": rv, "w": w, "source": source})


def collect_samples(con, c: Candidate) -> List[Dict[str, Any]]:
    out = []

    if exists(con, POSITIONS):
        cc = cols(con, POSITIONS)
        rcol = "net_pnl_r" if "net_pnl_r" in cc else "pnl_r" if "pnl_r" in cc else None
        if rcol and all(x in cc for x in ["symbol", "side", "setup"]):
            rows = many(con, f"""
                SELECT {qid(rcol)} AS r
                FROM {qid(POSITIONS)}
                WHERE UPPER(COALESCE(symbol,''))=?
                  AND UPPER(COALESCE(side,''))=?
                  AND COALESCE(setup,'')=?
                  AND closed_at IS NOT NULL
                ORDER BY closed_at DESC
                LIMIT 500
            """, (c.symbol, c.side, c.setup))
            for r in rows:
                add_sample(out, "live_exact", r.get("r"), 1.00)

    if exists(con, "trades"):
        cc = cols(con, "trades")
        rcol = next((x for x in ["net_pnl_r", "pnl_r", "result_r", "r"] if x in cc), None)
        if rcol and "symbol" in cc and "side" in cc:
            if "setup" in cc:
                rows = many(con, f"""
                    SELECT {qid(rcol)} AS r
                    FROM trades
                    WHERE UPPER(COALESCE(symbol,''))=?
                      AND UPPER(COALESCE(side,''))=?
                      AND COALESCE(setup,'')=?
                    ORDER BY rowid DESC
                    LIMIT 500
                """, (c.symbol, c.side, c.setup))
            else:
                rows = many(con, f"""
                    SELECT {qid(rcol)} AS r
                    FROM trades
                    WHERE UPPER(COALESCE(symbol,''))=?
                      AND UPPER(COALESCE(side,''))=?
                    ORDER BY rowid DESC
                    LIMIT 500
                """, (c.symbol, c.side))
            for r in rows:
                add_sample(out, "trade_exact", r.get("r"), 1.00)

    if exists(con, "universal_shadow_results_v2"):
        cc = cols(con, "universal_shadow_results_v2")
        if all(x in cc for x in ["symbol", "side", "setup", "result_r"]):
            rows = many(con, """
                SELECT result_r AS r
                FROM universal_shadow_results_v2
                WHERE UPPER(COALESCE(symbol,''))=?
                  AND UPPER(COALESCE(side,''))=?
                  AND COALESCE(setup,'')=?
                ORDER BY rowid DESC
                LIMIT 1000
            """, (c.symbol, c.side, c.setup))
            for r in rows:
                add_sample(out, "shadow_exact", r.get("r"), 0.12)

            rows = many(con, """
                SELECT result_r AS r
                FROM universal_shadow_results_v2
                WHERE COALESCE(setup,'')=?
                  AND NOT (UPPER(COALESCE(symbol,''))=? AND UPPER(COALESCE(side,''))=?)
                ORDER BY rowid DESC
                LIMIT 600
            """, (c.setup, c.symbol, c.side))
            for r in rows:
                add_sample(out, "shadow_family", r.get("r"), 0.025)

            rows = many(con, """
                SELECT result_r AS r
                FROM universal_shadow_results_v2
                WHERE UPPER(COALESCE(symbol,''))=?
                  AND UPPER(COALESCE(side,''))=?
                  AND COALESCE(setup,'')<>?
                ORDER BY rowid DESC
                LIMIT 600
            """, (c.symbol, c.side, c.setup))
            for r in rows:
                add_sample(out, "shadow_symbol_side", r.get("r"), 0.025)

    if exists(con, "universal_shadow_cases_v2"):
        cc = cols(con, "universal_shadow_cases_v2")
        rcol = next((x for x in ["result_r", "pnl_r"] if x in cc), None)
        if rcol and all(x in cc for x in ["symbol", "side", "setup"]):
            rows = many(con, f"""
                SELECT {qid(rcol)} AS r
                FROM universal_shadow_cases_v2
                WHERE UPPER(COALESCE(symbol,''))=?
                  AND UPPER(COALESCE(side,''))=?
                  AND COALESCE(setup,'')=?
                ORDER BY rowid DESC
                LIMIT 1000
            """, (c.symbol, c.side, c.setup))
            for r in rows:
                add_sample(out, "shadow_case_exact", r.get("r"), 0.08)

    return out


def w_cvar10(vals: List[Tuple[float, float]]) -> Optional[float]:
    if not vals:
        return None
    xs = sorted(vals, key=lambda z: z[0])
    total_w = sum(w for _, w in xs)
    target = max(total_w * 0.10, 1e-9)
    acc_w = 0.0
    acc = 0.0
    for r, w in xs:
        take = min(w, target - acc_w)
        if take <= 0:
            break
        acc += r * take
        acc_w += take
        if acc_w >= target:
            break
    return acc / acc_w if acc_w > 0 else None


def percentile(xs: List[float], p: float):
    if not xs:
        return None
    ys = sorted(xs)
    i = int(max(0, min(len(ys) - 1, round(p * (len(ys) - 1)))))
    return ys[i]


def posterior(samples: List[Dict[str, Any]]) -> Dict[str, Any]:
    vals = []
    mix: Dict[str, float] = {}
    raw = []

    for s in samples:
        r = fnum(s.get("r"))
        w = fnum(s.get("w"), 0.0) or 0.0
        src = str(s.get("source") or "unknown")
        if r is None or w <= 0:
            continue
        vals.append((r, w, src))
        raw.append(r)
        mix[src] = mix.get(src, 0.0) + w

    if not vals:
        return {
            "n_raw": 0,
            "n_eff": 0.0,
            "live_eff": 0.0,
            "shadow_eff": 0.0,
            "neighbor_eff": 0.0,
            "mean_r": None,
            "shrunk_mean_r": None,
            "lcb95_r": None,
            "prob_edge_pos": 0.50,
            "p_value_edge": 0.50,
            "cvar10_r": None,
            "pf_cons": None,
            "payoff_cons": None,
            "stability": 0.0,
            "source_mix": {},
        }

    n_eff = sum(w for _, w, _ in vals)
    live_eff = sum(w for _, w, src in vals if "live" in src or "trade" in src)
    shadow_eff = sum(w for _, w, src in vals if "shadow_exact" in src or "shadow_case_exact" in src)
    neighbor_eff = sum(w for _, w, src in vals if "family" in src or "symbol_side" in src)

    mean_r = sum(r * w for r, w, _ in vals) / n_eff
    var = sum(w * (r - mean_r) ** 2 for r, w, _ in vals) / max(n_eff, 1e-9)
    sd = math.sqrt(max(0.0, var))

    prior_strength = 24.0
    if live_eff >= 3:
        prior_strength = 14.0
    if live_eff >= 8:
        prior_strength = 8.0

    shrunk = mean_r * n_eff / (n_eff + prior_strength)

    model_penalty = 0.025
    model_penalty += 0.070 * (1.0 - clamp(live_eff / 6.0))
    model_penalty += 0.035 * clamp(neighbor_eff / max(n_eff, 1e-9))

    se = sd / math.sqrt(max(n_eff, 1.0))
    adj_se = math.sqrt(se * se + model_penalty * model_penalty)
    lcb95 = shrunk - 1.96 * adj_se
    prob_edge = norm_cdf(shrunk / max(adj_se, 1e-9))
    p_value = 1.0 - prob_edge

    wins = [(r, w) for r, w, _ in vals if r > 0.10]
    losses = [(abs(r), w) for r, w, _ in vals if r < -0.10]

    pf_cons = None
    payoff_cons = None

    if losses:
        gross_win = sum(r * w for r, w in wins)
        gross_loss = sum(r * w for r, w in losses)
        if gross_loss > 0:
            pf_cons = (gross_win * 0.75) / (gross_loss * 1.25)

    if wins and losses:
        win_raw = [r for r, _ in wins]
        loss_raw = [r for r, _ in losses]
        aw_lcb = percentile(win_raw, 0.25)
        al_ucb = percentile(loss_raw, 0.75)
        if aw_lcb is not None and al_ucb and al_ucb > 0:
            payoff_cons = aw_lcb / al_ucb

    cvar10 = w_cvar10([(r, w) for r, w, _ in vals])

    stability = 0.0
    if len(raw) >= 10:
        half = len(raw) // 2
        old = raw[:half]
        recent = raw[half:]
        old_m = sum(old) / len(old)
        recent_m = sum(recent) / len(recent)
        stability = clamp(1.0 - abs(recent_m - old_m) / 0.75)

    return {
        "n_raw": len(raw),
        "n_eff": n_eff,
        "live_eff": live_eff,
        "shadow_eff": shadow_eff,
        "neighbor_eff": neighbor_eff,
        "mean_r": mean_r,
        "shrunk_mean_r": shrunk,
        "lcb95_r": lcb95,
        "prob_edge_pos": prob_edge,
        "p_value_edge": p_value,
        "cvar10_r": cvar10,
        "pf_cons": pf_cons,
        "payoff_cons": payoff_cons,
        "stability": stability,
        "source_mix": mix,
    }


def bh_qvalues(posts: List[Dict[str, Any]]) -> None:
    m = len(posts)
    if m == 0:
        return

    ordered = sorted(enumerate(posts), key=lambda z: z[1].get("p_value_edge", 1.0))
    qvals = [1.0] * m
    prev = 1.0

    for rank_from_end, (idx, p) in enumerate(reversed(ordered), 1):
        rank = m - rank_from_end + 1
        raw_q = (p.get("p_value_edge", 1.0) * m) / max(rank, 1)
        prev = min(prev, raw_q)
        qvals[idx] = min(1.0, prev)

    for i, q in enumerate(qvals):
        posts[i]["q_value"] = q


def create_tables(con) -> None:
    con.execute(f"""
        CREATE TABLE IF NOT EXISTS {qid(POST_TABLE)} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            version TEXT NOT NULL,
            alpha_key TEXT,
            symbol TEXT,
            side TEXT,
            setup TEXT,
            n_raw INTEGER,
            n_eff REAL,
            live_eff REAL,
            shadow_eff REAL,
            neighbor_eff REAL,
            mean_r REAL,
            shrunk_mean_r REAL,
            lcb95_r REAL,
            prob_edge_pos REAL,
            p_value_edge REAL,
            q_value REAL,
            cvar10_r REAL,
            pf_cons REAL,
            payoff_cons REAL,
            stability REAL,
            source_mix TEXT,
            payload TEXT
        )
    """)

    con.execute(f"""
        CREATE TABLE IF NOT EXISTS {qid(GOV_TABLE)} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            version TEXT NOT NULL,
            decision_hash TEXT UNIQUE,
            mode TEXT,
            state TEXT,
            action TEXT,
            alpha_key TEXT,
            symbol TEXT,
            side TEXT,
            setup TEXT,
            source TEXT,
            brain_score REAL,
            brain_mean_r REAL,
            brain_lcb_r REAL,
            n_eff REAL,
            live_eff REAL,
            shrunk_mean_r REAL,
            lcb95_r REAL,
            prob_edge_pos REAL,
            q_value REAL,
            cvar10_r REAL,
            pf_cons REAL,
            payoff_cons REAL,
            stability REAL,
            allocation_score REAL,
            size_mult REAL,
            priority REAL,
            emitted_intent_id INTEGER,
            reasons TEXT,
            payload TEXT
        )
    """)

    con.execute(f"""
        CREATE TABLE IF NOT EXISTS {qid(HEALTH_TABLE)} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            version TEXT NOT NULL,
            db_quick_check TEXT,
            mode TEXT,
            candidates INTEGER,
            approved INTEGER,
            blocked INTEGER,
            emitted INTEGER,
            open_positions INTEGER,
            pending_intents INTEGER,
            daily_emits INTEGER,
            summary TEXT,
            payload TEXT
        )
    """)


def latest_payoff(con) -> Dict[str, Any]:
    if not exists(con, PAYOFF):
        return {}
    r = one(con, f"SELECT * FROM {qid(PAYOFF)} WHERE scope='GLOBAL' ORDER BY ts DESC LIMIT 1")
    return r or one(con, f"SELECT * FROM {qid(PAYOFF)} ORDER BY ts DESC LIMIT 1") or {}


def open_positions(con) -> int:
    if not exists(con, POSITIONS):
        return 0
    c = cols(con, POSITIONS)
    if "closed_at" in c and "status" in c:
        r = one(con, f"""
            SELECT COUNT(*) AS n
            FROM {qid(POSITIONS)}
            WHERE (closed_at IS NULL AND opened_at IS NOT NULL)
               OR UPPER(COALESCE(status,'')) IN ('OPEN','ACTIVE','RUNNING','OPEN_MANAGED')
        """)
        return int((r or {}).get("n") or 0)
    return 0


def pending_intents(con) -> int:
    if not exists(con, INTENTS):
        return 0
    c = cols(con, INTENTS)
    if "adapter_status" in c:
        r = one(con, f"""
            SELECT COUNT(*) AS n
            FROM {qid(INTENTS)}
            WHERE UPPER(COALESCE(adapter_status,'')) IN ('PENDING','PENDING_ADAPTER_BINDING','NEW')
        """)
        return int((r or {}).get("n") or 0)
    return 0


def daily_emits(con) -> int:
    start = utc().replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    r = one(con, f"""
        SELECT COUNT(*) AS n
        FROM {qid(GOV_TABLE)}
        WHERE ts >= ?
          AND emitted_intent_id IS NOT NULL
    """, (start,))
    return int((r or {}).get("n") or 0)


def recent_global_emit(con) -> bool:
    cutoff = (utc() - timedelta(minutes=GLOBAL_COOLDOWN_MIN)).isoformat()
    r = one(con, f"""
        SELECT COUNT(*) AS n
        FROM {qid(GOV_TABLE)}
        WHERE ts >= ?
          AND emitted_intent_id IS NOT NULL
    """, (cutoff,))
    return int((r or {}).get("n") or 0) > 0


def recent_alpha_emit(con, alpha_key: str) -> bool:
    cutoff = (utc() - timedelta(hours=ALPHA_COOLDOWN_H)).isoformat()
    r = one(con, f"""
        SELECT COUNT(*) AS n
        FROM {qid(GOV_TABLE)}
        WHERE alpha_key=?
          AND ts >= ?
          AND emitted_intent_id IS NOT NULL
    """, (alpha_key, cutoff))
    return int((r or {}).get("n") or 0) > 0


def text_has(tokens: List[str], reasons: List[str]) -> bool:
    t = "|".join(str(x).upper() for x in reasons)
    return any(tok.upper() in t for tok in tokens)


def sample_starved(c: Candidate, payoff: Dict[str, Any]) -> bool:
    payoff_text = str(payoff.get("reasons") or "").upper()
    return (
        text_has(SAMPLE_TOKENS, c.reasons)
        or "LCB95_NOT_POSITIVE" in payoff_text
        or str(payoff.get("payoff_health") or "").upper() in {"BAD", "INSUFFICIENT_SAMPLE"}
    )


def payoff_structural_bad(payoff: Dict[str, Any]) -> bool:
    txt = str(payoff.get("reasons") or "").lower()
    return any(x in txt for x in STRUCTURAL_BAD_PAYOFF)


def allocation_score(c: Candidate, p: Dict[str, Any]) -> float:
    score = 0.0
    score += 16 * clamp(c.score / 70.0)
    score += 14 * clamp((c.mean_r + 0.02) / 0.20)
    score += 14 * clamp((c.lcb_r + 0.42) / 0.45)
    score += 18 * clamp(((p.get("shrunk_mean_r") or -0.10) + 0.04) / 0.22)
    score += 18 * clamp(((p.get("lcb95_r") or -0.35) + 0.35) / 0.45)
    score += 10 * clamp(((p.get("prob_edge_pos") or 0.50) - 0.50) / 0.25)
    score += 5 * clamp((p.get("n_eff") or 0.0) / 60.0)
    score += 5 * clamp(p.get("stability") or 0.0)
    return round(clamp(score, 0, 100), 2)


def decide(con, c: Candidate, p: Dict[str, Any], payoff: Dict[str, Any]) -> Dict[str, Any]:
    reasons = []

    if open_positions(con) >= MAX_OPEN_CANARIES:
        reasons.append("OPEN_CANARY_CAP")
    if pending_intents(con) > 0:
        reasons.append("PENDING_INTENT_EXISTS")
    if daily_emits(con) >= MAX_DAILY_EMITS:
        reasons.append("DAILY_EMIT_CAP")
    if recent_global_emit(con):
        reasons.append("GLOBAL_COOLDOWN")
    if recent_alpha_emit(con, c.alpha_key):
        reasons.append("ALPHA_COOLDOWN")
    if text_has(HARD_TOKENS, c.reasons):
        reasons.append("HARD_REASON_PRESENT")
    if payoff_structural_bad(payoff):
        reasons.append("PAYOFF_STRUCTURAL_BAD")
    if not sample_starved(c, payoff):
        reasons.append("NOT_SAMPLE_STARVATION_CASE")

    if c.score < MIN_BRAIN_SCORE:
        reasons.append("BRAIN_SCORE_TOO_LOW")
    if c.mean_r < MIN_BRAIN_MEAN_R:
        reasons.append("BRAIN_MEAN_TOO_LOW")
    if c.lcb_r < MIN_BRAIN_LCB_R:
        reasons.append("BRAIN_LCB_TOO_NEGATIVE")

    if (p.get("prob_edge_pos") or 0.0) < MIN_PROB_EDGE:
        reasons.append("PROB_EDGE_TOO_LOW")
    if (p.get("q_value") or 1.0) > MAX_Q_VALUE:
        reasons.append("FDR_Q_TOO_HIGH")
    if p.get("lcb95_r") is not None and p["lcb95_r"] < MIN_POST_LCB95:
        reasons.append("POSTERIOR_LCB95_TOO_NEGATIVE")
    if p.get("cvar10_r") is not None and p["cvar10_r"] < MIN_CVAR10:
        reasons.append("CVAR10_TOO_BAD")
    if p.get("pf_cons") is not None and p["pf_cons"] < MIN_PF_CONS_IF_AVAILABLE:
        reasons.append("PF_CONSERVATIVE_TOO_LOW")

    score = allocation_score(c, p)
    if score < MIN_ALLOC_SCORE:
        reasons.append("ALLOC_SCORE_TOO_LOW")

    state = "BLOCKED"
    action = "OBSERVE_ONLY"

    if not reasons:
        state = "APPROVED_MICRO_RESEARCH_CANARY"
        action = "EMIT_ADAPTER_INTENT"
        reasons.append("APPROVED_BY_V19_1_RESEARCH_GOVERNOR")

    size = BASE_SIZE
    size *= 0.75 + 0.35 * clamp(score / 100.0)
    size *= 0.80 + 0.40 * clamp(((p.get("prob_edge_pos") or 0.50) - 0.50) / 0.30)

    if str(payoff.get("payoff_health") or "").upper() == "BAD":
        size *= 0.75
    if p.get("lcb95_r") is not None and p["lcb95_r"] > 0:
        size *= 1.20

    size = max(0.00060, min(MAX_SIZE, size))
    priority = score + 0.10 * c.score + 8.0 * clamp((p.get("prob_edge_pos") or 0.50) - 0.50, 0, 1)

    return {
        "state": state,
        "action": action,
        "reasons": reasons,
        "allocation_score": score,
        "size_mult": size,
        "priority": priority,
    }


def decision_hash(c: Candidate, d: Dict[str, Any]) -> str:
    bucket = utc().strftime("%Y%m%d%H")
    raw = f"{VERSION}|{bucket}|{c.alpha_key}|{d['state']}|{d['action']}"
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def save_post(con, c: Candidate, p: Dict[str, Any]) -> None:
    con.execute(f"""
        INSERT INTO {qid(POST_TABLE)}
        (ts,version,alpha_key,symbol,side,setup,n_raw,n_eff,live_eff,shadow_eff,neighbor_eff,
         mean_r,shrunk_mean_r,lcb95_r,prob_edge_pos,p_value_edge,q_value,cvar10_r,
         pf_cons,payoff_cons,stability,source_mix,payload)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        iso(), VERSION, c.alpha_key, c.symbol, c.side, c.setup,
        p.get("n_raw"), p.get("n_eff"), p.get("live_eff"), p.get("shadow_eff"), p.get("neighbor_eff"),
        p.get("mean_r"), p.get("shrunk_mean_r"), p.get("lcb95_r"),
        p.get("prob_edge_pos"), p.get("p_value_edge"), p.get("q_value"),
        p.get("cvar10_r"), p.get("pf_cons"), p.get("payoff_cons"), p.get("stability"),
        json.dumps(p.get("source_mix") or {}, sort_keys=True),
        json.dumps(p, sort_keys=True),
    ))


def save_decision(con, mode, c: Candidate, p: Dict[str, Any], d: Dict[str, Any], emitted_id=None) -> None:
    h = decision_hash(c, d)
    payload = {"candidate": c.__dict__, "posterior": p, "decision": d}

    try:
        con.execute(f"""
            INSERT INTO {qid(GOV_TABLE)}
            (ts,version,decision_hash,mode,state,action,alpha_key,symbol,side,setup,source,
             brain_score,brain_mean_r,brain_lcb_r,n_eff,live_eff,shrunk_mean_r,lcb95_r,
             prob_edge_pos,q_value,cvar10_r,pf_cons,payoff_cons,stability,allocation_score,
             size_mult,priority,emitted_intent_id,reasons,payload)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            iso(), VERSION, h, mode, d["state"], d["action"], c.alpha_key,
            c.symbol, c.side, c.setup, c.source,
            c.score, c.mean_r, c.lcb_r,
            p.get("n_eff"), p.get("live_eff"), p.get("shrunk_mean_r"), p.get("lcb95_r"),
            p.get("prob_edge_pos"), p.get("q_value"), p.get("cvar10_r"), p.get("pf_cons"),
            p.get("payoff_cons"), p.get("stability"), d.get("allocation_score"),
            d.get("size_mult"), d.get("priority"), emitted_id,
            ",".join(d.get("reasons") or []),
            json.dumps(payload, sort_keys=True),
        ))
    except sqlite3.IntegrityError:
        pass


def emit_intent(con, c: Candidate, d: Dict[str, Any]) -> Tuple[Optional[int], str]:
    if not exists(con, INTENTS):
        return None, "INTENT_TABLE_MISSING"

    cc = cols(con, INTENTS)
    needed = {"intent_state", "symbol", "side", "setup", "requested_size_mult"}

    if not needed.issubset(set(cc)):
        return None, "INTENT_SCHEMA_INCOMPATIBLE"

    values = {
        "ts": iso(),
        "created_at": iso(),
        "intent_state": ADAPTER_INTENT_STATE,
        "adapter_status": ADAPTER_STATUS,
        "symbol": c.symbol,
        "side": c.side,
        "setup": c.setup,
        "requested_size_mult": d["size_mult"],
        "institutional_priority": d["priority"],
        "source_tier": VERSION,
        "source": VERSION,
        "reason": ",".join(d.get("reasons") or []),
        "reasons": ",".join(d.get("reasons") or []),
        "payload": json.dumps({
            "version": VERSION,
            "alpha_key": c.alpha_key,
            "decision": d,
        }, sort_keys=True),
    }

    insert_cols = [x for x in cc if x in values and x != "id"]
    sql = f"INSERT INTO {qid(INTENTS)} ({','.join(qid(x) for x in insert_cols)}) VALUES ({','.join(['?'] * len(insert_cols))})"
    con.execute(sql, tuple(values[x] for x in insert_cols))
    return int(con.execute("SELECT last_insert_rowid()").fetchone()[0]), "EMITTED"


def write_summary(report: Dict[str, Any]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)

    lines = [
        "# V19.1 Institutional Research Governor",
        "",
        f"- UTC: `{iso()}`",
        f"- Mode: `{report['mode']}`",
        f"- DB: `{report['db']}`",
        f"- Candidates: `{report['candidates']}`",
        f"- Approved: `{report['approved']}`",
        f"- Blocked: `{report['blocked']}`",
        f"- Emitted: `{report['emitted']}`",
        f"- Open positions: `{report['open_positions']}`",
        f"- Pending intents: `{report['pending_intents']}`",
        f"- Daily emits: `{report['daily_emits']}`",
        f"- Summary: `{report['summary']}`",
        "",
        "## Top decisions",
    ]

    for d in report["decisions"][:24]:
        lines.append(
            f"- `{d['state']}` {d['symbol']} {d['side']} {d['setup']} "
            f"alloc={d['allocation_score']:.2f} size={d['size_mult']:.5f} "
            f"prob={d['prob_edge_pos']:.3f} q={d['q_value']:.3f} "
            f"brain={d['brain_score']:.2f} mean={d['brain_mean_r']:+.4f} "
            f"lcb={d['brain_lcb_r']:+.4f} n_eff={d['n_eff']:.1f} "
            f"live={d['live_eff']:.1f} post_lcb={d['lcb95_r']} "
            f"cvar={d['cvar10_r']} pfC={d['pf_cons']} "
            f"intent={d.get('emitted_intent_id')} reasons={d['reasons']}"
        )

    (OUT / "research_governor_summary.md").write_text("\n".join(lines))
    (OUT / "research_governor_report.json").write_text(json.dumps(report, indent=2, sort_keys=True))


def run(emit_one=False, max_candidates=50) -> Dict[str, Any]:
    if not DB.exists():
        raise RuntimeError("DB_MISSING")

    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    create_tables(con)

    dbq = con.execute("PRAGMA quick_check").fetchone()[0]
    payoff = latest_payoff(con)
    candidates = load_candidates(con)[:max_candidates]

    packs = []
    for c in candidates:
        samples = collect_samples(con, c)
        p = posterior(samples)
        packs.append((c, p))

    bh_qvalues([p for _, p in packs])

    decisions = []
    approved = 0
    blocked = 0
    emitted = 0

    for c, p in packs:
        save_post(con, c, p)
        d = decide(con, c, p, payoff)

        if d["state"] == "APPROVED_MICRO_RESEARCH_CANARY":
            approved += 1
        else:
            blocked += 1

        row = {
            **d,
            "symbol": c.symbol,
            "side": c.side,
            "setup": c.setup,
            "alpha_key": c.alpha_key,
            "source": c.source,
            "brain_score": c.score,
            "brain_mean_r": c.mean_r,
            "brain_lcb_r": c.lcb_r,
            "n_eff": p.get("n_eff") or 0.0,
            "live_eff": p.get("live_eff") or 0.0,
            "shrunk_mean_r": p.get("shrunk_mean_r"),
            "lcb95_r": p.get("lcb95_r"),
            "prob_edge_pos": p.get("prob_edge_pos") or 0.0,
            "q_value": p.get("q_value") or 1.0,
            "cvar10_r": p.get("cvar10_r"),
            "pf_cons": p.get("pf_cons"),
            "payoff_cons": p.get("payoff_cons"),
            "stability": p.get("stability") or 0.0,
            "emitted_intent_id": None,
        }
        decisions.append(row)
        save_decision(con, "EMIT_ONE" if emit_one else "REVIEW_ONLY", c, p, d)

    decisions.sort(
        key=lambda x: (
            1 if x["state"] == "APPROVED_MICRO_RESEARCH_CANARY" else 0,
            x["priority"],
            x["prob_edge_pos"],
        ),
        reverse=True,
    )

    if emit_one:
        top = next((x for x in decisions if x["state"] == "APPROVED_MICRO_RESEARCH_CANARY"), None)
        if top:
            c = Candidate(
                top["symbol"], top["side"], top["setup"], top["source"],
                top["brain_score"], top["brain_mean_r"], top["brain_lcb_r"],
                None, [], {},
            )
            intent_id, status = emit_intent(con, c, top)
            top["emit_status"] = status
            if intent_id:
                emitted = 1
                top["emitted_intent_id"] = intent_id
                save_decision(con, "EMIT_ONE", c, {}, top, intent_id)

    report = {
        "version": VERSION,
        "utc": iso(),
        "mode": "EMIT_ONE" if emit_one else "REVIEW_ONLY",
        "db": dbq,
        "candidates": len(candidates),
        "approved": approved,
        "blocked": blocked,
        "emitted": emitted,
        "open_positions": open_positions(con),
        "pending_intents": pending_intents(con),
        "daily_emits": daily_emits(con),
        "summary": "EMITTED_ONE_MICRO_RESEARCH_CANARY" if emitted else ("APPROVED_BUT_NOT_EMITTED" if approved else "NO_SAFE_RESEARCH_ALLOCATION"),
        "payoff": payoff,
        "decisions": decisions,
    }

    con.execute(f"""
        INSERT INTO {qid(HEALTH_TABLE)}
        (ts,version,db_quick_check,mode,candidates,approved,blocked,emitted,
         open_positions,pending_intents,daily_emits,summary,payload)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        iso(), VERSION, dbq, report["mode"], report["candidates"], approved,
        blocked, emitted, report["open_positions"], report["pending_intents"],
        report["daily_emits"], report["summary"], json.dumps(report, sort_keys=True),
    ))

    con.commit()
    con.close()

    write_summary(report)
    return report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--emit-one", action="store_true")
    ap.add_argument("--max-candidates", type=int, default=50)
    args = ap.parse_args()

    r = run(args.emit_one, args.max_candidates)

    print("===== V19.1 INSTITUTIONAL RESEARCH GOVERNOR =====")
    print("mode:", r["mode"])
    print("db:", r["db"])
    print("candidates:", r["candidates"])
    print("approved:", r["approved"])
    print("blocked:", r["blocked"])
    print("emitted:", r["emitted"])
    print("open_positions:", r["open_positions"])
    print("pending_intents:", r["pending_intents"])
    print("daily_emits:", r["daily_emits"])
    print("summary:", r["summary"])
    print("summary_file: data/v19_1_research_governor/research_governor_summary.md")

    for i, d in enumerate(r["decisions"][:14], 1):
        print(
            f"#{i:02d} {d['state']} {d['symbol']} {d['side']} {d['setup']} "
            f"alloc={d['allocation_score']:.2f} size={d['size_mult']:.5f} "
            f"prob={d['prob_edge_pos']:.3f} q={d['q_value']:.3f} "
            f"score={d['brain_score']:.2f} mean={d['brain_mean_r']:+.4f} "
            f"lcb={d['brain_lcb_r']:+.4f} n_eff={d['n_eff']:.1f} "
            f"live={d['live_eff']:.1f} post_lcb={d['lcb95_r']} "
            f"cvar={d['cvar10_r']} pfC={d['pf_cons']} "
            f"intent={d.get('emitted_intent_id')} "
            f"reasons={','.join(d['reasons'])[:180]}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
