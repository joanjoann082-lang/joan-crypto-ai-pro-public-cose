from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import sqlite3
import json
import math
import hashlib
from datetime import datetime, timezone


VERSION = "ALPHA_FAMILY_RESEARCH_CORE_V6_1_INSTITUTIONAL"


def utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def fnum(x: Any, default: float = 0.0) -> float:
    try:
        if x is None or x == "":
            return default
        y = float(x)
        return y if math.isfinite(y) else default
    except Exception:
        return default


def inum(x: Any, default: int = 0) -> int:
    try:
        return int(float(x))
    except Exception:
        return default


def js(x: Any) -> str:
    return json.dumps(x, ensure_ascii=False, sort_keys=True, default=str)


def jload(x: Any, default=None):
    if default is None:
        default = {}
    if isinstance(x, (dict, list)):
        return x
    if not isinstance(x, str) or not x.strip():
        return default
    try:
        return json.loads(x)
    except Exception:
        return default


def canon(x: Any, default: str = "UNKNOWN") -> str:
    if x is None or x == "":
        return default
    return str(x).strip().upper()


def table_exists(con: sqlite3.Connection, t: str) -> bool:
    return con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (t,),
    ).fetchone() is not None


def cols(con: sqlite3.Connection, t: str) -> List[str]:
    try:
        return [r[1] for r in con.execute(f"PRAGMA table_info({t})")]
    except Exception:
        return []


def parse_ts(x: Any) -> Optional[datetime]:
    if not x:
        return None
    s = str(x).replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


def horizon_bucket(entry_ts: Any, exit_ts: Any, fallback_min: Any = None) -> int:
    fb = inum(fallback_min, 0)
    if fb > 0:
        m = fb
    else:
        a = parse_ts(entry_ts)
        b = parse_ts(exit_ts)
        if a and b:
            m = max(1, int((b - a).total_seconds() / 60))
        else:
            m = 120

    if m <= 30:
        return 30
    if m <= 75:
        return 60
    if m <= 150:
        return 120
    if m <= 300:
        return 240
    return 480


def setup_family(setup: str) -> str:
    s = canon(setup)
    if any(x in s for x in ["CAPITULATION", "REBOUND", "PULLBACK", "DIP", "RECLAIM"]):
        return "REBOUND_PULLBACK_FAMILY"
    if any(x in s for x in ["BREAKOUT", "MOMENTUM", "IMPULSE", "EXPANSION"]):
        return "BREAKOUT_MOMENTUM_FAMILY"
    if any(x in s for x in ["BOUNCE_SHORT", "EXHAUSTION", "FADE", "REVERSAL"]):
        return "EXHAUSTION_REVERSAL_FAMILY"
    if any(x in s for x in ["TREND", "CONTINUATION"]):
        return "TREND_CONTINUATION_FAMILY"
    if any(x in s for x in ["RANGE", "MEAN", "CHOP"]):
        return "MEAN_REVERSION_FAMILY"
    return s or "UNKNOWN_FAMILY"


def stable_id(*parts: Any) -> str:
    raw = "|".join(str(x) for x in parts)
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


@dataclass
class Obs:
    source: str
    source_weight: float
    symbol: str
    side: str
    setup: str
    family: str
    regime: str
    session: str
    volatility_bucket: str
    news_bucket: str
    context_key: str
    horizon_min: int
    r: float
    mfe_r: float
    mae_r: float
    pnl_usd: float
    quality: str
    payload: Dict[str, Any]

    @property
    def family_key(self) -> str:
        return f"{self.symbol}:{self.side}:{self.family}:{self.horizon_min}"

    @property
    def child_key(self) -> str:
        return "|".join([
            self.family_key,
            self.regime,
            self.session,
            self.volatility_bucket,
            self.news_bucket,
            self.setup,
        ])


def weighted_mean(values: List[float], weights: List[float]) -> float:
    sw = sum(weights)
    if sw <= 0:
        return 0.0
    return sum(v * w for v, w in zip(values, weights)) / sw


def weighted_var(values: List[float], weights: List[float]) -> float:
    sw = sum(weights)
    if sw <= 0 or len(values) < 2:
        return 0.0
    m = weighted_mean(values, weights)
    return sum(w * ((v - m) ** 2) for v, w in zip(values, weights)) / sw


def weighted_lcb(values: List[float], weights: List[float], z: float = 1.64) -> float:
    if not values:
        return 0.0
    sw = sum(weights)
    if sw <= 1:
        return weighted_mean(values, weights)
    m = weighted_mean(values, weights)
    sd = math.sqrt(max(0.0, weighted_var(values, weights)))
    return m - z * sd / math.sqrt(sw)


def weighted_pf(values: List[float], weights: List[float]) -> float:
    pos = sum(max(0.0, v) * w for v, w in zip(values, weights))
    neg = abs(sum(min(0.0, v) * w for v, w in zip(values, weights)))
    if neg > 0:
        return pos / neg
    if pos > 0:
        return 999.0
    return 0.0


def percentile(values: List[float], p: float) -> float:
    if not values:
        return 0.0
    xs = sorted(values)
    idx = min(len(xs) - 1, max(0, round((len(xs) - 1) * p)))
    return xs[idx]


def source_weight(source: str, quality: str = "") -> float:
    s = canon(source)
    q = canon(quality)

    if "TRADE" in s or "POSITION" in s or "TANCAMENT" in s:
        base = 1.00
    elif "FORWARD" in s or "RESULTATS_QUANT" in s:
        base = 0.72
    elif "SHADOW_RESULT" in s or "UNIVERSAL_SHADOW_RESULTS" in s:
        base = 0.38
    else:
        base = 0.55

    if q in {"CONTAMINATED", "BROKEN", "INVALID", "DIRTY"}:
        base *= 0.10
    elif q in {"TEST", "PROVA"}:
        base *= 0.25
    elif q in {"NET", "CLEAN", "VALID"}:
        base *= 1.00

    return max(0.02, min(1.0, base))


def load_resultats_quant_nets(con: sqlite3.Connection) -> List[Obs]:
    if not table_exists(con, "resultats_quant_nets"):
        return []

    out: List[Obs] = []
    for r in con.execute("SELECT * FROM resultats_quant_nets"):
        d = dict(r)

        rr_raw = d.get("resultat_r")
        if rr_raw is None:
            continue

        symbol = canon(d.get("symbol"))
        side = canon(d.get("side"))
        setup = canon(d.get("setup"))
        if symbol == "UNKNOWN" or side == "UNKNOWN" or setup == "UNKNOWN":
            continue

        payload = jload(d.get("payload"), {})
        quality = canon(d.get("qualitat"), "UNKNOWN")
        source = canon(d.get("font"), "RESULTATS_QUANT_NETS")

        out.append(Obs(
            source=source,
            source_weight=source_weight(source, quality),
            symbol=symbol,
            side=side,
            setup=setup,
            family=setup_family(setup),
            regime=canon(d.get("regime")),
            session=canon(d.get("session")),
            volatility_bucket=canon(d.get("volatility_bucket")),
            news_bucket=canon(d.get("news_bucket")),
            context_key=canon(d.get("context_key")),
            horizon_min=horizon_bucket(d.get("entry_ts"), d.get("exit_ts"), payload.get("horizon_min") if isinstance(payload, dict) else None),
            r=fnum(d.get("resultat_r"), 0.0),
            mfe_r=fnum(d.get("mfe_r"), 0.0),
            mae_r=fnum(d.get("mae_r"), 0.0),
            pnl_usd=fnum(d.get("pnl_usd"), 0.0),
            quality=quality,
            payload=payload if isinstance(payload, dict) else {},
        ))

    return out


def load_trades(con: sqlite3.Connection) -> List[Obs]:
    if not table_exists(con, "trades"):
        return []

    out: List[Obs] = []
    for r in con.execute("SELECT * FROM trades"):
        d = dict(r)
        payload = jload(d.get("payload"), {})

        symbol = canon(d.get("symbol"))
        side = canon(d.get("side"))
        setup = canon(d.get("setup"))
        if symbol == "UNKNOWN" or side == "UNKNOWN" or setup == "UNKNOWN":
            continue

        r_val = d.get("pnl_r")
        if r_val is None:
            r_val = payload.get("pnl_r") if isinstance(payload, dict) else None
        if r_val is None:
            continue

        close = payload.get("close", {}) if isinstance(payload, dict) else {}

        out.append(Obs(
            source="TRADES_REALIZED",
            source_weight=1.0,
            symbol=symbol,
            side=side,
            setup=setup,
            family=setup_family(setup),
            regime=canon(close.get("regime") if isinstance(close, dict) else None),
            session=canon(close.get("session") if isinstance(close, dict) else None),
            volatility_bucket=canon(close.get("volatility_bucket") if isinstance(close, dict) else None),
            news_bucket=canon(close.get("news_bucket") if isinstance(close, dict) else None),
            context_key=canon(close.get("context_key") if isinstance(close, dict) else None),
            horizon_min=horizon_bucket(None, d.get("ts"), close.get("horizon_min") if isinstance(close, dict) else None),
            r=fnum(r_val, 0.0),
            mfe_r=fnum(close.get("mfe_r"), 0.0) if isinstance(close, dict) else 0.0,
            mae_r=fnum(close.get("mae_r"), 0.0) if isinstance(close, dict) else 0.0,
            pnl_usd=fnum(d.get("pnl_usd"), 0.0),
            quality="LIVE_REALIZED",
            payload=payload if isinstance(payload, dict) else {},
        ))

    return out


def load_shadow_results(con: sqlite3.Connection) -> List[Obs]:
    if not table_exists(con, "universal_shadow_results_v2"):
        return []

    out: List[Obs] = []
    for r in con.execute("SELECT * FROM universal_shadow_results_v2"):
        d = dict(r)

        symbol = canon(d.get("symbol"))
        side = canon(d.get("side"))
        setup = canon(d.get("setup"))
        if symbol == "UNKNOWN" or side == "UNKNOWN" or setup == "UNKNOWN":
            continue

        rr = d.get("result_r")
        if rr is None:
            continue

        out.append(Obs(
            source="UNIVERSAL_SHADOW_RESULTS_V2",
            source_weight=0.38,
            symbol=symbol,
            side=side,
            setup=setup,
            family=setup_family(setup),
            regime="UNKNOWN",
            session="UNKNOWN",
            volatility_bucket="UNKNOWN",
            news_bucket="UNKNOWN",
            context_key=canon(d.get("profile")),
            horizon_min=horizon_bucket(None, None, d.get("horizon_min")),
            r=fnum(rr, 0.0),
            mfe_r=fnum(d.get("mfe_r"), 0.0),
            mae_r=fnum(d.get("mae_r"), 0.0),
            pnl_usd=0.0,
            quality=canon(d.get("outcome"), "SHADOW"),
            payload=jload(d.get("payload"), {}),
        ))

    return out


def load_shadow_registry(con: sqlite3.Connection) -> Dict[str, Dict[str, Any]]:
    if not table_exists(con, "universal_shadow_registry_v2"):
        return {}

    out: Dict[str, Dict[str, Any]] = {}

    for r in con.execute("SELECT * FROM universal_shadow_registry_v2"):
        d = dict(r)
        symbol = canon(d.get("symbol"))
        side = canon(d.get("side"))
        setup = canon(d.get("setup"))
        family = setup_family(setup)
        horizon = horizon_bucket(None, None, d.get("horizon_min"))
        key = f"{symbol}:{side}:{family}:{horizon}"

        cur = out.setdefault(key, {
            "rows": 0,
            "n": 0,
            "exp_sum": 0.0,
            "pf_sum": 0.0,
            "quality_sum": 0.0,
            "states": {},
            "recommendations": {},
        })

        n = max(1, inum(d.get("n"), 0))
        cur["rows"] += 1
        cur["n"] += n
        cur["exp_sum"] += fnum(d.get("expectancy_r"), 0.0) * n
        cur["pf_sum"] += fnum(d.get("profit_factor"), 0.0) * n
        cur["quality_sum"] += fnum(d.get("quality_score"), 0.0) * n

        st = canon(d.get("state"))
        rec = canon(d.get("recommendation"))
        cur["states"][st] = cur["states"].get(st, 0) + 1
        cur["recommendations"][rec] = cur["recommendations"].get(rec, 0) + 1

    for v in out.values():
        denom = max(1, v["n"])
        v["expectancy_r"] = v["exp_sum"] / denom
        v["profit_factor"] = v["pf_sum"] / denom
        v["quality_score"] = v["quality_sum"] / denom

    return out


def load_promotion_context(con: sqlite3.Connection) -> Dict[str, Dict[str, Any]]:
    if not table_exists(con, "research_promotion_decisions_v1"):
        return {}

    out: Dict[str, Dict[str, Any]] = {}

    for r in con.execute("SELECT * FROM research_promotion_decisions_v1"):
        d = dict(r)
        symbol = canon(d.get("symbol"))
        side = canon(d.get("side"))
        setup = canon(d.get("setup"))
        family = setup_family(setup)
        key = f"{symbol}:{side}:{family}"

        cur = out.setdefault(key, {
            "rows": 0,
            "allow_canary_sum": 0,
            "allow_direct_sum": 0,
            "quality_sum": 0.0,
            "states": {},
            "reasons": {},
        })

        cur["rows"] += 1
        cur["allow_canary_sum"] += inum(d.get("allow_canary_probe"), 0)
        cur["allow_direct_sum"] += inum(d.get("allow_direct_open"), 0)
        cur["quality_sum"] += fnum(d.get("quality_score"), 0.0)

        st = canon(d.get("promotion_state"))
        cur["states"][st] = cur["states"].get(st, 0) + 1

        reasons = jload(d.get("reasons"), [])
        if isinstance(reasons, list):
            for x in reasons[:8]:
                sx = canon(x)
                cur["reasons"][sx] = cur["reasons"].get(sx, 0) + 1

    for v in out.values():
        v["avg_quality_score"] = v["quality_sum"] / max(1, v["rows"])
        v["canary_ratio"] = v["allow_canary_sum"] / max(1, v["rows"])
        v["direct_ratio"] = v["allow_direct_sum"] / max(1, v["rows"])

    return out


def by_group(obs: List[Obs], attr: str) -> Dict[str, List[Obs]]:
    out: Dict[str, List[Obs]] = {}
    for o in obs:
        k = getattr(o, attr)
        out.setdefault(k, []).append(o)
    return out


def calc_metrics(rows: List[Obs]) -> Dict[str, Any]:
    rs = [x.r for x in rows]
    ws = [x.source_weight for x in rows]
    mfe = [x.mfe_r for x in rows]
    mae = [x.mae_r for x in rows]

    raw_n = len(rows)
    weighted_n = sum(ws)
    exp = weighted_mean(rs, ws)
    lcbv = weighted_lcb(rs, ws)
    pf = weighted_pf(rs, ws)
    winrate = sum(w for r, w in zip(rs, ws) if r > 0) / max(0.000001, weighted_n)

    avg_mfe = weighted_mean(mfe, ws)
    avg_mae = weighted_mean(mae, ws)

    capture_values = []
    giveback_values = []
    for x in rows:
        if x.mfe_r > 0:
            capture_values.append(max(-3.0, min(3.0, x.r / x.mfe_r)))
            giveback_values.append(max(0.0, x.mfe_r - x.r))

    capture_eff = sum(capture_values) / len(capture_values) if capture_values else 0.0
    avg_giveback = sum(giveback_values) / len(giveback_values) if giveback_values else 0.0
    mfe_mae_ratio = avg_mfe / abs(avg_mae) if avg_mae < 0 else (999.0 if avg_mfe > 0 else 0.0)

    return {
        "raw_n": raw_n,
        "weighted_n": weighted_n,
        "expectancy_r": exp,
        "lcb_r": lcbv,
        "profit_factor": pf,
        "winrate": winrate,
        "median_r": percentile(rs, 0.50),
        "p10_r": percentile(rs, 0.10),
        "p90_r": percentile(rs, 0.90),
        "avg_mfe_r": avg_mfe,
        "avg_mae_r": avg_mae,
        "mfe_mae_ratio": mfe_mae_ratio,
        "capture_efficiency": capture_eff,
        "avg_giveback_r": avg_giveback,
        "sources": sorted(set(x.source for x in rows)),
    }


def source_consensus(rows: List[Obs]) -> Dict[str, Any]:
    groups: Dict[str, List[Obs]] = {}
    for x in rows:
        groups.setdefault(x.source, []).append(x)

    parts = {}
    signs = []
    for src, xs in groups.items():
        m = calc_metrics(xs)
        parts[src] = {
            "n": m["raw_n"],
            "weighted_n": m["weighted_n"],
            "expectancy_r": m["expectancy_r"],
            "profit_factor": m["profit_factor"],
        }
        if m["weighted_n"] >= 3:
            if m["expectancy_r"] > 0.01:
                signs.append(1)
            elif m["expectancy_r"] < -0.01:
                signs.append(-1)
            else:
                signs.append(0)

    conflict = bool(signs and max(signs) > 0 and min(signs) < 0)
    agreement = 0.0
    if signs:
        agreement = abs(sum(signs)) / len(signs)

    return {
        "parts": parts,
        "conflict": conflict,
        "agreement": agreement,
        "source_count": len(groups),
    }


def context_robustness(rows: List[Obs]) -> Dict[str, Any]:
    keys = []
    for o in rows:
        keys.append("|".join([o.regime, o.session, o.volatility_bucket, o.news_bucket]))

    groups: Dict[str, List[Obs]] = {}
    for k, o in zip(keys, rows):
        groups.setdefault(k, []).append(o)

    child_metrics = []
    total_w = 0.0
    positive_w = 0.0
    top_w = 0.0

    for k, xs in groups.items():
        m = calc_metrics(xs)
        w = m["weighted_n"]
        total_w += w
        top_w = max(top_w, w)
        if m["expectancy_r"] > 0 and m["profit_factor"] >= 1.0 and m["weighted_n"] >= 2:
            positive_w += w
        child_metrics.append({
            "context": k,
            "weighted_n": w,
            "expectancy_r": m["expectancy_r"],
            "lcb_r": m["lcb_r"],
            "profit_factor": m["profit_factor"],
            "raw_n": m["raw_n"],
        })

    child_metrics.sort(key=lambda x: (x["expectancy_r"], x["weighted_n"]), reverse=True)

    return {
        "context_count": len(groups),
        "positive_context_ratio": positive_w / max(0.000001, total_w),
        "top_context_concentration": top_w / max(0.000001, total_w),
        "top_contexts": child_metrics[:12],
    }


def classify_family(metrics: Dict[str, Any], ctx: Dict[str, Any], consensus: Dict[str, Any], shadow: Dict[str, Any], promotion: Dict[str, Any]) -> Tuple[str, str, float, List[str]]:
    n = metrics["raw_n"]
    wn = metrics["weighted_n"]
    exp = metrics["expectancy_r"]
    lcbv = metrics["lcb_r"]
    pf = metrics["profit_factor"]
    capture = metrics["capture_efficiency"]
    giveback = metrics["avg_giveback_r"]
    pos_ctx = ctx["positive_context_ratio"]
    concentration = ctx["top_context_concentration"]

    reasons = []

    score = 0.0
    score += min(25.0, wn / 4.0)
    score += max(-35.0, min(35.0, exp * 140.0))
    score += max(-35.0, min(35.0, lcbv * 180.0))
    score += max(-20.0, min(25.0, (pf - 1.0) * 35.0))
    score += max(-15.0, min(20.0, (pos_ctx - 0.50) * 40.0))
    score += max(-10.0, min(12.0, capture * 10.0))
    score -= max(0.0, min(10.0, giveback * 3.0))

    if consensus["conflict"]:
        score -= 20.0
        reasons.append("SOURCE_CONFLICT")

    if concentration > 0.70 and ctx["context_count"] >= 3:
        score -= 8.0
        reasons.append("CONTEXT_TOO_CONCENTRATED")

    sh_n = inum(shadow.get("n"), 0)
    sh_exp = fnum(shadow.get("expectancy_r"), 0.0)
    sh_pf = fnum(shadow.get("profit_factor"), 0.0)
    sh_q = fnum(shadow.get("quality_score"), 0.0)

    if sh_n >= 100:
        score += max(-10.0, min(10.0, sh_exp * 60.0))
        score += max(-5.0, min(8.0, (sh_pf - 1.0) * 8.0))
        score += max(-4.0, min(6.0, sh_q / 15.0))
        reasons.append("SHADOW_REGISTRY_USED")
    else:
        reasons.append("SHADOW_SUPPORT_LOW")

    if promotion:
        if promotion.get("canary_ratio", 0.0) > 0.25:
            score += 5.0
            reasons.append("PROMOTION_POLICY_PARTIAL_SUPPORT")
        if promotion.get("direct_ratio", 0.0) > 0.0:
            score += 4.0
            reasons.append("PROMOTION_DIRECT_SUPPORT")

    if n < 30:
        reasons.append("RAW_SAMPLE_LOW")
    if wn < 25:
        reasons.append("WEIGHTED_SAMPLE_LOW")
    if exp <= 0:
        reasons.append("EXPECTANCY_NOT_POSITIVE")
    if lcbv <= 0:
        reasons.append("LCB_NOT_POSITIVE")
    if pf < 1.05:
        reasons.append("PF_WEAK")
    if capture < 0.35:
        reasons.append("EXIT_CAPTURE_WEAK")
    if giveback > 0.65:
        reasons.append("HIGH_GIVEBACK_FROM_MFE")
    if pos_ctx < 0.50:
        reasons.append("CONTEXT_ROBUSTNESS_WEAK")

    if consensus["conflict"] and n >= 40:
        state = "FAMILY_CONFLICTED"
        rec = "SPLIT_BY_CONTEXT_BEFORE_CANARY"
    elif n >= 80 and wn >= 45 and exp > 0.035 and lcbv > 0.0 and pf >= 1.15 and pos_ctx >= 0.55 and concentration <= 0.75:
        state = "FAMILY_CANARY_READY"
        rec = "CREATE_MICRO_CANARY_CONTRACT"
        reasons.append("FAMILY_EDGE_CANARY_READY")
    elif n >= 40 and wn >= 25 and exp > 0.02 and pf >= 1.05 and pos_ctx >= 0.45:
        state = "FAMILY_RESEARCH_PROMOTABLE"
        rec = "KEEP_SHADOW_AND_PREPARE_CANARY"
        reasons.append("FAMILY_EDGE_PROMISING")
    elif n >= 40 and exp < -0.02 and pf < 0.90:
        state = "FAMILY_TOXIC"
        rec = "DEMOTE_FAMILY_RESEARCH_ONLY"
        reasons.append("FAMILY_NEGATIVE_EDGE")
    else:
        state = "FAMILY_RESEARCH_ONLY"
        rec = "ACCUMULATE_MORE_EVIDENCE"

    return state, rec, round(score, 4), sorted(set(reasons))


def ensure_schema(con: sqlite3.Connection) -> None:
    con.execute("""
    CREATE TABLE IF NOT EXISTS alpha_family_clusters_v6_1(
        family_key TEXT PRIMARY KEY,
        ts TEXT NOT NULL,
        version TEXT NOT NULL,
        symbol TEXT NOT NULL,
        side TEXT NOT NULL,
        family TEXT NOT NULL,
        horizon_min INTEGER NOT NULL,
        raw_n INTEGER NOT NULL,
        weighted_n REAL NOT NULL,
        winrate REAL,
        expectancy_r REAL,
        lcb_r REAL,
        median_r REAL,
        p10_r REAL,
        p90_r REAL,
        profit_factor REAL,
        avg_mfe_r REAL,
        avg_mae_r REAL,
        mfe_mae_ratio REAL,
        capture_efficiency REAL,
        avg_giveback_r REAL,
        context_count INTEGER,
        positive_context_ratio REAL,
        top_context_concentration REAL,
        source_count INTEGER,
        source_agreement REAL,
        source_conflict INTEGER,
        shadow_n INTEGER,
        shadow_expectancy_r REAL,
        shadow_profit_factor REAL,
        shadow_quality_score REAL,
        promotion_canary_ratio REAL,
        promotion_direct_ratio REAL,
        cluster_score REAL,
        cluster_state TEXT NOT NULL,
        recommendation TEXT NOT NULL,
        reasons TEXT NOT NULL,
        payload TEXT NOT NULL
    )
    """)

    con.execute("""
    CREATE TABLE IF NOT EXISTS alpha_family_children_v6_1(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL,
        family_key TEXT NOT NULL,
        child_key TEXT NOT NULL,
        symbol TEXT,
        side TEXT,
        setup TEXT,
        family TEXT,
        regime TEXT,
        session TEXT,
        volatility_bucket TEXT,
        news_bucket TEXT,
        horizon_min INTEGER,
        raw_n INTEGER,
        weighted_n REAL,
        expectancy_r REAL,
        lcb_r REAL,
        profit_factor REAL,
        avg_mfe_r REAL,
        avg_mae_r REAL,
        capture_efficiency REAL,
        payload TEXT NOT NULL
    )
    """)

    con.execute("""
    CREATE TABLE IF NOT EXISTS alpha_family_promotion_contracts_v6_1(
        contract_id TEXT PRIMARY KEY,
        ts TEXT NOT NULL,
        version TEXT NOT NULL,
        family_key TEXT NOT NULL,
        symbol TEXT NOT NULL,
        side TEXT NOT NULL,
        family TEXT NOT NULL,
        horizon_min INTEGER NOT NULL,
        contract_state TEXT NOT NULL,
        recommended_action TEXT NOT NULL,
        allow_micro_canary INTEGER NOT NULL,
        allow_direct_open INTEGER NOT NULL,
        evidence_n INTEGER NOT NULL,
        evidence_weighted_n REAL NOT NULL,
        expectancy_r REAL,
        lcb_r REAL,
        profit_factor REAL,
        quality_score REAL,
        reasons TEXT NOT NULL,
        payload TEXT NOT NULL
    )
    """)

    con.execute("""
    CREATE TABLE IF NOT EXISTS alpha_family_research_audit_v6_1(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL,
        version TEXT NOT NULL,
        event TEXT NOT NULL,
        level TEXT NOT NULL,
        message TEXT NOT NULL,
        payload TEXT NOT NULL
    )
    """)


def run(db_path: str | Path) -> Dict[str, Any]:
    db_path = Path(db_path)
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row

    ensure_schema(con)

    obs = []
    obs.extend(load_resultats_quant_nets(con))
    obs.extend(load_trades(con))
    obs.extend(load_shadow_results(con))

    shadow = load_shadow_registry(con)
    promotion = load_promotion_context(con)

    by_family: Dict[str, List[Obs]] = {}
    by_child: Dict[str, List[Obs]] = {}

    for o in obs:
        by_family.setdefault(o.family_key, []).append(o)
        by_child.setdefault(o.child_key, []).append(o)

    now = utc()

    con.execute("DELETE FROM alpha_family_clusters_v6_1")
    con.execute("DELETE FROM alpha_family_children_v6_1")
    con.execute("DELETE FROM alpha_family_promotion_contracts_v6_1")

    families_out = []

    for child_key, rows in by_child.items():
        m = calc_metrics(rows)
        o = rows[0]
        con.execute("""
        INSERT INTO alpha_family_children_v6_1(
            ts,family_key,child_key,symbol,side,setup,family,regime,session,volatility_bucket,news_bucket,horizon_min,
            raw_n,weighted_n,expectancy_r,lcb_r,profit_factor,avg_mfe_r,avg_mae_r,capture_efficiency,payload
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            now, o.family_key, child_key, o.symbol, o.side, o.setup, o.family, o.regime, o.session,
            o.volatility_bucket, o.news_bucket, o.horizon_min, m["raw_n"], m["weighted_n"],
            m["expectancy_r"], m["lcb_r"], m["profit_factor"], m["avg_mfe_r"], m["avg_mae_r"],
            m["capture_efficiency"], js({"sources": m["sources"]}),
        ))

    for family_key, rows in by_family.items():
        o = rows[0]
        m = calc_metrics(rows)
        ctx = context_robustness(rows)
        cons = source_consensus(rows)
        sh = shadow.get(family_key, {})
        promotion_key = ":".join(family_key.split(":")[:3])
        pol = promotion.get(promotion_key, {})

        state, rec, score, reasons = classify_family(m, ctx, cons, sh, pol)

        contract_state = {
            "FAMILY_CANARY_READY": "CONTRACT_MICRO_CANARY_READY",
            "FAMILY_RESEARCH_PROMOTABLE": "CONTRACT_RESEARCH_PROMOTABLE",
            "FAMILY_CONFLICTED": "CONTRACT_REQUIRES_CONTEXT_SPLIT",
            "FAMILY_TOXIC": "CONTRACT_DEMOTE",
        }.get(state, "CONTRACT_RESEARCH_ONLY")

        allow_micro = 1 if state == "FAMILY_CANARY_READY" else 0
        allow_direct = 0

        contract_id = stable_id(VERSION, family_key, state, round(score, 4))

        payload = {
            "context": ctx,
            "source_consensus": cons,
            "shadow_registry": sh,
            "promotion_context": pol,
            "top_examples": [
                {
                    "source": x.source,
                    "setup": x.setup,
                    "r": x.r,
                    "mfe_r": x.mfe_r,
                    "mae_r": x.mae_r,
                    "context_key": x.context_key,
                    "quality": x.quality,
                }
                for x in rows[:25]
            ],
        }

        con.execute("""
        INSERT OR REPLACE INTO alpha_family_clusters_v6_1(
            family_key,ts,version,symbol,side,family,horizon_min,raw_n,weighted_n,winrate,expectancy_r,lcb_r,
            median_r,p10_r,p90_r,profit_factor,avg_mfe_r,avg_mae_r,mfe_mae_ratio,capture_efficiency,avg_giveback_r,
            context_count,positive_context_ratio,top_context_concentration,source_count,source_agreement,source_conflict,
            shadow_n,shadow_expectancy_r,shadow_profit_factor,shadow_quality_score,promotion_canary_ratio,promotion_direct_ratio,
            cluster_score,cluster_state,recommendation,reasons,payload
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            family_key, now, VERSION, o.symbol, o.side, o.family, o.horizon_min, m["raw_n"], m["weighted_n"],
            m["winrate"], m["expectancy_r"], m["lcb_r"], m["median_r"], m["p10_r"], m["p90_r"],
            m["profit_factor"], m["avg_mfe_r"], m["avg_mae_r"], m["mfe_mae_ratio"], m["capture_efficiency"],
            m["avg_giveback_r"], ctx["context_count"], ctx["positive_context_ratio"], ctx["top_context_concentration"],
            cons["source_count"], cons["agreement"], int(cons["conflict"]), inum(sh.get("n"), 0),
            fnum(sh.get("expectancy_r"), 0.0), fnum(sh.get("profit_factor"), 0.0), fnum(sh.get("quality_score"), 0.0),
            fnum(pol.get("canary_ratio"), 0.0), fnum(pol.get("direct_ratio"), 0.0), score, state, rec, js(reasons), js(payload),
        ))

        con.execute("""
        INSERT OR REPLACE INTO alpha_family_promotion_contracts_v6_1(
            contract_id,ts,version,family_key,symbol,side,family,horizon_min,contract_state,recommended_action,
            allow_micro_canary,allow_direct_open,evidence_n,evidence_weighted_n,expectancy_r,lcb_r,profit_factor,
            quality_score,reasons,payload
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            contract_id, now, VERSION, family_key, o.symbol, o.side, o.family, o.horizon_min, contract_state, rec,
            allow_micro, allow_direct, m["raw_n"], m["weighted_n"], m["expectancy_r"], m["lcb_r"],
            m["profit_factor"], score, js(reasons), js(payload),
        ))

        families_out.append({
            "family_key": family_key,
            "state": state,
            "recommendation": rec,
            "score": score,
            "raw_n": m["raw_n"],
            "weighted_n": m["weighted_n"],
            "expectancy_r": m["expectancy_r"],
            "lcb_r": m["lcb_r"],
            "profit_factor": m["profit_factor"],
            "allow_micro_canary": allow_micro,
            "reasons": reasons,
        })

    states: Dict[str, int] = {}
    for f in families_out:
        states[f["state"]] = states.get(f["state"], 0) + 1

    summary = {
        "version": VERSION,
        "ts": now,
        "db_path": str(db_path),
        "obs_n": len(obs),
        "families_n": len(families_out),
        "states": states,
        "top_canary": [x for x in sorted(families_out, key=lambda y: y["score"], reverse=True) if x["state"] == "FAMILY_CANARY_READY"][:20],
        "top_promotable": [x for x in sorted(families_out, key=lambda y: y["score"], reverse=True) if x["state"] == "FAMILY_RESEARCH_PROMOTABLE"][:20],
        "toxic": [x for x in sorted(families_out, key=lambda y: y["expectancy_r"]) if x["state"] == "FAMILY_TOXIC"][:20],
    }

    con.execute("""
    INSERT INTO alpha_family_research_audit_v6_1(ts,version,event,level,message,payload)
    VALUES(?,?,?,?,?,?)
    """, (now, VERSION, "REFRESH", "INFO", "Alpha family research refreshed", js(summary)))

    con.commit()
    return summary


def x_state(x: Dict[str, Any]) -> str:
    return str(x.get("state") or "")


def render_panel(db_path: str | Path) -> str:
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row

    out = []
    out.append("===== ALPHA FAMILY RESEARCH CORE V6.1 =====")
    out.append("UTC: " + utc())

    if not table_exists(con, "alpha_family_clusters_v6_1"):
        out.append("NO_TABLE alpha_family_clusters_v6_1")
        return "\n".join(out)

    out.append("")
    out.append("===== STATES =====")
    for r in con.execute("""
        SELECT cluster_state, COUNT(*) n,
               ROUND(AVG(expectancy_r),4) avg_exp,
               ROUND(AVG(lcb_r),4) avg_lcb,
               ROUND(AVG(profit_factor),3) avg_pf,
               ROUND(AVG(capture_efficiency),3) avg_capture
        FROM alpha_family_clusters_v6_1
        GROUP BY cluster_state
        ORDER BY n DESC
    """):
        out.append(js(dict(r)))

    out.append("")
    out.append("===== TOP CANARY / PROMOTABLE =====")
    for r in con.execute("""
        SELECT family_key,cluster_state,recommendation,raw_n,
               ROUND(weighted_n,2) weighted_n,
               ROUND(expectancy_r,4) exp,
               ROUND(lcb_r,4) lcb,
               ROUND(profit_factor,3) pf,
               ROUND(capture_efficiency,3) capture,
               ROUND(avg_giveback_r,3) giveback,
               ROUND(positive_context_ratio,3) ctx_pos,
               ROUND(source_agreement,3) source_agreement,
               source_conflict,
               ROUND(cluster_score,2) score,
               reasons
        FROM alpha_family_clusters_v6_1
        WHERE cluster_state IN ('FAMILY_CANARY_READY','FAMILY_RESEARCH_PROMOTABLE','FAMILY_CONFLICTED')
        ORDER BY cluster_score DESC, raw_n DESC
        LIMIT 40
    """):
        out.append(js(dict(r)))

    out.append("")
    out.append("===== TOXIC =====")
    for r in con.execute("""
        SELECT family_key,cluster_state,recommendation,raw_n,
               ROUND(expectancy_r,4) exp,
               ROUND(lcb_r,4) lcb,
               ROUND(profit_factor,3) pf,
               ROUND(cluster_score,2) score,
               reasons
        FROM alpha_family_clusters_v6_1
        WHERE cluster_state='FAMILY_TOXIC'
        ORDER BY expectancy_r ASC, profit_factor ASC
        LIMIT 30
    """):
        out.append(js(dict(r)))

    out.append("")
    out.append("===== CONTRACTS READY =====")
    for r in con.execute("""
        SELECT contract_id,family_key,contract_state,recommended_action,allow_micro_canary,evidence_n,
               ROUND(evidence_weighted_n,2) weighted_n,
               ROUND(expectancy_r,4) exp,
               ROUND(lcb_r,4) lcb,
               ROUND(profit_factor,3) pf,
               ROUND(quality_score,2) quality_score
        FROM alpha_family_promotion_contracts_v6_1
        ORDER BY allow_micro_canary DESC, quality_score DESC, evidence_n DESC
        LIMIT 40
    """):
        out.append(js(dict(r)))

    return "\n".join(out)
