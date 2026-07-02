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
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

ROOT = Path.cwd()
DB = ROOT / "data" / "joanbot_v14.sqlite"
OUT = ROOT / "data" / "v17_5_1"
VERSION = "V17.5.1_SELF_CONTAINED_INSTITUTIONAL_QUANT_BRAIN"

TABLES = {
    "outcome": "outcome_provenance_v1",
    "canary": "paper_micro_canary_positions_v11",
    "positions": "positions",
    "trades": "trades",
    "shadow_results": "universal_shadow_results_v2",
    "tensor": "alpha_evidence_tensor_v5",
    "registry": "alpha_setup_registry_v16",
    "posterior": "alpha_bayesian_posterior_v5",
    "rollup": "alpha_research_rollup_v16",
    "promotion": "alpha_promotion_contract_v5",
    "final_gate": "alpha_final_gate_v16",
    "control": "institutional_control_plane_v11",
    "hygiene": "evidence_hygiene_summary_v1",
}

SOURCE_WEIGHT = {
    "outcome_clean": 1.00,
    "micro_canary_net": 0.88,
    "position_closed": 0.72,
    "trade_closed": 0.62,
    "shadow_resolved": 0.24,
}

PRIOR_N = 80.0
BOOTSTRAP_N = 400
TIME_COLS = ("closed_at", "resolved_at", "updated_at", "ts", "opened_at", "created_at", "last_managed_at")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def ts_string(row: Dict[str, Any]) -> str:
    best = None
    for c in TIME_COLS:
        d = parse_dt(row.get(c))
        if d and (best is None or d > best):
            best = d
    return best.isoformat() if best else ""


def ts_float(row: Dict[str, Any]) -> float:
    d = parse_dt(ts_string(row))
    return d.timestamp() if d else 0.0


def age_days(ts: str) -> Optional[float]:
    d = parse_dt(ts)
    if not d:
        return None
    return max(0.0, (datetime.now(timezone.utc) - d).total_seconds() / 86400.0)


def boolish(x: Any) -> Optional[bool]:
    if x is None:
        return None
    if isinstance(x, bool):
        return x
    if isinstance(x, (int, float)):
        return int(x) != 0
    s = str(x).strip().lower()
    if s in {"1", "true", "yes", "ok", "allow", "allowed"}:
        return True
    if s in {"0", "false", "no", "deny", "blocked", "reject", "rejected"}:
        return False
    return None


def connect_ro() -> sqlite3.Connection:
    con = sqlite3.connect("file:" + str(DB.resolve()) + "?mode=ro", uri=True, timeout=10)
    con.row_factory = sqlite3.Row
    return con


def connect_rw() -> sqlite3.Connection:
    con = sqlite3.connect(str(DB), timeout=20)
    con.row_factory = sqlite3.Row
    return con


def table_exists(con: sqlite3.Connection, table: str) -> bool:
    return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def load_rows(con: sqlite3.Connection, table: str) -> List[Dict[str, Any]]:
    if not table_exists(con, table):
        return []
    return [dict(r) for r in con.execute(f"SELECT * FROM {qid(table)}").fetchall()]


def first(row: Dict[str, Any], cols: Iterable[str], default: Any = None) -> Any:
    for c in cols:
        if c in row and row.get(c) not in (None, "", "None", "nan", "NaN"):
            return row.get(c)
    return default


def identity(row: Dict[str, Any]) -> Tuple[str, str, str, str, str]:
    symbol = str(first(row, ["symbol", "selected_symbol", "edge_symbol"], "") or "").upper()
    side = str(first(row, ["side", "selected_side", "edge_side"], "") or "").upper()
    setup = str(first(row, ["setup", "selected_setup", "edge_setup"], "") or "")
    profile = str(first(row, ["profile", "selected_profile", "edge_profile"], "") or "")
    horizon = str(first(row, ["horizon_min", "selected_horizon_min", "edge_horizon_min"], "") or "")
    return symbol, side, setup, profile, horizon


def base_key(symbol: str, side: str, setup: str) -> str:
    if symbol and side and setup:
        return f"{symbol.upper()}|{side.upper()}|{setup}"
    return ""


def row_key(row: Dict[str, Any]) -> str:
    symbol, side, setup, _, _ = identity(row)
    return base_key(symbol, side, setup)


def latest_by_key(rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        k = row_key(r)
        if not k:
            continue
        if k not in out or ts_float(r) >= ts_float(out[k]):
            out[k] = r
    return out


def build_position_map(datasets: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Tuple[str, str, str, str, str]]:
    mp: Dict[str, Tuple[str, str, str, str, str]] = {}
    for source in ("positions", "canary", "trades", "outcome"):
        for r in datasets.get(source, []):
            ident = identity(r)
            if not base_key(ident[0], ident[1], ident[2]):
                continue
            for c in ("position_id", "id", "case_id"):
                v = r.get(c)
                if v not in (None, ""):
                    mp[str(v)] = ident
    return mp


def fill_identity(row: Dict[str, Any], pos_map: Dict[str, Tuple[str, str, str, str, str]]) -> Tuple[str, str, str, str, str, bool]:
    ident = identity(row)
    if base_key(ident[0], ident[1], ident[2]):
        return (*ident, False)
    for c in ("position_id", "id", "case_id"):
        v = row.get(c)
        if v not in (None, "") and str(v) in pos_map:
            return (*pos_map[str(v)], False)
    return "", "", "", "", "", True


def r_value(row: Dict[str, Any], cols: Iterable[str]) -> Optional[float]:
    for c in cols:
        if c in row:
            v = fnum(row.get(c), None)
            if v is not None:
                return v
    return None


def is_closed(row: Dict[str, Any]) -> bool:
    st = str(row.get("status") or "").upper()
    if st in {"OPEN", "ACTIVE", "RUNNING", "PENDING"}:
        return False
    if row.get("closed_at") not in (None, ""):
        return True
    if st in {"CLOSED", "DONE", "RESOLVED", "EXITED", "WIN", "LOSS"}:
        return True
    return r_value(row, ["net_pnl_r", "pnl_r", "gross_pnl_r", "result_r"]) is not None


def is_clean_outcome(row: Dict[str, Any]) -> bool:
    b = boolish(row.get("clean_for_evidence"))
    if b is not None:
        return b
    return row.get("excluded_reason") in (None, "")


@dataclass
class EvidenceEvent:
    key: str
    symbol: str
    side: str
    setup: str
    profile: str
    horizon_min: str
    source: str
    source_table: str
    event_id: str
    position_id: str
    ts: str
    r: Optional[float]
    weight: float
    clean: bool
    excluded: bool
    orphan: bool
    cost_status: str
    reason: str


def make_event(
    row: Dict[str, Any],
    source: str,
    table: str,
    rcols: Iterable[str],
    pos_map: Dict[str, Tuple[str, str, str, str, str]],
    base_weight: float,
    cost_status: str,
) -> EvidenceEvent:
    symbol, side, setup, profile, horizon, orphan = fill_identity(row, pos_map)
    k = base_key(symbol, side, setup)
    if not k:
        orphan = True
        k = "ORPHAN|" + source + "|" + str(first(row, ["position_id", "case_id", "id"], ""))
    r = r_value(row, rcols)
    excluded = False
    reason = ""
    if source == "outcome_clean":
        excluded = not is_clean_outcome(row)
        reason = str(row.get("excluded_reason") or "")
    elif source in {"micro_canary_net", "position_closed", "trade_closed"}:
        excluded = not is_closed(row)
        reason = "NOT_CLOSED" if excluded else ""
    elif source == "shadow_resolved":
        st = str(row.get("outcome") or row.get("status") or "").upper()
        excluded = st in {"", "OPEN", "PENDING", "ACTIVE"}
        reason = "UNRESOLVED_SHADOW" if excluded else ""
    ew = fnum(row.get("evidence_weight"), None)
    weight = base_weight if ew is None else base_weight * clamp(ew, 0.0, 2.0)
    return EvidenceEvent(
        key=k,
        symbol=symbol,
        side=side,
        setup=setup,
        profile=profile,
        horizon_min=str(horizon or ""),
        source=source,
        source_table=table,
        event_id=str(first(row, ["id", "case_id"], "")),
        position_id=str(first(row, ["position_id", "id", "case_id"], "")),
        ts=ts_string(row),
        r=r,
        weight=round(weight, 6),
        clean=(not excluded and not orphan and r is not None),
        excluded=excluded,
        orphan=orphan,
        cost_status=cost_status,
        reason=reason,
    )


def collect_evidence(datasets: Dict[str, List[Dict[str, Any]]]) -> List[EvidenceEvent]:
    pos_map = build_position_map(datasets)
    specs = [
        ("outcome", "outcome_clean", ["pnl_r"], SOURCE_WEIGHT["outcome_clean"], "net_or_clean_outcome_no_extra_cost"),
        ("canary", "micro_canary_net", ["net_pnl_r", "pnl_r", "gross_pnl_r"], SOURCE_WEIGHT["micro_canary_net"], "net_preferred_no_extra_cost"),
        ("positions", "position_closed", ["net_pnl_r", "pnl_r"], SOURCE_WEIGHT["position_closed"], "realized_no_extra_cost"),
        ("trades", "trade_closed", ["net_pnl_r", "pnl_r"], SOURCE_WEIGHT["trade_closed"], "realized_no_extra_cost"),
        ("shadow_results", "shadow_resolved", ["result_r"], SOURCE_WEIGHT["shadow_resolved"], "shadow_discounted_no_extra_cost"),
    ]
    events: List[EvidenceEvent] = []
    for dataset, source, rcols, weight, cost_status in specs:
        table = TABLES[dataset]
        for row in datasets.get(dataset, []):
            events.append(make_event(row, source, table, rcols, pos_map, weight, cost_status))
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
    var = sum(w * ((x - mean) ** 2) for x, w in zip(xs, ws)) / sw
    return math.sqrt(max(0.0, var))


def effective_n(ws: List[float]) -> float:
    sw = sum(ws)
    sw2 = sum(w * w for w in ws)
    return 0.0 if sw2 <= 0 else (sw * sw) / sw2


def percentile(xs: List[float], p: float) -> Optional[float]:
    if not xs:
        return None
    ys = sorted(xs)
    if len(ys) == 1:
        return ys[0]
    k = (len(ys) - 1) * p
    lo = int(math.floor(k))
    hi = int(math.ceil(k))
    if lo == hi:
        return ys[lo]
    return ys[lo] * (hi - k) + ys[hi] * (k - lo)


def cvar_left(xs: List[float], alpha: float = 0.05) -> Optional[float]:
    q = percentile(xs, alpha)
    if q is None:
        return None
    tail = [x for x in xs if x <= q]
    return sum(tail) / len(tail) if tail else q


def profit_factor(xs: List[float], ws: List[float]) -> Optional[float]:
    gp = sum(x * w for x, w in zip(xs, ws) if x > 0)
    gl = abs(sum(x * w for x, w in zip(xs, ws) if x < 0))
    return None if gl <= 0 else gp / gl


def max_drawdown(xs: List[float], ws: List[float]) -> float:
    eq = 0.0
    peak = 0.0
    mdd = 0.0
    for x, w in zip(xs, ws):
        eq += x * w
        peak = max(peak, eq)
        mdd = max(mdd, peak - eq)
    return mdd


def deterministic_seed(key: str) -> int:
    return int(hashlib.sha256(key.encode()).hexdigest()[:12], 16)


def bootstrap_lcb(xs: List[float], ws: List[float], key: str, n_boot: int = BOOTSTRAP_N) -> Optional[float]:
    if len(xs) < 8:
        return None
    sw = sum(ws)
    if sw <= 0:
        return None
    rnd = random.Random(deterministic_seed(key))
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
    return percentile(means, 0.05)


def deflated_lcb(mean: Optional[float], std: Optional[float], effn: float, family_size: int) -> Optional[float]:
    if mean is None:
        return None
    std = std if std is not None and std > 0 else 0.35
    effn = max(1.0, effn)
    se = std / math.sqrt(effn)
    family_haircut = std * math.sqrt(2.0 * math.log(max(2, family_size))) / math.sqrt(effn)
    return mean - 1.65 * se - family_haircut


def fold_stability(events: List[Dict[str, Any]], folds: int = 4) -> Dict[str, Any]:
    clean = sorted(events, key=lambda e: e.get("ts") or "")
    xs = [fnum(e.get("r"), None) for e in clean]
    xs = [x for x in xs if x is not None]
    if len(xs) < folds * 4:
        return {"folds": 0, "fold_pass_rate": None, "fold_min_mean": None, "fold_dispersion": None, "stability_score": 0.0}
    step = max(1, len(xs) // folds)
    chunks = []
    for j in range(folds):
        part = xs[j * step:] if j == folds - 1 else xs[j * step:(j + 1) * step]
        if part:
            chunks.append(sum(part) / len(part))
    if not chunks:
        return {"folds": 0, "fold_pass_rate": None, "fold_min_mean": None, "fold_dispersion": None, "stability_score": 0.0}
    pass_rate = len([x for x in chunks if x > 0]) / len(chunks)
    dispersion = statistics.pstdev(chunks) if len(chunks) > 1 else 0.0
    mean_abs = abs(sum(chunks) / len(chunks))
    stability = pass_rate * clamp(1.0 - dispersion / (mean_abs + 0.08))
    return {"folds": len(chunks), "fold_pass_rate": pass_rate, "fold_min_mean": min(chunks), "fold_dispersion": dispersion, "stability_score": stability}


def recency_score(events: List[Dict[str, Any]], half_life_days: float = 21.0) -> Dict[str, Any]:
    xs, ws = [], []
    for e in events:
        r = fnum(e.get("r"), None)
        if r is None:
            continue
        age = age_days(e.get("ts") or "")
        w = 0.40 if age is None else 0.5 ** (age / half_life_days)
        xs.append(r)
        ws.append(w)
    mean = weighted_mean(xs, ws)
    return {"recency_mean_r": mean, "recency_weight": sum(ws), "recency_score": clamp(((mean or 0.0) + 0.04) / 0.12)}


def metric(row: Optional[Dict[str, Any]], names: Iterable[str]) -> Optional[float]:
    if not row:
        return None
    return r_value(row, names)


def quality_from_rows(*rows: Optional[Dict[str, Any]]) -> Optional[float]:
    qcols = [
        "sample_quality", "validation_quality", "fold_quality", "path_quality", "stability_quality",
        "context_quality", "tensor_quality", "posterior_quality", "decay_quality", "tail_quality",
        "cpcv_score", "feature_score", "risk_score", "robustness_score",
    ]
    vals = []
    for row in rows:
        if not row:
            continue
        for c in qcols:
            v = fnum(row.get(c), None)
            if v is not None:
                vals.append(clamp(v, 0.0, 1.0))
    return sum(vals) / len(vals) if vals else None


def hard_veto_from_rows(*rows: Optional[Dict[str, Any]]) -> str:
    chunks = []
    for row in rows:
        if not row:
            continue
        for c in ("hard_vetoes", "reason", "reasons", "invalidations", "payload", "control_contract_json"):
            v = row.get(c)
            if not v:
                continue
            s = str(v)
            up = s.upper()
            if any(tok in up for tok in ("HARD", "VETO", "TOXIC", "REJECT", "INVALID", "BLOCK")):
                chunks.append(s[:400])
    return " | ".join(chunks)


def alpha_prior(k: str, maps: Dict[str, Dict[str, Dict[str, Any]]]) -> Dict[str, Any]:
    tensor = maps["tensor"].get(k)
    registry = maps["registry"].get(k)
    posterior = maps["posterior"].get(k)
    rollup = maps["rollup"].get(k)
    promotion = maps["promotion"].get(k)
    gate = maps["final_gate"].get(k)
    hygiene = maps["hygiene"].get(k)

    exp_vals = [
        metric(tensor, ["shrunk_expectancy_r", "expectancy_r", "validation_exp_r", "recent_exp_r"]),
        metric(registry, ["expectancy_r"]),
        metric(rollup, ["expectancy_r"]),
        metric(posterior, ["posterior_mean_r", "tensor_mean_r"]),
        metric(promotion, ["posterior_mean_r", "expectancy_r"]),
    ]
    exp_vals = [v for v in exp_vals if v is not None]
    raw_exp = sum(exp_vals) / len(exp_vals) if exp_vals else None

    lcb_vals = [
        metric(posterior, ["posterior_lcb_r", "tensor_lcb_r"]),
        metric(registry, ["posterior_lcb_r"]),
        metric(rollup, ["posterior_lcb_r"]),
        metric(promotion, ["posterior_lcb_r", "edge_lcb_r"]),
        metric(tensor, ["lcb_expectancy_r", "posterior_lcb_r"]),
    ]
    lcb_vals = [v for v in lcb_vals if v is not None]
    raw_lcb = sum(lcb_vals) / len(lcb_vals) if lcb_vals else None

    pf_vals = [
        metric(tensor, ["profit_factor_capped", "profit_factor"]),
        metric(registry, ["profit_factor"]),
        metric(rollup, ["profit_factor"]),
        metric(promotion, ["profit_factor", "edge_pf_cap"]),
    ]
    pf_vals = [v for v in pf_vals if v is not None]
    pf = sum(pf_vals) / len(pf_vals) if pf_vals else None

    prob_vals = [
        metric(posterior, ["prob_edge_gt_zero", "prob_edge_gt_min"]),
        metric(promotion, ["prob_edge_gt_zero", "prob_edge_gt_min"]),
        metric(rollup, ["prob_edge_gt_zero"]),
    ]
    prob_vals = [v for v in prob_vals if v is not None]
    prob = sum(prob_vals) / len(prob_vals) if prob_vals else None

    alpha_n = max(
        inum((tensor or {}).get("n")),
        inum((registry or {}).get("sample_n")),
        inum((posterior or {}).get("n")),
        inum((rollup or {}).get("sample_n")),
        inum((promotion or {}).get("sample_n")),
    )

    alpha_cost_haircut = 0.014
    return {
        "alpha_raw_expectancy_r": raw_exp,
        "alpha_net_expectancy_r": raw_exp - alpha_cost_haircut if raw_exp is not None else None,
        "alpha_net_lcb_r": raw_lcb - alpha_cost_haircut if raw_lcb is not None else None,
        "alpha_profit_factor": pf,
        "alpha_prob_edge_gt_zero": prob,
        "alpha_sample_n": alpha_n,
        "alpha_quality": quality_from_rows(tensor, registry, posterior, rollup, promotion, hygiene),
        "final_gate_allow_trade": boolish((gate or {}).get("allow_trade")),
        "alpha_hard_veto": hard_veto_from_rows(tensor, registry, posterior, rollup, promotion, gate),
        "sources": {
            "tensor": bool(tensor),
            "registry": bool(registry),
            "posterior": bool(posterior),
            "rollup": bool(rollup),
            "promotion": bool(promotion),
            "final_gate": bool(gate),
            "hygiene": bool(hygiene),
        },
    }


def posterior_blend(emp_mean: Optional[float], emp_std: Optional[float], effn: float, alpha_mean: Optional[float], alpha_quality: Optional[float]) -> Dict[str, Any]:
    emp_std = emp_std if emp_std is not None and emp_std > 0 else 0.35
    alpha_q = clamp(alpha_quality if alpha_quality is not None else 0.35, 0.10, 1.0)
    alpha_n = 30.0 * alpha_q if alpha_mean is not None else 0.0
    parts = []
    if emp_mean is not None and effn > 0:
        parts.append((emp_mean, effn))
    if alpha_mean is not None and alpha_n > 0:
        parts.append((alpha_mean, alpha_n))
    if not parts:
        return {"posterior_mean_r": None, "posterior_lcb_r": None, "posterior_eff_n": 0.0, "prob_edge_gt_zero": None}
    raw_n = sum(w for _, w in parts)
    raw_mean = sum(v * w for v, w in parts) / raw_n
    post_mean = raw_mean * (raw_n / (raw_n + PRIOR_N))
    se = emp_std / math.sqrt(max(1.0, raw_n))
    post_lcb = post_mean - 1.65 * se
    prob = normal_cdf(post_mean / se) if se > 0 else None
    return {"posterior_mean_r": post_mean, "posterior_lcb_r": post_lcb, "posterior_eff_n": raw_n, "prob_edge_gt_zero": prob}


def source_counts(events: List[Dict[str, Any]]) -> Dict[str, int]:
    out = defaultdict(int)
    for e in events:
        out[e.get("source") or "unknown"] += 1
    return dict(out)


def build_spine() -> Tuple[Dict[str, Any], Dict[str, List[Dict[str, Any]]]]:
    con = connect_ro()
    quick_check = con.execute("PRAGMA quick_check").fetchone()[0]
    datasets = {name: load_rows(con, table) for name, table in TABLES.items()}
    events = collect_evidence(datasets)

    alpha_maps = {name: latest_by_key(datasets[name]) for name in ("tensor", "registry", "posterior", "rollup", "promotion", "final_gate", "hygiene")}

    grouped: Dict[str, List[EvidenceEvent]] = defaultdict(list)
    for e in events:
        grouped[e.key].append(e)

    keys = set(k for k in grouped if not str(k).startswith("ORPHAN|"))
    for mp in alpha_maps.values():
        keys.update(mp.keys())

    candidates = []
    for k in sorted(keys):
        parts = k.split("|")
        if len(parts) < 3:
            continue
        alpha = alpha_prior(k, alpha_maps)
        evs = grouped.get(k, [])
        clean = [e for e in evs if e.clean]
        live = [e for e in clean if e.source != "shadow_resolved"]
        shadow = [e for e in clean if e.source == "shadow_resolved"]
        trace = len(clean) / len(evs) if evs else 0.0
        row = {
            "key": k,
            "symbol": parts[0],
            "side": parts[1],
            "setup": parts[2],
            "traceability_score": trace,
            "clean_n": len(clean),
            "live_n": len(live),
            "shadow_n": len(shadow),
            "alpha_net_expectancy_r": alpha.get("alpha_net_expectancy_r"),
            "alpha_net_lcb_r": alpha.get("alpha_net_lcb_r"),
            "alpha_profit_factor": alpha.get("alpha_profit_factor"),
            "alpha_prob_edge_gt_zero": alpha.get("alpha_prob_edge_gt_zero"),
            "effective_quality": alpha.get("alpha_quality") if alpha.get("alpha_quality") is not None else trace,
            "final_gate_allow_trade": alpha.get("final_gate_allow_trade"),
            "hard_veto_text": alpha.get("alpha_hard_veto") or "",
            "alpha_sources": alpha.get("sources"),
        }
        candidates.append(row)

    by_key: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for e in events:
        by_key[e.key].append(asdict(e))

    evidence_totals = {
        "events": len(events),
        "clean_events": len([e for e in events if e.clean]),
        "orphan_events": len([e for e in events if e.orphan]),
        "excluded_events": len([e for e in events if e.excluded]),
        "events_without_r": len([e for e in events if e.r is None]),
    }
    source_totals = {s: len([e for e in events if e.source == s]) for s in SOURCE_WEIGHT}
    con.close()

    return {
        "version": "embedded_evidence_spine_v17_5_1",
        "generated_utc": utc_now(),
        "quick_check": quick_check,
        "candidate_count": len(candidates),
        "evidence_totals": evidence_totals,
        "source_totals": source_totals,
        "candidates": candidates,
    }, by_key


def derive_candidate(base: Dict[str, Any], events: List[Dict[str, Any]], family_size: int) -> Dict[str, Any]:
    clean = [e for e in events if e.get("clean") and fnum(e.get("r"), None) is not None]
    live = [e for e in clean if e.get("source") != "shadow_resolved"]
    shadow = [e for e in clean if e.get("source") == "shadow_resolved"]
    xs = [float(e["r"]) for e in clean]
    ws = [float(e.get("weight") or 1.0) for e in clean]
    live_xs = [float(e["r"]) for e in live]
    live_ws = [float(e.get("weight") or 1.0) for e in live]
    shadow_xs = [float(e["r"]) for e in shadow]
    shadow_ws = [float(e.get("weight") or 1.0) for e in shadow]

    emp_mean = weighted_mean(xs, ws)
    emp_std = weighted_std(xs, ws, emp_mean)
    effn = effective_n(ws)
    live_mean = weighted_mean(live_xs, live_ws)
    shadow_mean = weighted_mean(shadow_xs, shadow_ws)
    alpha_mean = fnum(base.get("alpha_net_expectancy_r"), None)
    alpha_quality = fnum(base.get("effective_quality"), None)
    post = posterior_blend(emp_mean, emp_std, effn, alpha_mean, alpha_quality)

    boot_lcb = bootstrap_lcb(xs, ws, base["key"], BOOTSTRAP_N)
    def_lcb = deflated_lcb(post["posterior_mean_r"], emp_std, post["posterior_eff_n"], family_size)
    lcb_stack = [v for v in [post["posterior_lcb_r"], boot_lcb, def_lcb, fnum(base.get("alpha_net_lcb_r"), None)] if v is not None]
    institutional_lcb = min(lcb_stack) if lcb_stack else None

    rec = recency_score(clean)
    mean_stack = [v for v in [post["posterior_mean_r"], rec["recency_mean_r"], alpha_mean] if v is not None]
    robust_mean = sum(mean_stack) / len(mean_stack) if mean_stack else None

    pf = profit_factor(xs, ws)
    alpha_pf = fnum(base.get("alpha_profit_factor"), None)
    effective_pf = min([v for v in [pf, alpha_pf] if v is not None], default=None)
    live_pf = profit_factor(live_xs, live_ws)
    dd = max_drawdown(xs, ws) if xs else None
    worst = min(xs) if xs else None
    best = max(xs) if xs else None
    cvar = cvar_left(xs, 0.05)
    fold = fold_stability(clean)
    trace = fnum(base.get("traceability_score"), 0.0) or 0.0
    q = fnum(base.get("effective_quality"), None)
    prob = post["prob_edge_gt_zero"]
    live_ratio = len(live) / len(clean) if clean else 0.0
    shadow_ratio = len(shadow) / len(clean) if clean else 0.0

    red, yellow, green = [], [], []
    if base.get("hard_veto_text"):
        red.append("HARD_VETO_PRESENT")
    if not clean:
        red.append("NO_CLEAN_EVIDENCE")
    if institutional_lcb is not None and institutional_lcb <= -0.10:
        red.append("LCB_STRUCTURALLY_NEGATIVE")
    if cvar is not None and cvar <= -2.0:
        red.append("CVaR_TOO_NEGATIVE")
    if worst is not None and worst <= -2.5:
        red.append("FAT_TAIL_WORST_R")
    if dd is not None and dd >= 8.0:
        red.append("DRAWDOWN_R_TOO_HIGH")

    if robust_mean is not None and robust_mean > 0:
        green.append("ROBUST_MEAN_POSITIVE")
    else:
        yellow.append("ROBUST_MEAN_NOT_POSITIVE")
    if institutional_lcb is not None and institutional_lcb > 0:
        green.append("INSTITUTIONAL_LCB_POSITIVE")
    else:
        yellow.append("INSTITUTIONAL_LCB_NOT_POSITIVE")
    if prob is not None and prob >= 0.60:
        green.append("PROB_EDGE_OK")
    else:
        yellow.append("PROB_EDGE_WEAK")
    if effective_pf is not None and effective_pf >= 1.12:
        green.append("PF_OK")
    else:
        yellow.append("PF_WEAK")
    if fold["stability_score"] >= 0.50:
        green.append("FOLD_STABILITY_OK")
    else:
        yellow.append("FOLD_STABILITY_WEAK")
    if len(live) >= 3:
        green.append("LIVE_EVIDENCE_PRESENT")
    else:
        yellow.append("LOW_LIVE_EVIDENCE")
    if trace < 0.45:
        yellow.append("TRACEABILITY_WEAK")
    if len(clean) < 30:
        yellow.append("LOW_CLEAN_SAMPLE")

    score = 0.0
    score += 20.0 * clamp(((robust_mean or 0.0) + 0.01) / 0.09)
    score += 20.0 * clamp(((institutional_lcb or -0.06) + 0.02) / 0.07)
    score += 14.0 * clamp(((prob or 0.50) - 0.50) / 0.35)
    score += 11.0 * clamp(((effective_pf or 1.0) - 1.0) / 0.70)
    score += 9.0 * clamp(fold["stability_score"])
    score += 8.0 * clamp(rec["recency_score"])
    score += 7.0 * clamp(q or 0.0)
    score += 6.0 * clamp(live_ratio * 2.0)
    score += 5.0 * clamp(trace)
    score = round(clamp(score, 0.0, 100.0), 2)

    variance = (emp_std or 0.35) ** 2
    raw_kelly = max(0.0, (robust_mean or 0.0) / variance) if variance > 0 else 0.0
    live_cap = 0.12 if len(live) < 3 else 0.22 if len(live) < 10 else 0.45
    lcb_cap = 0.10 if institutional_lcb is None or institutional_lcb <= 0 else 0.50
    cvar_cap = 0.12 if cvar is not None and cvar < -1.2 else 0.45
    size_mult = min(raw_kelly * 0.18 * clamp(score / 100.0), live_cap, lcb_cap, cvar_cap, 0.65)

    if red:
        state = "BLOCKED"
        size_mult = 0.0
    elif robust_mean is not None and robust_mean > 0:
        if institutional_lcb is not None and institutional_lcb > 0 and len(live) >= 15 and score >= 76 and (effective_pf or 0) >= 1.18:
            state = "PAPER_AUTHORITY_READY"
        elif len(live) >= 3 and score >= 60 and (effective_pf or 0) >= 1.05:
            state = "PAPER_MICRO_READY"
            size_mult = min(size_mult if size_mult > 0 else 0.04, 0.16)
        elif len(clean) >= 15 and score >= 45:
            state = "SHADOW_AUTHORITY_READY"
            size_mult = 0.015
        else:
            state = "RESEARCH_POSITIVE_BUT_WEAK"
            size_mult = 0.0
    else:
        state = "RESEARCH_ONLY"
        size_mult = 0.0

    if state == "PAPER_AUTHORITY_READY" and institutional_lcb is not None and institutional_lcb > 0.015 and len(live) >= 25 and score >= 84:
        state = "PROMOTION_WATCH"

    return {
        "key": base["key"],
        "symbol": base.get("symbol"),
        "side": base.get("side"),
        "setup": base.get("setup"),
        "authority_state": state,
        "brain_score": score,
        "recommended_size_mult": round(size_mult, 4),
        "clean_n": len(clean),
        "live_n": len(live),
        "shadow_n": len(shadow),
        "effective_n": round(effn, 4),
        "empirical_mean_r": emp_mean,
        "empirical_std_r": emp_std,
        "live_mean_r": live_mean,
        "shadow_mean_r": shadow_mean,
        "robust_mean_r": robust_mean,
        "posterior_mean_r": post["posterior_mean_r"],
        "posterior_lcb_r": post["posterior_lcb_r"],
        "bootstrap_lcb_r": boot_lcb,
        "deflated_lcb_r": def_lcb,
        "institutional_lcb_r": institutional_lcb,
        "prob_edge_gt_zero": prob,
        "profit_factor": effective_pf,
        "live_profit_factor": live_pf,
        "max_drawdown_r": dd,
        "worst_r": worst,
        "best_r": best,
        "cvar_5_r": cvar,
        "fold_stability": fold,
        "recency": rec,
        "traceability_score": trace,
        "quality": q,
        "live_ratio": live_ratio,
        "shadow_ratio": shadow_ratio,
        "source_counts": source_counts(clean),
        "green_flags": green,
        "yellow_flags": yellow,
        "red_flags": red,
        "final_gate_allow_trade": base.get("final_gate_allow_trade"),
    }


def build() -> Dict[str, Any]:
    OUT.mkdir(parents=True, exist_ok=True)
    spine_report, by_key = build_spine()
    bases = {c["key"]: c for c in spine_report.get("candidates", [])}
    keys = sorted(set(bases.keys()) | set(k for k in by_key if not str(k).startswith("ORPHAN|")))
    family = max(52, len(keys))
    candidates = []
    for k in keys:
        base = bases.get(k)
        if not base:
            parts = str(k).split("|")
            base = {
                "key": k,
                "symbol": parts[0] if len(parts) > 0 else "",
                "side": parts[1] if len(parts) > 1 else "",
                "setup": parts[2] if len(parts) > 2 else "",
                "traceability_score": 0.0,
                "effective_quality": None,
            }
        candidates.append(derive_candidate(base, by_key.get(k, []), family))
    order = {
        "PROMOTION_WATCH": 7,
        "PAPER_AUTHORITY_READY": 6,
        "PAPER_MICRO_READY": 5,
        "SHADOW_AUTHORITY_READY": 4,
        "RESEARCH_POSITIVE_BUT_WEAK": 3,
        "RESEARCH_ONLY": 2,
        "BLOCKED": 1,
    }
    candidates.sort(key=lambda c: (order.get(c["authority_state"], 0), c["brain_score"], c["clean_n"]), reverse=True)
    states = defaultdict(int)
    for c in candidates:
        states[c["authority_state"]] += 1
    return {
        "version": VERSION,
        "generated_utc": utc_now(),
        "db": str(DB),
        "spine_quick_check": spine_report.get("quick_check"),
        "candidate_count": len(candidates),
        "state_counts": dict(states),
        "evidence_totals": spine_report.get("evidence_totals"),
        "source_totals": spine_report.get("source_totals"),
        "candidates": candidates,
    }


def connect_rw() -> sqlite3.Connection:
    con = sqlite3.connect(str(DB), timeout=20)
    con.row_factory = sqlite3.Row
    return con


def write_db(report: Dict[str, Any]) -> None:
    con = connect_rw()
    con.execute("""
        CREATE TABLE IF NOT EXISTS institutional_quant_brain_v17_5_1 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            version TEXT NOT NULL,
            key TEXT NOT NULL,
            symbol TEXT,
            side TEXT,
            setup TEXT,
            authority_state TEXT,
            brain_score REAL,
            recommended_size_mult REAL,
            clean_n INTEGER,
            live_n INTEGER,
            shadow_n INTEGER,
            robust_mean_r REAL,
            institutional_lcb_r REAL,
            prob_edge_gt_zero REAL,
            profit_factor REAL,
            cvar_5_r REAL,
            max_drawdown_r REAL,
            fold_stability_score REAL,
            traceability_score REAL,
            red_flags TEXT,
            yellow_flags TEXT,
            green_flags TEXT,
            payload TEXT
        )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_qb_v17_5_1_state_ts ON institutional_quant_brain_v17_5_1(authority_state, ts)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_qb_v17_5_1_key_ts ON institutional_quant_brain_v17_5_1(key, ts)")
    ts = report["generated_utc"]
    for c in report["candidates"]:
        con.execute("""
            INSERT INTO institutional_quant_brain_v17_5_1
            (ts, version, key, symbol, side, setup, authority_state, brain_score,
             recommended_size_mult, clean_n, live_n, shadow_n, robust_mean_r,
             institutional_lcb_r, prob_edge_gt_zero, profit_factor, cvar_5_r,
             max_drawdown_r, fold_stability_score, traceability_score,
             red_flags, yellow_flags, green_flags, payload)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            ts,
            VERSION,
            c.get("key"),
            c.get("symbol"),
            c.get("side"),
            c.get("setup"),
            c.get("authority_state"),
            c.get("brain_score"),
            c.get("recommended_size_mult"),
            c.get("clean_n"),
            c.get("live_n"),
            c.get("shadow_n"),
            c.get("robust_mean_r"),
            c.get("institutional_lcb_r"),
            c.get("prob_edge_gt_zero"),
            c.get("profit_factor"),
            c.get("cvar_5_r"),
            c.get("max_drawdown_r"),
            (c.get("fold_stability") or {}).get("stability_score"),
            c.get("traceability_score"),
            json.dumps(c.get("red_flags"), sort_keys=True),
            json.dumps(c.get("yellow_flags"), sort_keys=True),
            json.dumps(c.get("green_flags"), sort_keys=True),
            json.dumps(c, sort_keys=True),
        ))
    con.commit()
    con.close()


def fmt(x: Any) -> str:
    v = fnum(x, None)
    return "-" if v is None else f"{v:.4f}"


def write_md(report: Dict[str, Any]) -> None:
    lines = []
    lines.append("# V17.5.1 Self-contained Institutional Quant Brain")
    lines.append("")
    lines.append(f"- UTC: `{report['generated_utc']}`")
    lines.append(f"- DB quick_check: `{report.get('spine_quick_check')}`")
    lines.append(f"- Candidates: `{report['candidate_count']}`")
    lines.append(f"- States: `{report['state_counts']}`")
    lines.append(f"- Evidence totals: `{report.get('evidence_totals')}`")
    lines.append(f"- Source totals: `{report.get('source_totals')}`")
    lines.append("")

    actionable = [
        c for c in report["candidates"]
        if c["authority_state"] in {
            "PROMOTION_WATCH",
            "PAPER_AUTHORITY_READY",
            "PAPER_MICRO_READY",
            "SHADOW_AUTHORITY_READY",
            "RESEARCH_POSITIVE_BUT_WEAK",
        }
    ]
    lines.append("## Positive / actionable lanes")
    if not actionable:
        lines.append("- none")
    else:
        lines.append("| rank | state | score | size | symbol | side | setup | clean | live | shadow | robust_mean | inst_LCB | prob | PF | CVaR5 | fold | trace | flags |")
        lines.append("|---:|---|---:|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|")
        for i, c in enumerate(actionable[:50], 1):
            flags = ",".join((c.get("red_flags") or []) + (c.get("yellow_flags") or []) + (c.get("green_flags") or []))[:180]
            fold = (c.get("fold_stability") or {}).get("stability_score")
            lines.append(
                f"| {i} | {c['authority_state']} | {fmt(c['brain_score'])} | {fmt(c['recommended_size_mult'])} | "
                f"{c.get('symbol') or '-'} | {c.get('side') or '-'} | {(c.get('setup') or '-')[:32]} | "
                f"{c.get('clean_n')} | {c.get('live_n')} | {c.get('shadow_n')} | "
                f"{fmt(c.get('robust_mean_r'))} | {fmt(c.get('institutional_lcb_r'))} | {fmt(c.get('prob_edge_gt_zero'))} | "
                f"{fmt(c.get('profit_factor'))} | {fmt(c.get('cvar_5_r'))} | {fmt(fold)} | {fmt(c.get('traceability_score'))} | {flags} |"
            )

    lines.append("")
    lines.append("## Full institutional ranking")
    lines.append("| rank | state | score | symbol | side | setup | clean | live | shadow | robust_mean | inst_LCB | PF | DD | worst | flags |")
    lines.append("|---:|---|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|")
    for i, c in enumerate(report["candidates"][:100], 1):
        flags = ",".join((c.get("red_flags") or []) + (c.get("yellow_flags") or []))[:160]
        lines.append(
            f"| {i} | {c['authority_state']} | {fmt(c['brain_score'])} | {c.get('symbol') or '-'} | {c.get('side') or '-'} | "
            f"{(c.get('setup') or '-')[:32]} | {c.get('clean_n')} | {c.get('live_n')} | {c.get('shadow_n')} | "
            f"{fmt(c.get('robust_mean_r'))} | {fmt(c.get('institutional_lcb_r'))} | {fmt(c.get('profit_factor'))} | "
            f"{fmt(c.get('max_drawdown_r'))} | {fmt(c.get('worst_r'))} | {flags} |"
        )

    (OUT / "quant_brain_summary.md").write_text("\n".join(lines))


def write_outputs(report: Dict[str, Any], write_database: bool) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "quant_brain_latest.json").write_text(json.dumps(report, indent=2, sort_keys=True))
    write_md(report)
    with (OUT / "quant_brain_ledger.jsonl").open("a") as f:
        f.write(json.dumps({"ts": report["generated_utc"], "states": report["state_counts"], "top": report["candidates"][:10]}, sort_keys=True) + "\n")
    if write_database:
        write_db(report)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write-db", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    report = build()
    write_outputs(report, args.write_db)

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print("===== V17.5.1 SELF-CONTAINED INSTITUTIONAL QUANT BRAIN =====")
        print("quick_check:", report.get("spine_quick_check"))
        print("candidate_count:", report["candidate_count"])
        print("state_counts:", report["state_counts"])
        print("evidence_totals:", report.get("evidence_totals"))
        for i, c in enumerate(report["candidates"][:35], 1):
            print(
                f"#{i:02d} {c['authority_state']} score={fmt(c['brain_score'])} "
                f"size={fmt(c['recommended_size_mult'])} {c.get('symbol') or '-'} {c.get('side') or '-'} "
                f"{(c.get('setup') or '-')[:30]} clean={c.get('clean_n')} live={c.get('live_n')} shadow={c.get('shadow_n')} "
                f"mean={fmt(c.get('robust_mean_r'))} lcb={fmt(c.get('institutional_lcb_r'))} "
                f"pf={fmt(c.get('profit_factor'))} cvar={fmt(c.get('cvar_5_r'))} "
                f"flags={','.join((c.get('red_flags') or []) + (c.get('yellow_flags') or []))[:110]}"
            )

    return 0 if report.get("spine_quick_check") == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
