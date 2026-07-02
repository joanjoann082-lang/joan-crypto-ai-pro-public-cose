#!/usr/bin/env python3
from __future__ import annotations

import json, math, sqlite3, hashlib, statistics
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple, Optional

DB = Path("data/joanbot_v14.sqlite")
OUT = Path("data/v20_0_quant_brain")
VERSION = "V20.0_INSTITUTIONAL_QUANT_RESEARCH_BRAIN"

CANDIDATE_TABLES = [
    "institutional_quant_brain_v17_5_1",
    "institutional_promotion_controller_v17_6_1",
    "institutional_micro_canary_contract_queue_v17_6_1",
    "institutional_research_governor_v19_1",
]

OUTCOME_TABLES = [
    "paper_micro_canary_positions_v11",
    "positions",
    "trades",
    "universal_shadow_results_v2",
    "universal_shadow_cases_v2",
]

BRAIN_TABLE = "institutional_quant_research_brain_v20_0"
LEDGER_TABLE = "institutional_alpha_ledger_v20_0"
HEALTH_TABLE = "institutional_quant_brain_health_v20_0"

HARD_TOKENS = [
    "HARD_VETO", "FATAL", "SYSTEM_BLOCK", "RISK_KILL",
    "DB_NOT_OK", "ADAPTER_ERROR", "DRAWDOWN_R_TOO_HIGH",
    "DRAWDOWN_TOO_HIGH"
]

def utc() -> str:
    return datetime.now(timezone.utc).isoformat()

def qid(x: str) -> str:
    return '"' + str(x).replace('"','""') + '"'

def fnum(x: Any, default=None):
    try:
        if x is None or x == "":
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
        (table,)
    ).fetchone() is not None

def cols(con, table: str) -> List[str]:
    if not exists(con, table):
        return []
    return [r[1] for r in con.execute(f"PRAGMA table_info({qid(table)})")]

def rows(con, sql: str, args=()) -> List[Dict[str,Any]]:
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute(sql, args).fetchall()]
    except Exception:
        return []

def one(con, sql: str, args=()) -> Optional[Dict[str,Any]]:
    con.row_factory = sqlite3.Row
    try:
        r = con.execute(sql, args).fetchone()
        return dict(r) if r else None
    except Exception:
        return None

def parse_payload(x: Any) -> Dict[str,Any]:
    if isinstance(x, dict):
        return x
    if not x:
        return {}
    try:
        v = json.loads(str(x))
        return v if isinstance(v, dict) else {}
    except Exception:
        return {}

def flatten_reasons(x: Any) -> List[str]:
    if x is None:
        return []
    if isinstance(x, list):
        return [str(v) for v in x]
    if isinstance(x, dict):
        return [str(k) for k,v in x.items() if v]
    s = str(x)
    try:
        return flatten_reasons(json.loads(s))
    except Exception:
        pass
    out = []
    for p in s.replace("[","").replace("]","").replace('"',"").replace("'","").split(","):
        p = p.strip()
        if p:
            out.append(p)
    return out

def pick(row: Dict[str,Any], payload: Dict[str,Any], *names):
    metrics = payload.get("metrics") if isinstance(payload.get("metrics"), dict) else {}
    for n in names:
        if n in row and row.get(n) is not None:
            return row.get(n)
        if n in metrics and metrics.get(n) is not None:
            return metrics.get(n)
        if n in payload and payload.get(n) is not None:
            return payload.get(n)
    return None

def order_col(c: List[str]) -> str:
    for x in ["ts","created_at","updated_at","opened_at","closed_at","id"]:
        if x in c:
            return x
    return "rowid"

def latest_filter(con, table: str, c: List[str]):
    for tc in ["ts","created_at","updated_at"]:
        if tc in c:
            r = one(con, f"SELECT MAX({qid(tc)}) AS mx FROM {qid(table)}")
            if r and r.get("mx"):
                return f"WHERE {qid(tc)}=?", (r["mx"],)
    return "", ()

def canonical_candidate(row: Dict[str,Any], source_table: str):
    payload = parse_payload(row.get("payload"))

    symbol = pick(row, payload, "symbol","edge_symbol","selected_symbol")
    side = pick(row, payload, "side","edge_side","selected_side")
    setup = pick(row, payload, "setup","edge_setup","selected_setup","family_name")

    if not symbol or not side or not setup:
        return None

    score = fnum(
        pick(row,payload,"brain_score","score","allocation_score","priority","institutional_priority"),
        0.0
    ) or 0.0

    mean_r = fnum(
        pick(row,payload,"robust_mean_r","mean_r","shrunk_mean_r","expectancy_r","posterior_mean_r"),
        0.0
    ) or 0.0

    lcb_r = fnum(
        pick(row,payload,"institutional_lcb_r","lcb95_r","lcb_r","posterior_lcb_r"),
        -1.0
    ) or -1.0

    pf = fnum(pick(row,payload,"profit_factor","pf_cons","pf"), None)

    reasons = []
    for k in [
        "reasons","reason","hard_vetoes","red_flags","state",
        "authority_state","tier","action","queue_state","source_tier"
    ]:
        if row.get(k) is not None:
            reasons += flatten_reasons(row.get(k))

    alpha_key = f"{str(symbol).upper()}|{str(side).upper()}|{str(setup)}"

    return {
        "alpha_key": alpha_key,
        "symbol": str(symbol).upper(),
        "side": str(side).upper(),
        "setup": str(setup),
        "source_table": source_table,
        "score": score,
        "mean_r": mean_r,
        "lcb_r": lcb_r,
        "pf": pf,
        "reasons": sorted(set(reasons)),
        "payload": payload,
    }

def load_candidates(con):
    out = []

    for t in CANDIDATE_TABLES:
        if not exists(con,t):
            continue

        c = cols(con,t)

        if not any(x in c for x in ["symbol","edge_symbol","selected_symbol"]) and "payload" not in c:
            continue

        where,args = latest_filter(con,t,c)
        oc = order_col(c)

        data = rows(
            con,
            f"""
            SELECT *
            FROM {qid(t)}
            {where}
            ORDER BY {qid(oc) if oc != "rowid" else "rowid"} DESC
            LIMIT 150
            """,
            args
        )

        for r in data:
            cc = canonical_candidate(r,t)
            if cc:
                out.append(cc)

    best = {}
    for c in out:
        old = best.get(c["alpha_key"])
        if old is None or (c["score"],c["mean_r"],c["lcb_r"]) > (old["score"],old["mean_r"],old["lcb_r"]):
            best[c["alpha_key"]] = c

    return sorted(best.values(), key=lambda x:(x["score"],x["mean_r"],x["lcb_r"]), reverse=True)

def add_sample(samples, r, w, source):
    rv = fnum(r)
    if rv is not None:
        samples.append({"r": rv, "w": w, "source": source})

def collect_samples(con, cand):
    sym, side, setup = cand["symbol"], cand["side"], cand["setup"]
    samples = []

    for t in OUTCOME_TABLES:
        if not exists(con,t):
            continue

        c = cols(con,t)
        rcol = next((x for x in ["net_pnl_r","pnl_r","result_r","r"] if x in c), None)

        if not rcol:
            continue

        has_sym = "symbol" in c
        has_side = "side" in c
        has_setup = "setup" in c
        oc = order_col(c)
        is_shadow = "shadow" in t

        if has_sym and has_side and has_setup:
            data = rows(
                con,
                f"""
                SELECT {qid(rcol)} AS r
                FROM {qid(t)}
                WHERE UPPER(COALESCE(symbol,''))=?
                  AND UPPER(COALESCE(side,''))=?
                  AND COALESCE(setup,'')=?
                ORDER BY {qid(oc) if oc!="rowid" else "rowid"} DESC
                LIMIT 800
                """,
                (sym,side,setup)
            )

            w = 0.12 if is_shadow else 1.0
            src = "shadow_exact" if is_shadow else "live_exact"

            for rr in data:
                add_sample(samples, rr.get("r"), w, src)

        if is_shadow and has_setup:
            data = rows(
                con,
                f"""
                SELECT {qid(rcol)} AS r
                FROM {qid(t)}
                WHERE COALESCE(setup,'')=?
                ORDER BY {qid(oc) if oc!="rowid" else "rowid"} DESC
                LIMIT 500
                """,
                (setup,)
            )

            for rr in data:
                add_sample(samples, rr.get("r"), 0.018, "weak_family_setup")

        if is_shadow and has_sym and has_side:
            data = rows(
                con,
                f"""
                SELECT {qid(rcol)} AS r
                FROM {qid(t)}
                WHERE UPPER(COALESCE(symbol,''))=?
                  AND UPPER(COALESCE(side,''))=?
                ORDER BY {qid(oc) if oc!="rowid" else "rowid"} DESC
                LIMIT 500
                """,
                (sym,side)
            )

            for rr in data:
                add_sample(samples, rr.get("r"), 0.018, "weak_symbol_side")

    return samples

def weighted_percentile(vals, p):
    if not vals:
        return None
    xs = sorted((r,w) for r,w in vals if w > 0)
    total = sum(w for _,w in xs)

    if total <= 0:
        return None

    target = total * p
    acc = 0.0

    for r,w in xs:
        acc += w
        if acc >= target:
            return r

    return xs[-1][0]

def weighted_tail_mean(vals, p=0.10):
    if not vals:
        return None

    xs = sorted((r,w) for r,w in vals if w > 0)
    total = sum(w for _,w in xs)
    target = total * p

    if total <= 0 or target <= 0:
        return None

    accw = 0.0
    acc = 0.0

    for r,w in xs:
        take = min(w, target - accw)

        if take <= 0:
            break

        acc += r * take
        accw += take

        if accw >= target:
            break

    return acc / accw if accw > 0 else None

def posterior(samples, brain_mean):
    vals = []
    mix = {}

    for s in samples:
        r = fnum(s.get("r"))
        w = fnum(s.get("w"), 0.0) or 0.0
        src = str(s.get("source") or "unknown")

        if r is None or w <= 0:
            continue

        r = max(-5.0, min(5.0, r))
        vals.append((r,w,src))
        mix[src] = mix.get(src,0.0) + w

    if not vals:
        return {
            "n_raw": 0,
            "n_eff": 0.0,
            "live_eff": 0.0,
            "shadow_eff": 0.0,
            "mean_r": None,
            "median_r": None,
            "shrunk_mean_r": 0.0,
            "lcb95_r": -1.0,
            "prob_edge": 0.5,
            "q_value": 1.0,
            "cvar10_r": -1.0,
            "pf_cons": None,
            "payoff_cons": None,
            "stability": 0.0,
            "source_mix": {},
        }

    n_eff = sum(w for _,w,_ in vals)
    live_eff = sum(w for _,w,s in vals if "live" in s or "trade" in s)
    shadow_eff = sum(w for _,w,s in vals if "shadow" in s)

    v2 = [(r,w) for r,w,_ in vals]
    mean = sum(r*w for r,w in v2) / max(n_eff,1e-9)
    med = weighted_percentile(v2,0.5)
    p25 = weighted_percentile(v2,0.25)
    p75 = weighted_percentile(v2,0.75)

    iqr = (p75-p25) if p25 is not None and p75 is not None else 1.0
    robust_sd = max(abs(iqr)/1.349,0.05)

    prior_mean = max(-0.05, min(0.05, brain_mean * 0.25))

    prior_strength = 30.0
    if live_eff >= 3:
        prior_strength = 18.0
    if live_eff >= 8:
        prior_strength = 10.0

    shrunk = (mean*n_eff + prior_mean*prior_strength) / (n_eff + prior_strength)

    model_risk = 0.035
    model_risk += 0.080 * (1.0 - clamp(live_eff/8.0))
    model_risk += 0.030 * clamp(shadow_eff/max(n_eff,1e-9))

    se = robust_sd / math.sqrt(max(n_eff,1.0))
    total_se = math.sqrt(se*se + model_risk*model_risk)

    lcb = shrunk - 1.96 * total_se
    prob = norm_cdf(shrunk / max(total_se,1e-9))
    cvar = weighted_tail_mean(v2,0.10)

    wins = [(r,w) for r,w in v2 if r > 0.10]
    losses = [(abs(r),w) for r,w in v2 if r < -0.10]

    pf = None
    payoff = None

    if losses:
        gw = sum(r*w for r,w in wins)
        gl = sum(r*w for r,w in losses)
        if gl > 0:
            pf = (gw*0.75) / (gl*1.25)

    if wins and losses:
        aw = weighted_percentile(wins,0.25)
        al = weighted_percentile(losses,0.75)
        if aw is not None and al and al > 0:
            payoff = aw/al

    raw = [r for r,_,_ in vals]
    stability = 0.0

    if len(raw) >= 12:
        half = len(raw)//2
        recent = raw[:half]
        old = raw[half:]
        stability = clamp(1.0 - abs(statistics.mean(recent)-statistics.mean(old))/0.75)

    return {
        "n_raw": len(raw),
        "n_eff": n_eff,
        "live_eff": live_eff,
        "shadow_eff": shadow_eff,
        "mean_r": mean,
        "median_r": med,
        "shrunk_mean_r": shrunk,
        "lcb95_r": lcb,
        "prob_edge": prob,
        "q_value": 1.0,
        "cvar10_r": cvar,
        "pf_cons": pf,
        "payoff_cons": payoff,
        "stability": stability,
        "source_mix": mix,
    }

def add_q_values(items):
    m = len(items)

    if not m:
        return

    order = sorted(range(m), key=lambda i: 1.0-items[i]["posterior"]["prob_edge"])
    prev = 1.0
    q = [1.0] * m

    for rev_rank,idx in enumerate(reversed(order),1):
        rank = m-rev_rank+1
        p = 1.0-items[idx]["posterior"]["prob_edge"]
        prev = min(prev,p*m/max(rank,1))
        q[idx] = min(1.0,prev)

    for i,v in enumerate(q):
        items[i]["posterior"]["q_value"] = v

def market_regime(con, symbol):
    t = "market_snapshots"

    if not exists(con,t):
        return {"regime":"UNKNOWN","data_quality":0.0}

    c = cols(con,t)

    sym_col = "symbol" if "symbol" in c else None
    price_col = next((x for x in ["price","close","last","mark_price","mid_price"] if x in c),None)
    ts_col = next((x for x in ["ts","created_at","time"] if x in c),None)

    if not price_col:
        return {"regime":"UNKNOWN","data_quality":0.1}

    where = ""
    args = ()

    if sym_col:
        where = f"WHERE UPPER(COALESCE({qid(sym_col)},''))=?"
        args = (symbol,)

    oc = ts_col or "rowid"

    data = rows(
        con,
        f"""
        SELECT {qid(price_col)} AS p
        FROM {qid(t)}
        {where}
        ORDER BY {qid(oc) if oc!="rowid" else "rowid"} DESC
        LIMIT 160
        """,
        args
    )

    ps = [fnum(r.get("p")) for r in data]
    ps = [p for p in ps if p and p > 0]

    if len(ps) < 20:
        return {"regime":"UNKNOWN","data_quality":0.2,"n":len(ps)}

    ret1 = ps[0]/ps[1]-1 if len(ps)>1 else 0
    ret20 = ps[0]/ps[min(20,len(ps)-1)]-1

    rets = [
        ps[i]/ps[i+1]-1
        for i in range(min(len(ps)-1,80))
        if ps[i+1] > 0
    ]

    vol = sum(abs(x) for x in rets) / max(len(rets),1)

    trend = "UP" if ret20 > 0.01 else "DOWN" if ret20 < -0.01 else "RANGE"
    volstate = "HIGHVOL" if vol > 0.004 else "LOWVOL"

    return {
        "regime": f"{trend}_{volstate}",
        "data_quality": clamp(len(ps)/120.0),
        "n": len(ps),
        "price": ps[0],
        "ret1": ret1,
        "ret20": ret20,
        "vol_abs": vol,
    }

def decision(cand, post, reg):
    reasons = []
    rtxt = "|".join(str(x).upper() for x in cand.get("reasons",[]))

    if any(t in rtxt for t in HARD_TOKENS):
        reasons.append("HARD_REASON_PRESENT")
    if cand["score"] < 45:
        reasons.append("BRAIN_SCORE_LOW")
    if cand["mean_r"] < -0.05:
        reasons.append("BRAIN_MEAN_NEGATIVE")
    if post["n_eff"] < 20:
        reasons.append("EVIDENCE_N_TOO_LOW")
    if post["prob_edge"] < 0.68:
        reasons.append("PROB_EDGE_LOW")
    if post["q_value"] > 0.45:
        reasons.append("FDR_Q_HIGH")
    if post["lcb95_r"] < -0.35:
        reasons.append("LCB_TOO_NEGATIVE")
    if post["cvar10_r"] is not None and post["cvar10_r"] < -1.8:
        reasons.append("CVAR_TOO_BAD")
    if post["pf_cons"] is not None and post["pf_cons"] < 0.75:
        reasons.append("PF_CONSERVATIVE_LOW")
    if reg.get("data_quality",0) < 0.2:
        reasons.append("MARKET_DATA_WEAK")

    score = 0.0
    score += 18 * clamp(cand["score"]/70.0)
    score += 18 * clamp((post["prob_edge"]-0.50)/0.35)
    score += 18 * clamp((post["lcb95_r"]+0.35)/0.55)
    score += 14 * clamp((post["shrunk_mean_r"]+0.03)/0.20)
    score += 10 * clamp((post["cvar10_r"]+2.0)/2.5 if post["cvar10_r"] is not None else 0)
    score += 8 * clamp(post["n_eff"]/80.0)
    score += 6 * clamp(post["stability"])
    score += 4 * clamp(reg.get("data_quality",0))

    state = "BLOCKED"
    action = "RESEARCH_ONLY"

    if not reasons and score >= 62:
        state = "VALIDATED_RESEARCH_EDGE"
        action = "ELIGIBLE_FOR_CONTRACT_REVIEW"
    elif not any(r in reasons for r in ["HARD_REASON_PRESENT","CVAR_TOO_BAD","PF_CONSERVATIVE_LOW"]) and score >= 52 and post["prob_edge"] >= 0.62:
        state = "PROMISING_RESEARCH_EDGE"
        action = "WATCHLIST_MORE_LIVE_EVIDENCE"

    return {
        "state": state,
        "action": action,
        "quant_score": round(score,2),
        "reasons": reasons,
    }

def create_tables(con):
    con.execute(f"""
        CREATE TABLE IF NOT EXISTS {qid(LEDGER_TABLE)} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            version TEXT NOT NULL,
            alpha_key TEXT,
            symbol TEXT,
            side TEXT,
            setup TEXT,
            source_table TEXT,
            n_raw INTEGER,
            n_eff REAL,
            live_eff REAL,
            shadow_eff REAL,
            mean_r REAL,
            median_r REAL,
            shrunk_mean_r REAL,
            lcb95_r REAL,
            prob_edge REAL,
            q_value REAL,
            cvar10_r REAL,
            pf_cons REAL,
            payoff_cons REAL,
            stability REAL,
            regime TEXT,
            source_mix TEXT,
            payload TEXT
        )
    """)

    con.execute(f"""
        CREATE TABLE IF NOT EXISTS {qid(BRAIN_TABLE)} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            version TEXT NOT NULL,
            alpha_key TEXT,
            symbol TEXT,
            side TEXT,
            setup TEXT,
            state TEXT,
            action TEXT,
            quant_score REAL,
            brain_score REAL,
            n_eff REAL,
            live_eff REAL,
            prob_edge REAL,
            q_value REAL,
            lcb95_r REAL,
            cvar10_r REAL,
            pf_cons REAL,
            regime TEXT,
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
            candidates INTEGER,
            validated INTEGER,
            promising INTEGER,
            blocked INTEGER,
            summary TEXT,
            payload TEXT
        )
    """)

def run():
    if not DB.exists():
        raise RuntimeError("DB_MISSING")

    OUT.mkdir(parents=True, exist_ok=True)

    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row

    create_tables(con)

    qc = con.execute("PRAGMA quick_check").fetchone()[0]
    candidates = load_candidates(con)

    packs = []

    for cand in candidates:
        sam = collect_samples(con,cand)
        post = posterior(sam,cand["mean_r"])
        reg = market_regime(con,cand["symbol"])
        packs.append({"candidate":cand,"posterior":post,"regime":reg})

    add_q_values(packs)

    rows_out = []
    counts = {"validated":0,"promising":0,"blocked":0}

    for x in packs:
        cand = x["candidate"]
        post = x["posterior"]
        reg = x["regime"]
        dec = decision(cand,post,reg)

        if dec["state"] == "VALIDATED_RESEARCH_EDGE":
            counts["validated"] += 1
        elif dec["state"] == "PROMISING_RESEARCH_EDGE":
            counts["promising"] += 1
        else:
            counts["blocked"] += 1

        payload = {
            "candidate": cand,
            "posterior": post,
            "regime": reg,
            "decision": dec,
        }

        con.execute(f"""
            INSERT INTO {qid(LEDGER_TABLE)}
            (ts,version,alpha_key,symbol,side,setup,source_table,n_raw,n_eff,live_eff,shadow_eff,
             mean_r,median_r,shrunk_mean_r,lcb95_r,prob_edge,q_value,cvar10_r,pf_cons,
             payoff_cons,stability,regime,source_mix,payload)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            utc(),VERSION,cand["alpha_key"],cand["symbol"],cand["side"],cand["setup"],cand["source_table"],
            post["n_raw"],post["n_eff"],post["live_eff"],post["shadow_eff"],post["mean_r"],post["median_r"],
            post["shrunk_mean_r"],post["lcb95_r"],post["prob_edge"],post["q_value"],post["cvar10_r"],
            post["pf_cons"],post["payoff_cons"],post["stability"],reg.get("regime"),
            json.dumps(post.get("source_mix"),sort_keys=True),
            json.dumps(payload,sort_keys=True)
        ))

        con.execute(f"""
            INSERT INTO {qid(BRAIN_TABLE)}
            (ts,version,alpha_key,symbol,side,setup,state,action,quant_score,brain_score,n_eff,
             live_eff,prob_edge,q_value,lcb95_r,cvar10_r,pf_cons,regime,reasons,payload)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            utc(),VERSION,cand["alpha_key"],cand["symbol"],cand["side"],cand["setup"],
            dec["state"],dec["action"],dec["quant_score"],cand["score"],post["n_eff"],post["live_eff"],
            post["prob_edge"],post["q_value"],post["lcb95_r"],post["cvar10_r"],post["pf_cons"],
            reg.get("regime"),",".join(dec["reasons"]),json.dumps(payload,sort_keys=True)
        ))

        rows_out.append({
            **dec,
            "symbol": cand["symbol"],
            "side": cand["side"],
            "setup": cand["setup"],
            "brain_score": cand["score"],
            "n_eff": post["n_eff"],
            "live_eff": post["live_eff"],
            "prob": post["prob_edge"],
            "q": post["q_value"],
            "lcb": post["lcb95_r"],
            "cvar": post["cvar10_r"],
            "pf": post["pf_cons"],
            "regime": reg.get("regime"),
        })

    rows_out.sort(
        key=lambda r: (
            r["state"]=="VALIDATED_RESEARCH_EDGE",
            r["state"]=="PROMISING_RESEARCH_EDGE",
            r["quant_score"]
        ),
        reverse=True
    )

    summary = (
        "VALIDATED_PRESENT" if counts["validated"]
        else "PROMISING_ONLY" if counts["promising"]
        else "NO_VALIDATED_EDGE"
    )

    report = {
        "version": VERSION,
        "utc": utc(),
        "db": qc,
        "candidates": len(candidates),
        **counts,
        "summary": summary,
        "top": rows_out[:30],
    }

    con.execute(f"""
        INSERT INTO {qid(HEALTH_TABLE)}
        (ts,version,db_quick_check,candidates,validated,promising,blocked,summary,payload)
        VALUES (?,?,?,?,?,?,?,?,?)
    """, (
        utc(),VERSION,qc,len(candidates),counts["validated"],counts["promising"],
        counts["blocked"],summary,json.dumps(report,sort_keys=True)
    ))

    con.commit()
    con.close()

    (OUT/"v20_quant_brain_report.json").write_text(json.dumps(report,indent=2,sort_keys=True))

    lines = [
        "# V20.0 Institutional Quant Research Brain",
        "",
        f"- UTC: `{report['utc']}`",
        f"- DB: `{qc}`",
        f"- Candidates: `{len(candidates)}`",
        f"- Validated: `{counts['validated']}`",
        f"- Promising: `{counts['promising']}`",
        f"- Blocked: `{counts['blocked']}`",
        f"- Summary: `{summary}`",
        "",
        "## Top ranked alpha lanes",
    ]

    for i,r in enumerate(rows_out[:25],1):
        lines.append(
            f"- #{i:02d} `{r['state']}` {r['symbol']} {r['side']} {r['setup']} "
            f"qScore={r['quant_score']} prob={r['prob']:.3f} q={r['q']:.3f} "
            f"n={r['n_eff']:.1f} live={r['live_eff']:.1f} "
            f"lcb={r['lcb']:.4f} cvar={r['cvar']} pfC={r['pf']} "
            f"regime={r['regime']} reasons={','.join(r['reasons'])}"
        )

    (OUT/"v20_quant_brain_summary.md").write_text("\n".join(lines))

    return report

def main():
    rep = run()

    print("===== V20.0 INSTITUTIONAL QUANT RESEARCH BRAIN =====")
    print("db:", rep["db"])
    print("candidates:", rep["candidates"])
    print("validated:", rep["validated"])
    print("promising:", rep["promising"])
    print("blocked:", rep["blocked"])
    print("summary:", rep["summary"])
    print("summary_file: data/v20_0_quant_brain/v20_quant_brain_summary.md")

    for i,r in enumerate(rep["top"][:16],1):
        print(
            f"#{i:02d} {r['state']} qScore={r['quant_score']} "
            f"{r['symbol']} {r['side']} {r['setup']} "
            f"prob={r['prob']:.3f} q={r['q']:.3f} "
            f"n={r['n_eff']:.1f} live={r['live_eff']:.1f} "
            f"lcb={r['lcb']:.4f} cvar={r['cvar']} pfC={r['pf']} "
            f"reasons={','.join(r['reasons'])[:160]}"
        )

    return 0

if __name__ == "__main__":
    raise SystemExit(main())
