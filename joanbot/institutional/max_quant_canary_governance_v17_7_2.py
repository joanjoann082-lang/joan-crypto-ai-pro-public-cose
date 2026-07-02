#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import sqlite3
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

ROOT = Path.cwd()
DB = ROOT / "data" / "joanbot_v14.sqlite"
OUT = ROOT / "data" / "v17_7_2"

VERSION = "V17.7.2_MAX_QUANT_CANARY_GOVERNANCE_GATE"

QUEUE_TABLE = "institutional_micro_canary_contract_queue_v17_6_1"
BRAIN_TABLE = "institutional_quant_brain_v17_5_1"
PROMO_TABLE = "institutional_promotion_controller_v17_6_1"

GOV_TABLE = "institutional_quant_canary_governance_v17_7_2"
INTENT_TABLE = "institutional_quant_canary_execution_intents_v17_7_2"
HEALTH_TABLE = "institutional_quant_canary_governance_health_v17_7_2"
AUDIT_TABLE = "institutional_quant_canary_governance_audit_v17_7_2"

MAX_OPEN_CANARIES_GLOBAL = 2
MAX_OPEN_CANARIES_PER_KEY = 1
MAX_DAILY_INTENTS = 2
MAX_SIZE_MULT = 0.025
MIN_SIZE_MULT = 0.005
CONTRACT_TTL_HOURS = 24
BRAIN_STALE_MINUTES = 20
COOLDOWN_HOURS_AFTER_INTENT = 18

PRIOR_N = 90.0
PRIOR_MEAN_R = 0.0
DEFAULT_STD_R = 0.45
BOOTSTRAP_N = 500
MIN_CLEAN_N_REVIEW = 150
MIN_SHADOW_N_REVIEW = 150

FATAL_FLAGS = {
    "HARD_VETO_PRESENT",
    "CVaR_TOO_NEGATIVE",
    "FAT_TAIL_WORST_R",
    "DRAWDOWN_R_TOO_HIGH",
    "NO_CLEAN_EVIDENCE",
}

SOURCE_SPEC = {
    "outcome_clean": {
        "table": "outcome_provenance_v1",
        "rcols": ["pnl_r"],
        "weight": 1.00,
        "kind": "live",
    },
    "micro_canary_net": {
        "table": "paper_micro_canary_positions_v11",
        "rcols": ["net_pnl_r", "pnl_r", "gross_pnl_r"],
        "weight": 0.88,
        "kind": "live",
    },
    "position_closed": {
        "table": "positions",
        "rcols": ["net_pnl_r", "pnl_r", "pnl_usd"],
        "weight": 0.72,
        "kind": "live",
    },
    "trade_closed": {
        "table": "trades",
        "rcols": ["net_pnl_r", "pnl_r", "pnl_usd"],
        "weight": 0.62,
        "kind": "live",
    },
    "shadow_resolved": {
        "table": "universal_shadow_results_v2",
        "rcols": ["result_r"],
        "weight": 0.20,
        "kind": "shadow",
    },
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


def normal_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def safe_json(x: Any, fallback: Any) -> Any:
    if x is None:
        return fallback
    if isinstance(x, (dict, list)):
        return x
    try:
        return json.loads(str(x))
    except Exception:
        return fallback


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


def age_hours(ts: Any) -> Optional[float]:
    m = age_minutes(ts)
    return None if m is None else m / 60.0


def sha256_obj(obj: Any) -> str:
    raw = json.dumps(obj, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def connect_ro() -> sqlite3.Connection:
    con = sqlite3.connect("file:" + str(DB.resolve()) + "?mode=ro", uri=True, timeout=10)
    con.row_factory = sqlite3.Row
    return con


def connect_rw() -> sqlite3.Connection:
    con = sqlite3.connect(str(DB), timeout=20)
    con.row_factory = sqlite3.Row
    return con


def exists(con: sqlite3.Connection, table: str) -> bool:
    return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def columns(con: sqlite3.Connection, table: str) -> List[str]:
    if not exists(con, table):
        return []
    return [r[1] for r in con.execute(f"PRAGMA table_info({qid(table)})").fetchall()]


def latest_ts(con: sqlite3.Connection, table: str) -> Optional[str]:
    if not exists(con, table):
        return None
    return con.execute(f"SELECT MAX(ts) FROM {qid(table)}").fetchone()[0]


def latest_row_by_key(con: sqlite3.Connection, table: str, key: str) -> Optional[Dict[str, Any]]:
    ts = latest_ts(con, table)
    if not ts:
        return None
    row = con.execute(
        f"SELECT * FROM {qid(table)} WHERE ts=? AND key=? ORDER BY id DESC LIMIT 1",
        (ts, key)
    ).fetchone()
    return dict(row) if row else None


def row_ts(row: Dict[str, Any]) -> str:
    for c in ("closed_at", "resolved_at", "updated_at", "ts", "opened_at", "created_at", "last_managed_at"):
        if row.get(c) not in (None, ""):
            d = parse_dt(row.get(c))
            if d:
                return d.isoformat()
    return ""


def get_first(row: Dict[str, Any], names: Iterable[str], default: Any = None) -> Any:
    for n in names:
        if n in row and row.get(n) not in (None, "", "None", "nan", "NaN"):
            return row.get(n)
    return default


def is_closed(row: Dict[str, Any]) -> bool:
    st = str(row.get("status") or "").upper()
    if st in {"OPEN", "ACTIVE", "RUNNING", "PENDING"}:
        return False
    if row.get("closed_at") not in (None, ""):
        return True
    if st in {"CLOSED", "DONE", "RESOLVED", "EXITED", "WIN", "LOSS"}:
        return True
    return any(fnum(row.get(c), None) is not None for c in ("net_pnl_r", "pnl_r", "gross_pnl_r", "result_r"))


def clean_outcome(row: Dict[str, Any]) -> bool:
    c = row.get("clean_for_evidence")
    if c is not None:
        b = str(c).strip().lower()
        if b in {"0", "false", "no"}:
            return False
        if b in {"1", "true", "yes"}:
            return True
    return row.get("excluded_reason") in (None, "")


def load_review_queue(con: sqlite3.Connection) -> List[Dict[str, Any]]:
    if not exists(con, QUEUE_TABLE):
        raise RuntimeError(f"missing queue table: {QUEUE_TABLE}")
    rows = con.execute(f"""
        SELECT *
        FROM {qid(QUEUE_TABLE)}
        WHERE queue_state='MANUAL_REVIEW_REQUIRED'
        ORDER BY institutional_priority DESC, id DESC
    """).fetchall()
    return [dict(r) for r in rows]


def open_canaries_global(con: sqlite3.Connection) -> int:
    table = "paper_micro_canary_positions_v11"
    if not exists(con, table):
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


def open_canaries_key(con: sqlite3.Connection, symbol: str, side: str, setup: str) -> int:
    table = "paper_micro_canary_positions_v11"
    if not exists(con, table):
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


def daily_intent_count(con: sqlite3.Connection) -> int:
    if not exists(con, INTENT_TABLE):
        return 0
    try:
        return int(con.execute(
            f"SELECT COUNT(*) FROM {qid(INTENT_TABLE)} WHERE substr(ts,1,10)=?",
            (utc_day(),)
        ).fetchone()[0])
    except Exception:
        return 0


def recent_intent_for_key(con: sqlite3.Connection, key: str) -> Optional[Dict[str, Any]]:
    if not exists(con, INTENT_TABLE):
        return None
    row = con.execute(
        f"SELECT * FROM {qid(INTENT_TABLE)} WHERE key=? ORDER BY id DESC LIMIT 1",
        (key,)
    ).fetchone()
    return dict(row) if row else None


def fetch_samples(con: sqlite3.Connection, symbol: str, side: str, setup: str) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    for source, spec in SOURCE_SPEC.items():
        table = spec["table"]
        if not exists(con, table):
            continue
        cols = set(columns(con, table))
        if not {"symbol", "side", "setup"}.issubset(cols):
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
            continue

        for rr in rows:
            row = dict(rr)
            if source == "outcome_clean" and not clean_outcome(row):
                continue
            if source in {"micro_canary_net", "position_closed", "trade_closed"} and not is_closed(row):
                continue
            if source == "shadow_resolved":
                st = str(row.get("outcome") or row.get("status") or "").upper()
                if st in {"", "OPEN", "PENDING", "ACTIVE"}:
                    continue

            value = None
            for c in spec["rcols"]:
                if c in row:
                    value = fnum(row.get(c), None)
                    if value is not None:
                        break
            if value is None:
                continue

            ew = fnum(row.get("evidence_weight"), None)
            base_w = float(spec["weight"])
            weight = base_w if ew is None else base_w * clamp(ew, 0.0, 2.0)

            events.append({
                "source": source,
                "kind": spec["kind"],
                "r": float(value),
                "weight": float(weight),
                "ts": row_ts(row),
                "id": str(get_first(row, ["id", "case_id", "position_id"], "")),
            })
    events.sort(key=lambda e: e.get("ts") or "")
    return events


def weighted_mean(xs: List[float], ws: List[float]) -> Optional[float]:
    sw = sum(ws)
    if not xs or sw <= 0:
        return None
    return sum(x * w for x, w in zip(xs, ws)) / sw


def weighted_std(xs: List[float], ws: List[float], mean: Optional[float] = None) -> Optional[float]:
    if not xs:
        return None
    if mean is None:
        mean = weighted_mean(xs, ws)
    if mean is None:
        return None
    sw = sum(ws)
    if sw <= 0:
        return None
    return math.sqrt(max(0.0, sum(w * (x - mean) ** 2 for x, w in zip(xs, ws)) / sw))


def effective_n(ws: List[float]) -> float:
    sw = sum(ws)
    sw2 = sum(w * w for w in ws)
    return 0.0 if sw2 <= 0 else (sw * sw) / sw2


def weighted_percentile(xs: List[float], ws: List[float], p: float) -> Optional[float]:
    if not xs:
        return None
    pairs = sorted(zip(xs, ws), key=lambda z: z[0])
    total = sum(max(0.0, w) for _, w in pairs)
    if total <= 0:
        return sorted(xs)[int(clamp(p) * (len(xs)-1))]
    threshold = p * total
    acc = 0.0
    for x, w in pairs:
        acc += max(0.0, w)
        if acc >= threshold:
            return x
    return pairs[-1][0]


def cvar_left(xs: List[float], ws: List[float], alpha: float = 0.05) -> Optional[float]:
    q = weighted_percentile(xs, ws, alpha)
    if q is None:
        return None
    tail = [(x, w) for x, w in zip(xs, ws) if x <= q]
    sw = sum(w for _, w in tail)
    if sw <= 0:
        return q
    return sum(x*w for x, w in tail) / sw


def profit_factor(xs: List[float], ws: List[float]) -> Optional[float]:
    gp = sum(x * w for x, w in zip(xs, ws) if x > 0)
    gl = abs(sum(x * w for x, w in zip(xs, ws) if x < 0))
    return None if gl <= 0 else gp / gl


def winrate(xs: List[float], ws: List[float]) -> Optional[float]:
    sw = sum(ws)
    if sw <= 0:
        return None
    return sum(w for x, w in zip(xs, ws) if x > 0) / sw


def max_drawdown(xs: List[float], ws: List[float]) -> float:
    eq = 0.0
    peak = 0.0
    mdd = 0.0
    for x, w in zip(xs, ws):
        eq += x * w
        peak = max(peak, eq)
        mdd = max(mdd, peak - eq)
    return mdd


def winsorized_values(xs: List[float], ws: List[float], lo: float = 0.05, hi: float = 0.95) -> List[float]:
    if not xs:
        return []
    qlo = weighted_percentile(xs, ws, lo)
    qhi = weighted_percentile(xs, ws, hi)
    if qlo is None or qhi is None:
        return xs[:]
    return [max(qlo, min(qhi, x)) for x in xs]


def trimmed_values(xs: List[float], trim: float = 0.10) -> List[float]:
    if not xs:
        return []
    ys = sorted(xs)
    k = int(len(ys) * trim)
    if len(ys) - 2 * k <= 0:
        return ys
    return ys[k:len(ys)-k]


def deterministic_rng(key: str) -> random.Random:
    seed = int(hashlib.sha256(key.encode()).hexdigest()[:12], 16)
    return random.Random(seed)


def bootstrap_lcb(xs: List[float], ws: List[float], key: str, n_boot: int = BOOTSTRAP_N) -> Optional[float]:
    if len(xs) < 12:
        return None
    sw = sum(ws)
    if sw <= 0:
        return None

    rnd = deterministic_rng("BOOT|" + key)
    probs = [w / sw for w in ws]
    cdf = []
    acc = 0.0
    for p in probs:
        acc += p
        cdf.append(acc)

    means = []
    m = len(xs)
    for _ in range(n_boot):
        s = 0.0
        for _j in range(m):
            u = rnd.random()
            idx = 0
            while idx < len(cdf) - 1 and cdf[idx] < u:
                idx += 1
            s += xs[idx]
        means.append(s / m)
    means.sort()
    return means[int(0.05 * (len(means) - 1))]


def fold_stability(events: List[Dict[str, Any]], folds: int = 4) -> Dict[str, Any]:
    vals = [fnum(e.get("r"), None) for e in sorted(events, key=lambda z: z.get("ts") or "")]
    vals = [v for v in vals if v is not None]
    if len(vals) < folds * 4:
        return {"folds": 0, "pass_rate": None, "min_mean": None, "dispersion": None, "score": 0.0}

    step = max(1, len(vals) // folds)
    means = []
    for i in range(folds):
        part = vals[i*step:] if i == folds - 1 else vals[i*step:(i+1)*step]
        if part:
            means.append(sum(part) / len(part))

    if not means:
        return {"folds": 0, "pass_rate": None, "min_mean": None, "dispersion": None, "score": 0.0}

    pass_rate = len([m for m in means if m > 0]) / len(means)
    dispersion = statistics.pstdev(means) if len(means) > 1 else 0.0
    center = abs(sum(means) / len(means))
    score = pass_rate * clamp(1.0 - dispersion / (center + 0.08))
    return {"folds": len(means), "pass_rate": pass_rate, "min_mean": min(means), "dispersion": dispersion, "score": score}


def recency_metrics(events: List[Dict[str, Any]], half_life_days: float = 21.0) -> Dict[str, Any]:
    xs, ws = [], []
    for e in events:
        r = fnum(e.get("r"), None)
        if r is None:
            continue
        age_m = age_minutes(e.get("ts"))
        decay = 0.40 if age_m is None else 0.5 ** ((age_m / 1440.0) / half_life_days)
        xs.append(r)
        ws.append(decay * float(e.get("weight") or 1.0))
    mean = weighted_mean(xs, ws)
    return {"mean_r": mean, "weight": sum(ws), "score": clamp(((mean or 0.0) + 0.04) / 0.12)}


def posterior_metrics(xs: List[float], ws: List[float], key: str, family_size: int) -> Dict[str, Any]:
    mean = weighted_mean(xs, ws)
    std = weighted_std(xs, ws, mean) or DEFAULT_STD_R
    effn = effective_n(ws)

    if mean is None or effn <= 0:
        return {
            "mean_r": None,
            "std_r": None,
            "eff_n": 0.0,
            "posterior_mean_r": None,
            "posterior_std_r": None,
            "posterior_lcb_r": None,
            "bootstrap_lcb_r": None,
            "deflated_lcb_r": None,
            "institutional_lcb_r": None,
            "prob_edge_gt_zero": None,
            "prob_edge_gt_target": None,
            "sprt_log_lr": None,
        }

    raw_n = effn
    post_n = raw_n + PRIOR_N
    post_mean = (mean * raw_n + PRIOR_MEAN_R * PRIOR_N) / post_n
    post_std = std
    se = post_std / math.sqrt(max(1.0, raw_n))
    post_lcb = post_mean - 1.65 * se

    multiple_test_haircut = post_std * math.sqrt(2.0 * math.log(max(2, family_size))) / math.sqrt(max(1.0, raw_n))
    def_lcb = post_mean - 1.65 * se - multiple_test_haircut

    boot = bootstrap_lcb(xs, ws, key)

    lcb_stack = [v for v in (post_lcb, def_lcb, boot) if v is not None]
    institutional_lcb = min(lcb_stack) if lcb_stack else None

    prob0 = normal_cdf(post_mean / se) if se > 0 else None
    target = 0.015
    prob_target = normal_cdf((post_mean - target) / se) if se > 0 else None

    mu1 = 0.020
    mu0 = 0.0
    var = max((std * std) / max(1.0, raw_n), 1e-9)
    ll_h1 = -((mean - mu1) ** 2) / (2.0 * var)
    ll_h0 = -((mean - mu0) ** 2) / (2.0 * var)
    sprt = ll_h1 - ll_h0

    return {
        "mean_r": mean,
        "std_r": std,
        "eff_n": effn,
        "posterior_mean_r": post_mean,
        "posterior_std_r": post_std,
        "posterior_lcb_r": post_lcb,
        "bootstrap_lcb_r": boot,
        "deflated_lcb_r": def_lcb,
        "institutional_lcb_r": institutional_lcb,
        "prob_edge_gt_zero": prob0,
        "prob_edge_gt_target": prob_target,
        "sprt_log_lr": sprt,
    }


def entropy_binary(p: Optional[float]) -> float:
    if p is None:
        return 1.0
    p = clamp(p, 1e-6, 1.0 - 1e-6)
    return -(p * math.log(p, 2) + (1 - p) * math.log(1 - p, 2))


def expected_info_value(prob_edge: Optional[float], inst_lcb: Optional[float], clean_n: int, live_n: int, robust_mean: Optional[float]) -> float:
    p = prob_edge if prob_edge is not None else 0.50
    entropy = entropy_binary(p)
    sample_need = clamp((250.0 - clean_n) / 250.0)
    live_need = clamp((20.0 - live_n) / 20.0)
    upside = clamp(((robust_mean or 0.0) + 0.01) / 0.10)
    lcb_gap = clamp((0.0 - (inst_lcb or -0.50)) / 0.50)
    return round(100.0 * entropy * (0.35 * sample_need + 0.45 * live_need + 0.20 * lcb_gap) * upside, 4)


def source_counts(events: List[Dict[str, Any]]) -> Dict[str, int]:
    out = defaultdict(int)
    for e in events:
        out[e.get("source") or "unknown"] += 1
    return dict(out)


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
    return {"red": [str(x) for x in red], "yellow": [str(x) for x in yellow], "green": [str(x) for x in green]}


def validate_quant_contract(qrow: Dict[str, Any], con: sqlite3.Connection, family_size: int) -> Dict[str, Any]:
    qpayload = safe_json(qrow.get("payload"), {})
    contract = safe_json(qrow.get("contract_json"), {})
    key = str(qrow.get("key") or qpayload.get("key") or "")
    symbol = str(qrow.get("symbol") or qpayload.get("symbol") or "").upper()
    side = str(qrow.get("side") or qpayload.get("side") or "").upper()
    setup = str(qrow.get("setup") or qpayload.get("setup") or "")

    brain = latest_row_by_key(con, BRAIN_TABLE, key)
    promo = latest_row_by_key(con, PROMO_TABLE, key)
    brain_payload = safe_json((brain or {}).get("payload"), {})
    promo_payload = safe_json((promo or {}).get("payload"), {})
    flags = parse_flags(brain or {}, brain_payload)

    events = fetch_samples(con, symbol, side, setup)
    live_events = [e for e in events if e.get("kind") == "live"]
    shadow_events = [e for e in events if e.get("kind") == "shadow"]

    xs = [float(e["r"]) for e in events]
    ws = [float(e.get("weight") or 1.0) for e in events]
    live_xs = [float(e["r"]) for e in live_events]
    live_ws = [float(e.get("weight") or 1.0) for e in live_events]

    winsor_xs = winsorized_values(xs, ws)
    trimmed_xs = trimmed_values(xs, 0.10)

    post = posterior_metrics(winsor_xs, ws[:len(winsor_xs)], key, family_size) if winsor_xs else posterior_metrics(xs, ws, key, family_size)
    raw_post = posterior_metrics(xs, ws, key + "|RAW", family_size)

    mean_candidates = [v for v in (
        post.get("posterior_mean_r"),
        weighted_mean(winsor_xs, ws[:len(winsor_xs)]) if winsor_xs else None,
        sum(trimmed_xs) / len(trimmed_xs) if trimmed_xs else None,
        fnum(brain_payload.get("robust_mean_r"), fnum((brain or {}).get("robust_mean_r"), None)),
    ) if v is not None]
    robust_mean = sum(mean_candidates) / len(mean_candidates) if mean_candidates else None

    inst_lcb_candidates = [v for v in (
        post.get("institutional_lcb_r"),
        raw_post.get("institutional_lcb_r"),
        fnum(brain_payload.get("institutional_lcb_r"), fnum((brain or {}).get("institutional_lcb_r"), None)),
    ) if v is not None]
    institutional_lcb = min(inst_lcb_candidates) if inst_lcb_candidates else None

    pf = profit_factor(xs, ws)
    live_pf = profit_factor(live_xs, live_ws)
    wr = winrate(xs, ws)
    cvar5 = cvar_left(xs, ws, 0.05)
    cvar10 = cvar_left(xs, ws, 0.10)
    dd = max_drawdown(xs, ws) if xs else None
    worst = min(xs) if xs else None
    fold = fold_stability(events)
    recency = recency_metrics(events)
    src_counts = source_counts(events)
    evsi = expected_info_value(post.get("prob_edge_gt_zero"), institutional_lcb, len(events), len(live_events), robust_mean)

    reasons: List[str] = []
    hard_fail = False

    q_age_h = age_hours(qrow.get("ts"))
    brain_age_m = age_minutes((brain or {}).get("ts"))

    if qrow.get("queue_state") != "MANUAL_REVIEW_REQUIRED":
        hard_fail = True
        reasons.append("QUEUE_NOT_MANUAL_REVIEW_REQUIRED")
    if qrow.get("requested_mode") != "PAPER_MICRO_CANARY_ONLY":
        hard_fail = True
        reasons.append("QUEUE_MODE_NOT_PAPER_MICRO_CANARY")
    if qrow.get("execution_permission") != "NO_AUTO_EXECUTION":
        hard_fail = True
        reasons.append("UNSAFE_QUEUE_EXECUTION_PERMISSION")
    if q_age_h is None or q_age_h > CONTRACT_TTL_HOURS:
        hard_fail = True
        reasons.append("CONTRACT_EXPIRED")
    if brain_age_m is None or brain_age_m > BRAIN_STALE_MINUTES:
        hard_fail = True
        reasons.append("BRAIN_STALE")
    if not brain:
        hard_fail = True
        reasons.append("LATEST_BRAIN_MISSING")
    if not promo:
        hard_fail = True
        reasons.append("LATEST_PROMOTION_MISSING")

    promo_action = promo_payload.get("action") or (promo or {}).get("action")
    promo_queue = promo_payload.get("queue_state") or (promo or {}).get("queue_state")
    if promo_action != "REVIEW_MICRO_CANARY_CONTRACT":
        hard_fail = True
        reasons.append("PROMOTION_NOT_REVIEW_CONTRACT_NOW")
    if promo_queue != "MANUAL_REVIEW_REQUIRED":
        hard_fail = True
        reasons.append("PROMOTION_QUEUE_NOT_MANUAL_REVIEW_NOW")

    fatal = sorted(list(set(flags["red"]) & FATAL_FLAGS))
    if fatal:
        hard_fail = True
        reasons.append("BRAIN_FATAL_FLAGS:" + ",".join(fatal))
    if promo_payload.get("hard_hits"):
        hard_fail = True
        reasons.append("PROMOTION_HARD_HITS_PRESENT")

    size = fnum(qrow.get("requested_size_mult"), 0.0) or 0.0
    if size < MIN_SIZE_MULT or size > MAX_SIZE_MULT:
        hard_fail = True
        reasons.append("SIZE_OUT_OF_BOUNDS")

    open_g = open_canaries_global(con)
    open_k = open_canaries_key(con, symbol, side, setup)
    daily_n = daily_intent_count(con)
    recent = recent_intent_for_key(con, key)
    recent_age_h = age_hours((recent or {}).get("ts")) if recent else None

    if open_g >= MAX_OPEN_CANARIES_GLOBAL:
        hard_fail = True
        reasons.append("GLOBAL_CANARY_CAP")
    if open_k >= MAX_OPEN_CANARIES_PER_KEY:
        hard_fail = True
        reasons.append("KEY_CANARY_CAP")
    if daily_n >= MAX_DAILY_INTENTS:
        hard_fail = True
        reasons.append("DAILY_INTENT_CAP")
    if recent_age_h is not None and recent_age_h < COOLDOWN_HOURS_AFTER_INTENT:
        hard_fail = True
        reasons.append("KEY_COOLDOWN_ACTIVE")

    clean_n = len(events)
    live_n = len(live_events)
    shadow_n = len(shadow_events)
    effn = post.get("eff_n") or 0.0

    if clean_n < MIN_CLEAN_N_REVIEW:
        hard_fail = True
        reasons.append("CLEAN_SAMPLE_TOO_LOW")
    if shadow_n < MIN_SHADOW_N_REVIEW:
        hard_fail = True
        reasons.append("SHADOW_SAMPLE_TOO_LOW")
    if robust_mean is None or robust_mean <= 0:
        hard_fail = True
        reasons.append("ROBUST_MEAN_NOT_POSITIVE")
    if institutional_lcb is None or institutional_lcb <= -0.60:
        hard_fail = True
        reasons.append("INSTITUTIONAL_LCB_DISASTROUS")
    if pf is None or pf < 0.45:
        hard_fail = True
        reasons.append("PF_TOO_LOW_FOR_QUANT_CANARY")
    if post.get("prob_edge_gt_zero") is None or post.get("prob_edge_gt_zero") < 0.50:
        hard_fail = True
        reasons.append("PROB_EDGE_BELOW_50")
    if cvar5 is not None and cvar5 <= -1.80:
        hard_fail = True
        reasons.append("CVAR5_TOO_NEGATIVE")
    if dd is not None and dd >= 6.0:
        hard_fail = True
        reasons.append("DRAWDOWN_TOO_HIGH")
    if worst is not None and worst <= -2.25:
        hard_fail = True
        reasons.append("WORST_R_TOO_NEGATIVE")

    quant_score = 0.0
    quant_score += 18.0 * clamp(((robust_mean or 0.0) + 0.005) / 0.085)
    quant_score += 18.0 * clamp(((institutional_lcb or -0.60) + 0.60) / 0.60)
    quant_score += 14.0 * clamp(((post.get("prob_edge_gt_zero") or 0.50) - 0.50) / 0.35)
    quant_score += 10.0 * clamp(((post.get("prob_edge_gt_target") or 0.50) - 0.45) / 0.35)
    quant_score += 10.0 * clamp(((pf or 0.50) - 0.45) / 0.90)
    quant_score += 8.0 * clamp(fold["score"])
    quant_score += 7.0 * clamp(recency["score"])
    quant_score += 6.0 * clamp(effn / 250.0)
    quant_score += 5.0 * clamp(live_n / 20.0)
    quant_score += 4.0 * clamp(evsi / 60.0)
    quant_score = round(clamp(quant_score, 0.0, 100.0), 4)

    if hard_fail:
        lifecycle_state = "QUANT_REJECTED"
        validation_state = "FAILED_MAX_QUANT_REVALIDATION"
        can_emit = 0
    else:
        if quant_score >= 68.0 and institutional_lcb is not None and institutional_lcb > -0.25:
            lifecycle_state = "QUANT_VALIDATED_HIGH_PRIORITY"
        else:
            lifecycle_state = "QUANT_VALIDATED_REVIEW"
        validation_state = "VALID_MAX_QUANT_MANUAL_PAPER_CANARY_CONTRACT"
        can_emit = 1
        reasons.append("MAX_QUANT_VALIDATED_FOR_MANUAL_APPROVAL")

    variance = (post.get("std_r") or DEFAULT_STD_R) ** 2
    raw_kelly = max(0.0, (robust_mean or 0.0) / variance) if variance > 0 else 0.0
    lcb_cap = 0.35 if institutional_lcb is not None and institutional_lcb > -0.25 else 0.12
    cvar_cap = 0.12 if cvar5 is not None and cvar5 < -1.20 else 0.35
    live_cap = 0.12 if live_n < 3 else 0.22 if live_n < 10 else 0.45
    quant_size = min(raw_kelly * 0.16 * clamp(quant_score / 100.0), lcb_cap, cvar_cap, live_cap, MAX_SIZE_MULT, size)
    if can_emit and quant_size < MIN_SIZE_MULT:
        quant_size = MIN_SIZE_MULT
    if not can_emit:
        quant_size = 0.0

    contract_hash = sha256_obj({
        "version": VERSION,
        "queue_id": qrow.get("id"),
        "key": key,
        "symbol": symbol,
        "side": side,
        "setup": setup,
        "requested_size_mult": quant_size,
        "contract_json": contract,
        "brain_ts": (brain or {}).get("ts"),
        "promo_ts": (promo or {}).get("ts"),
        "quant_score": quant_score,
    })

    return {
        "queue_id": qrow.get("id"),
        "key": key,
        "symbol": symbol,
        "side": side,
        "setup": setup,
        "lifecycle_state": lifecycle_state,
        "validation_state": validation_state,
        "can_emit_intent": can_emit,
        "quant_score": quant_score,
        "requested_size_mult_original": size,
        "requested_size_mult_quant": round(quant_size, 6),
        "contract_hash": contract_hash,
        "reasons": reasons,
        "brain_ts": (brain or {}).get("ts"),
        "promotion_ts": (promo or {}).get("ts"),
        "contract_age_hours": q_age_h,
        "brain_age_minutes": brain_age_m,
        "open_canaries_global": open_g,
        "open_canaries_key": open_k,
        "daily_intents": daily_n,
        "recent_key_intent_age_hours": recent_age_h,
        "metrics": {
            "clean_n": clean_n,
            "live_n": live_n,
            "shadow_n": shadow_n,
            "effective_n": effn,
            "mean_r": post.get("mean_r"),
            "winsor_posterior_mean_r": post.get("posterior_mean_r"),
            "raw_posterior_mean_r": raw_post.get("posterior_mean_r"),
            "robust_mean_r": robust_mean,
            "posterior_lcb_r": post.get("posterior_lcb_r"),
            "bootstrap_lcb_r": post.get("bootstrap_lcb_r"),
            "deflated_lcb_r": post.get("deflated_lcb_r"),
            "institutional_lcb_r": institutional_lcb,
            "prob_edge_gt_zero": post.get("prob_edge_gt_zero"),
            "prob_edge_gt_target": post.get("prob_edge_gt_target"),
            "sprt_log_lr": post.get("sprt_log_lr"),
            "profit_factor": pf,
            "live_profit_factor": live_pf,
            "winrate": wr,
            "cvar_5_r": cvar5,
            "cvar_10_r": cvar10,
            "max_drawdown_r": dd,
            "worst_r": worst,
            "fold_stability": fold,
            "recency": recency,
            "evsi_score": evsi,
            "source_counts": src_counts,
        },
        "contract_json": contract,
        "queue_payload": qpayload,
        "brain_payload": brain_payload,
        "promotion_payload": promo_payload,
    }


def create_tables(con: sqlite3.Connection) -> None:
    con.execute(f"""
        CREATE TABLE IF NOT EXISTS {qid(GOV_TABLE)} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            version TEXT NOT NULL,
            queue_id INTEGER,
            key TEXT,
            symbol TEXT,
            side TEXT,
            setup TEXT,
            lifecycle_state TEXT,
            validation_state TEXT,
            quant_score REAL,
            requested_size_mult_quant REAL,
            contract_hash TEXT,
            brain_ts TEXT,
            promotion_ts TEXT,
            reasons TEXT,
            payload TEXT
        )
    """)
    con.execute(f"""
        CREATE TABLE IF NOT EXISTS {qid(INTENT_TABLE)} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            version TEXT NOT NULL,
            intent_hash TEXT NOT NULL UNIQUE,
            queue_id INTEGER,
            key TEXT NOT NULL,
            symbol TEXT,
            side TEXT,
            setup TEXT,
            intent_state TEXT NOT NULL,
            requested_mode TEXT NOT NULL,
            requested_size_mult REAL,
            execution_permission TEXT NOT NULL,
            adapter_status TEXT NOT NULL,
            contract_hash TEXT,
            contract_json TEXT,
            payload TEXT
        )
    """)
    con.execute(f"""
        CREATE TABLE IF NOT EXISTS {qid(HEALTH_TABLE)} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            version TEXT NOT NULL,
            quick_check TEXT,
            queue_candidates INTEGER,
            valid_contracts INTEGER,
            rejected_contracts INTEGER,
            emitted_intents INTEGER,
            state_counts TEXT,
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
            queue_id INTEGER,
            payload TEXT
        )
    """)
    con.execute(f"CREATE INDEX IF NOT EXISTS idx_gov_v17_7_2_key_ts ON {qid(GOV_TABLE)}(key, ts)")
    con.execute(f"CREATE INDEX IF NOT EXISTS idx_gov_v17_7_2_state_ts ON {qid(GOV_TABLE)}(lifecycle_state, ts)")
    con.execute(f"CREATE INDEX IF NOT EXISTS idx_intent_v17_7_2_state ON {qid(INTENT_TABLE)}(intent_state, ts)")
    con.execute(f"CREATE INDEX IF NOT EXISTS idx_intent_v17_7_2_key ON {qid(INTENT_TABLE)}(key, ts)")


def build_report(approve_id: Optional[int] = None, emit_intent: bool = False) -> Dict[str, Any]:
    con = connect_ro()
    quick = con.execute("PRAGMA quick_check").fetchone()[0]
    queue = load_review_queue(con)
    family_size = max(52, len(queue))
    validations = [validate_quant_contract(q, con, family_size) for q in queue]
    con.close()

    emitted: List[Dict[str, Any]] = []

    if approve_id is not None and emit_intent:
        selected = [v for v in validations if int(v["queue_id"]) == int(approve_id)]
        if not selected:
            raise RuntimeError(f"approve_id not found: {approve_id}")
        v = selected[0]
        if not v["can_emit_intent"]:
            raise RuntimeError("MAX_QUANT_CONTRACT_REJECTED: " + ",".join(v["reasons"]))

        conw = connect_rw()
        create_tables(conw)
        ts = utc_now()
        h = sha256_obj({
            "version": VERSION,
            "day": utc_day(),
            "queue_id": v["queue_id"],
            "key": v["key"],
            "contract_hash": v["contract_hash"],
            "size": v["requested_size_mult_quant"],
        })

        conw.execute(f"""
            INSERT OR IGNORE INTO {qid(INTENT_TABLE)}
            (ts, version, intent_hash, queue_id, key, symbol, side, setup,
             intent_state, requested_mode, requested_size_mult, execution_permission,
             adapter_status, contract_hash, contract_json, payload)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            ts, VERSION, h, v["queue_id"], v["key"], v["symbol"], v["side"], v["setup"],
            "MAX_QUANT_MANUAL_APPROVED_PENDING_PAPER_ADAPTER",
            "PAPER_MICRO_CANARY_ONLY",
            v["requested_size_mult_quant"],
            "PAPER_ONLY_NO_REAL_EXECUTION",
            "PENDING_ADAPTER_BINDING",
            v["contract_hash"],
            json.dumps(v["contract_json"], sort_keys=True),
            json.dumps(v, sort_keys=True),
        ))

        conw.execute(f"""
            INSERT INTO {qid(AUDIT_TABLE)}
            (ts, version, event_type, key, queue_id, payload)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            ts, VERSION, "MAX_QUANT_MANUAL_INTENT_EMITTED", v["key"], v["queue_id"],
            json.dumps({"intent_hash": h, "validation": v}, sort_keys=True),
        ))
        conw.commit()
        conw.close()
        emitted.append(v)

    counts = defaultdict(int)
    for v in validations:
        counts[v["lifecycle_state"]] += 1

    return {
        "version": VERSION,
        "generated_utc": utc_now(),
        "quick_check": quick,
        "queue_candidates": len(queue),
        "valid_contracts": len([v for v in validations if v["can_emit_intent"]]),
        "rejected_contracts": len([v for v in validations if not v["can_emit_intent"]]),
        "emitted_intents": len(emitted),
        "state_counts": dict(counts),
        "validations": validations,
        "emitted": emitted,
    }


def write_outputs(report: Dict[str, Any], write_db: bool) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "max_quant_canary_governance_latest.json").write_text(json.dumps(report, indent=2, sort_keys=True))

    lines = []
    lines.append("# V17.7.2 MAX Quant Canary Governance Gate")
    lines.append("")
    lines.append(f"- UTC: `{report['generated_utc']}`")
    lines.append(f"- DB quick_check: `{report['quick_check']}`")
    lines.append(f"- Queue candidates: `{report['queue_candidates']}`")
    lines.append(f"- Valid contracts: `{report['valid_contracts']}`")
    lines.append(f"- Rejected contracts: `{report['rejected_contracts']}`")
    lines.append(f"- Emitted intents: `{report['emitted_intents']}`")
    lines.append(f"- State counts: `{report['state_counts']}`")
    lines.append("")
    lines.append("## MAX quant revalidation")
    lines.append("| queue_id | state | emit | q_score | symbol | side | setup | q_size | mean | inst_LCB | prob0 | probT | PF | CVaR5 | DD | clean/live/shadow | EVSI | reasons |")
    lines.append("|---:|---|---:|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|---|")
    for v in report["validations"]:
        m = v["metrics"]
        cls = f"{m.get('clean_n')}/{m.get('live_n')}/{m.get('shadow_n')}"
        lines.append(
            f"| {v['queue_id']} | {v['lifecycle_state']} / {v['validation_state']} | "
            f"{v['can_emit_intent']} | {fmt(v['quant_score'])} | {v['symbol']} | {v['side']} | {v['setup'][:32]} | "
            f"{fmt(v['requested_size_mult_quant'])} | {fmt(m.get('robust_mean_r'))} | {fmt(m.get('institutional_lcb_r'))} | "
            f"{fmt(m.get('prob_edge_gt_zero'))} | {fmt(m.get('prob_edge_gt_target'))} | {fmt(m.get('profit_factor'))} | "
            f"{fmt(m.get('cvar_5_r'))} | {fmt(m.get('max_drawdown_r'))} | {cls} | {fmt(m.get('evsi_score'))} | "
            f"{','.join(v['reasons'])[:260]} |"
        )
    lines.append("")
    lines.append("## Emitted intents")
    if not report["emitted"]:
        lines.append("- none")
    else:
        for v in report["emitted"]:
            lines.append(f"- queue_id `{v['queue_id']}` `{v['symbol']} {v['side']} {v['setup']}` size={v['requested_size_mult_quant']} hash=`{v['contract_hash'][:16]}`")

    (OUT / "max_quant_canary_governance_summary.md").write_text("\n".join(lines))

    with (OUT / "max_quant_canary_governance_ledger.jsonl").open("a") as f:
        f.write(json.dumps({
            "ts": report["generated_utc"],
            "states": report["state_counts"],
            "valid": report["valid_contracts"],
            "emitted": report["emitted_intents"],
            "top": report["validations"][:10],
        }, sort_keys=True) + "\n")

    if not write_db:
        return

    con = connect_rw()
    create_tables(con)
    ts = report["generated_utc"]

    for v in report["validations"]:
        con.execute(f"""
            INSERT INTO {qid(GOV_TABLE)}
            (ts, version, queue_id, key, symbol, side, setup, lifecycle_state,
             validation_state, quant_score, requested_size_mult_quant, contract_hash,
             brain_ts, promotion_ts, reasons, payload)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            ts, VERSION, v["queue_id"], v["key"], v["symbol"], v["side"], v["setup"],
            v["lifecycle_state"], v["validation_state"], v["quant_score"],
            v["requested_size_mult_quant"], v["contract_hash"], v["brain_ts"],
            v["promotion_ts"], json.dumps(v["reasons"], sort_keys=True), json.dumps(v, sort_keys=True),
        ))

    con.execute(f"""
        INSERT INTO {qid(HEALTH_TABLE)}
        (ts, version, quick_check, queue_candidates, valid_contracts, rejected_contracts,
         emitted_intents, state_counts, payload)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        ts, VERSION, report["quick_check"], report["queue_candidates"], report["valid_contracts"],
        report["rejected_contracts"], report["emitted_intents"], json.dumps(report["state_counts"], sort_keys=True),
        json.dumps(report, sort_keys=True),
    ))

    con.commit()
    con.close()


def fmt(x: Any) -> str:
    v = fnum(x, None)
    return "-" if v is None else f"{v:.4f}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write-db", action="store_true")
    ap.add_argument("--approve-id", type=int)
    ap.add_argument("--emit-intent", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    report = build_report(approve_id=args.approve_id, emit_intent=args.emit_intent)
    write_outputs(report, write_db=args.write_db)

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print("===== V17.7.2 MAX QUANT CANARY GOVERNANCE GATE =====")
        print("quick_check:", report["quick_check"])
        print("queue_candidates:", report["queue_candidates"])
        print("valid_contracts:", report["valid_contracts"])
        print("rejected_contracts:", report["rejected_contracts"])
        print("emitted_intents:", report["emitted_intents"])
        print("state_counts:", report["state_counts"])
        for v in report["validations"]:
            m = v["metrics"]
            print(
                f"queue_id={v['queue_id']} state={v['lifecycle_state']} emit={v['can_emit_intent']} "
                f"qscore={fmt(v['quant_score'])} {v['symbol']} {v['side']} {v['setup']} "
                f"size={fmt(v['requested_size_mult_quant'])} mean={fmt(m.get('robust_mean_r'))} "
                f"lcb={fmt(m.get('institutional_lcb_r'))} prob0={fmt(m.get('prob_edge_gt_zero'))} "
                f"pf={fmt(m.get('profit_factor'))} cvar={fmt(m.get('cvar_5_r'))} "
                f"evsi={fmt(m.get('evsi_score'))} reasons={','.join(v['reasons'])[:220]}"
            )
    return 0 if report.get("quick_check") == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
