#!/usr/bin/env python3
from __future__ import annotations

import argparse, hashlib, json, math, os, shutil, sqlite3, statistics, time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

DB = Path("data/joanbot_v14.sqlite")
OUT = Path("data/v21_0_quant_kernel")
LOCK = OUT / "v21_0_kernel.lock"
LOCKDIR = OUT / "v21_0_kernel.lockdir"

VERSION = "V21.1_ADAPTER_CANONICAL_INSTITUTIONAL_QUANT_KERNEL"

MARKET = "institutional_market_data_latest_v18_9"
MARKET_HEALTH = "institutional_market_data_health_v18_9"
BRAIN = "institutional_quant_brain_v17_5_1"
INTENTS = "institutional_quant_canary_execution_intents_v17_7_2"
PAPER = "paper_micro_canary_positions_v11"
POSITIONS = "positions"
PAYOFF18 = "institutional_payoff_intelligence_v18_6"
PAYOFF20 = "institutional_payoff_intelligence_v20_4"

KERNEL = "institutional_quant_kernel_decisions_v21_0"
MEMORY = "institutional_quant_kernel_memory_v21_0"
EMISSIONS = "institutional_quant_kernel_emissions_v21_0"
HEALTH = "institutional_quant_kernel_health_v21_0"

HARD = [
    "HARD_VETO_PRESENT", "HARD_REASON_PRESENT",
    "LCB_STRUCTURALLY_NEGATIVE", "LCB_TOO_NEGATIVE",
    "POSTERIOR_LCB95_TOO_NEGATIVE",
    "SIZE_MULT_OUT_OF_BOUNDS", "INVALID_REQUEST_MODE",
]

STRUCTURAL_NEG = [
    "BRAIN_SCORE_TOO_LOW", "BRAIN_MEAN_TOO_LOW",
    "PROB_EDGE_TOO_LOW", "FDR_Q_TOO_HIGH",
    "ALLOC_SCORE_TOO_LOW", "PF_CONSERVATIVE_TOO_LOW",
]

POSITIVE = [
    "APPROVED_BY_V19_1_RESEARCH_GOVERNOR",
    "APPROVED_MICRO_RESEARCH_CANARY",
    "POSITIVE_EDGE_NEEDS_LIVE_EVIDENCE",
    "SHADOW_AUTHORITY_READY",
    "REVIEW_MICRO_CANARY_CONTRACT",
    "ROBUST_MEAN_POSITIVE",
    "TRACEABILITY_OK",
]

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def qid(x):
    return '"' + str(x).replace('"', '""') + '"'

def fnum(x, default=None):
    try:
        if x is None or x == "":
            return default
        return float(str(x).replace(",", ""))
    except Exception:
        return default

def clamp(x, lo, hi):
    return max(lo, min(hi, x))

def sigmoid(x):
    try:
        return 1.0 / (1.0 + math.exp(-x))
    except Exception:
        return 0.5

def parse_ts(ts):
    if not ts:
        return None
    try:
        d = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.astimezone(timezone.utc)
    except Exception:
        return None

def age_min(ts):
    d = parse_ts(ts)
    if not d:
        return None
    return max(0.0, (datetime.now(timezone.utc)-d).total_seconds()/60.0)

def connect(write=True):
    """
    V21.0.3-PROSAFE SQLite connector.

    Rules:
    - Use plain absolute filesystem path, not malformed file: URI.
    - Compatible with Termux /storage/emulated/0.
    - WAL + busy_timeout for multi-service paper bot.
    - Fail loudly with diagnostic context.
    """
    candidates = []

    try:
        env_db = os.environ.get("JOANBOT_DB")
        if env_db:
            candidates.append(Path(env_db))
    except Exception:
        pass

    candidates.append(DB)
    candidates.append(Path.cwd() / DB)
    candidates.append(Path("/storage/emulated/0/Download/joan_crypto_ai_pro_v14/data/joanbot_v14.sqlite"))

    db_path = None
    for c in candidates:
        try:
            cc = c if c.is_absolute() else (Path.cwd() / c)
            cc = cc.resolve()
            if cc.exists():
                db_path = cc
                break
        except Exception:
            continue

    if db_path is None:
        checked = []
        for c in candidates:
            try:
                checked.append(str((c if c.is_absolute() else Path.cwd() / c).resolve()))
            except Exception:
                checked.append(str(c))
        raise sqlite3.OperationalError("DB_NOT_FOUND checked=" + repr(checked))

    if not db_path.parent.exists():
        raise sqlite3.OperationalError(f"DB_PARENT_NOT_FOUND: {db_path.parent}")

    try:
        con = sqlite3.connect(str(db_path), timeout=120, isolation_level=None)
    except sqlite3.OperationalError as e:
        size = db_path.stat().st_size if db_path.exists() else None
        raise sqlite3.OperationalError(
            f"DB_OPEN_FAILED path={db_path} exists={db_path.exists()} size={size} err={e}"
        )

    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=120000")

    try:
        con.execute("PRAGMA journal_mode=WAL")
    except Exception:
        pass

    try:
        con.execute("PRAGMA synchronous=NORMAL")
    except Exception:
        pass

    try:
        con.execute("PRAGMA temp_store=MEMORY")
    except Exception:
        pass

    return con


def exists(con, table):
    return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None

def cols(con, table):
    if not exists(con, table):
        return set()
    return {r[1] for r in con.execute(f"PRAGMA table_info({qid(table)})")}

def table_info(con, table):
    if not exists(con, table):
        return []
    return [dict(r) for r in con.execute(f"PRAGMA table_info({qid(table)})").fetchall()]

def rows(con, sql, args=()):
    try:
        return [dict(r) for r in con.execute(sql, args).fetchall()]
    except Exception:
        return []

def one(con, sql, args=()):
    r = rows(con, sql, args)
    return r[0] if r else {}

def retry(fn, tries=10):
    delay = 0.20
    last = None
    for _ in range(tries):
        try:
            return fn()
        except sqlite3.OperationalError as e:
            last = e
            if "locked" not in str(e).lower():
                raise
            time.sleep(delay)
            delay = min(3.0, delay * 1.7)
    raise last

def ensure(con):
    con.execute(f"""
    CREATE TABLE IF NOT EXISTS {qid(MEMORY)} (
        key TEXT PRIMARY KEY,
        ts TEXT, version TEXT,
        symbol TEXT, side TEXT, setup TEXT,
        n INTEGER, win_n INTEGER, loss_n INTEGER,
        mean_r REAL, median_r REAL, stdev_r REAL,
        lcb95_r REAL, cvar10_r REAL, max_dd_r REAL,
        profit_factor REAL, payoff_ratio REAL,
        posterior_prob_win REAL, posterior_exp_r REAL,
        sample_quality TEXT, payload TEXT
    )
    """)

    con.execute(f"""
    CREATE TABLE IF NOT EXISTS {qid(KERNEL)} (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT, version TEXT,
        state TEXT, action TEXT,
        symbol TEXT, side TEXT, setup TEXT,
        brain_score REAL, quant_score REAL, fdr_q REAL,
        posterior_exp_r REAL, lcb95_r REAL, cvar10_r REAL,
        profit_factor REAL, posterior_prob_win REAL,
        market_alignment REAL, data_confidence REAL,
        global_payoff_penalty REAL,
        size_mult REAL,
        reasons TEXT, payload TEXT
    )
    """)

    con.execute(f"""
    CREATE TABLE IF NOT EXISTS {qid(EMISSIONS)} (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT, version TEXT,
        emission_hash TEXT UNIQUE,
        intent_id INTEGER,
        symbol TEXT, side TEXT, setup TEXT,
        size_mult REAL,
        action TEXT, status TEXT,
        reasons TEXT, payload TEXT
    )
    """)

    con.execute(f"""
    CREATE TABLE IF NOT EXISTS {qid(HEALTH)} (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT, version TEXT,
        quick_check TEXT,
        market_summary TEXT,
        payoff_health TEXT,
        brain_rows INTEGER,
        decisions INTEGER,
        ready INTEGER,
        emitted INTEGER,
        verdict TEXT,
        problems TEXT,
        payload TEXT
    )
    """)

def blob(r):
    return " ".join(str(v or "") for v in r.values()).upper()

def has(r, tokens):
    b = blob(r)
    return any(t in b for t in tokens)

def brain_score(r):
    vals = [
        fnum(r.get("brain_score")),
        fnum(r.get("allocation_score")),
        fnum(r.get("score")),
        fnum(r.get("institutional_priority")),
        fnum(r.get("priority")),
    ]
    return max([v for v in vals if v is not None] or [0.0])

def latest_brain(con):
    if not exists(con, BRAIN):
        return []
    cs = cols(con, BRAIN)

    order_col = None
    for c in ["brain_score", "allocation_score", "score", "institutional_priority", "priority"]:
        if c in cs:
            order_col = c
            break
    order = f"COALESCE({qid(order_col)},0) DESC" if order_col else ("id DESC" if "id" in cs else "rowid DESC")

    if "ts" in cs:
        ts = one(con, f"SELECT MAX(ts) ts FROM {qid(BRAIN)}").get("ts")
        rs = rows(con, f"SELECT * FROM {qid(BRAIN)} WHERE ts=? ORDER BY {order} LIMIT 160", (ts,))
        if rs:
            return rs

    return rows(con, f"SELECT * FROM {qid(BRAIN)} ORDER BY {order} LIMIT 160")

def load_market(con):
    if not exists(con, MARKET):
        return {}
    rs = rows(con, f"SELECT metric,value,status,age_min,source,source_detail,ts FROM {qid(MARKET)}")
    return {r["metric"]: r for r in rs}

def mval(m, k, default=None):
    return fnum((m.get(k) or {}).get("value"), default)

def mst(m, k):
    return str((m.get(k) or {}).get("status") or "MISS").upper()

def market_health(con):
    if not exists(con, MARKET_HEALTH):
        return {}
    return one(con, f"""
        SELECT ts, version, summary, live_count, stale_count, miss_count, invalid_count, error_count
        FROM {qid(MARKET_HEALTH)}
        ORDER BY id DESC LIMIT 1
    """)

def payoff(con):
    for t in [PAYOFF20, PAYOFF18]:
        if exists(con, t):
            r = one(con, f"SELECT * FROM {qid(t)} ORDER BY id DESC LIMIT 1")
            if r:
                return r
    return {}

def pnl_r(r):
    v = fnum(r.get("net_pnl_r"))
    if v is None:
        v = fnum(r.get("pnl_r"))
    if v is None:
        v = fnum(r.get("gross_r"))
    if v is None:
        usd = fnum(r.get("net_pnl_usd"), fnum(r.get("pnl_usd")))
        risk = fnum(r.get("risk_usd"))
        if usd is not None and risk and risk > 0:
            v = usd / risk
    return v

def closed_trades(con):
    out = []
    for t in [PAPER, POSITIONS]:
        if not exists(con, t):
            continue
        cs = cols(con, t)
        wh = []
        if "status" in cs:
            wh.append("UPPER(COALESCE(status,'')) IN ('CLOSED','DONE','EXITED')")
        if "closed_at" in cs:
            wh.append("closed_at IS NOT NULL")
        if not wh:
            continue
        order = "closed_at ASC" if "closed_at" in cs else ("id ASC" if "id" in cs else "rowid ASC")
        for r in rows(con, f"SELECT * FROM {qid(t)} WHERE {' OR '.join(wh)} ORDER BY {order} LIMIT 1000"):
            r["_table"] = t
            out.append(r)
    return out

def setup_key(sym, side, setup):
    return f"{sym}|{side}|{setup}"

def max_drawdown(vals):
    eq = 0.0
    peak = 0.0
    dd = 0.0
    for v in vals:
        eq += v
        peak = max(peak, eq)
        dd = min(dd, eq - peak)
    return dd

def stats(vals):
    vals = [float(v) for v in vals if v is not None]
    n = len(vals)
    if n == 0:
        return dict(
            n=0, win_n=0, loss_n=0, mean_r=0.0, median_r=0.0, stdev_r=1.0,
            lcb95_r=-0.50, cvar10_r=-1.00, max_dd_r=0.0,
            profit_factor=None, payoff_ratio=None,
            posterior_prob_win=0.50, posterior_exp_r=-0.04,
            sample_quality="NO_SAMPLE"
        )

    wins = [v for v in vals if v > 0]
    losses = [v for v in vals if v < 0]
    mean = statistics.mean(vals)
    median = statistics.median(vals)
    stdev = statistics.pstdev(vals) if n >= 2 else 1.0

    z = 2.35 if n < 10 else 2.10 if n < 25 else 1.96
    lcb = mean - z * stdev / math.sqrt(max(n, 1))

    tail_n = max(1, math.ceil(0.10 * n))
    cvar = statistics.mean(sorted(vals)[:tail_n])

    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    pf = gross_win / gross_loss if gross_loss > 0 else None

    mw = statistics.mean(wins) if wins else 0.0
    ml = abs(statistics.mean(losses)) if losses else 0.0
    payoff_ratio = mw / ml if ml > 0 else None

    # Jeffreys prior + shrinkage to slightly negative prior
    pwin = (len(wins) + 0.5) / (n + 1.0)
    prior = -0.04
    k = 30.0
    shrink = n / (n + k)
    post_exp = shrink * mean + (1.0 - shrink) * prior

    if n < 5:
        sq = "NOISY_SAMPLE"
    elif n < 15:
        sq = "LOW_SAMPLE"
    elif n < 40:
        sq = "MEDIUM_SAMPLE"
    else:
        sq = "INSTITUTIONAL_SAMPLE"

    return dict(
        n=n, win_n=len(wins), loss_n=len(losses),
        mean_r=mean, median_r=median, stdev_r=stdev,
        lcb95_r=lcb, cvar10_r=cvar, max_dd_r=max_drawdown(vals),
        profit_factor=pf, payoff_ratio=payoff_ratio,
        posterior_prob_win=pwin, posterior_exp_r=post_exp,
        sample_quality=sq
    )

def build_memory(con):
    grouped = {}
    for r in closed_trades(con):
        sym = str(r.get("symbol") or "").upper()
        side = str(r.get("side") or "").upper()
        setup = str(r.get("setup") or "")
        v = pnl_r(r)
        if sym and side and setup and v is not None:
            grouped.setdefault(setup_key(sym, side, setup), []).append(float(v))

    for k, vals in grouped.items():
        sym, side, setup = k.split("|", 2)
        s = stats(vals)
        con.execute(f"""
        INSERT OR REPLACE INTO {qid(MEMORY)}
        (key, ts, version, symbol, side, setup, n, win_n, loss_n, mean_r, median_r,
         stdev_r, lcb95_r, cvar10_r, max_dd_r, profit_factor, payoff_ratio,
         posterior_prob_win, posterior_exp_r, sample_quality, payload)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            k, now_iso(), VERSION, sym, side, setup,
            s["n"], s["win_n"], s["loss_n"], s["mean_r"], s["median_r"],
            s["stdev_r"], s["lcb95_r"], s["cvar10_r"], s["max_dd_r"],
            s["profit_factor"], s["payoff_ratio"], s["posterior_prob_win"],
            s["posterior_exp_r"], s["sample_quality"],
            json.dumps({"returns": vals[-250:], **s}, sort_keys=True)
        ))

def memory(con, sym, side, setup):
    if not exists(con, MEMORY):
        return stats([])
    r = one(con, f"SELECT * FROM {qid(MEMORY)} WHERE key=?", (setup_key(sym, side, setup),))
    if not r:
        return stats([])
    return {
        "n": int(fnum(r.get("n"), 0) or 0),
        "win_n": int(fnum(r.get("win_n"), 0) or 0),
        "loss_n": int(fnum(r.get("loss_n"), 0) or 0),
        "mean_r": fnum(r.get("mean_r"), 0.0),
        "median_r": fnum(r.get("median_r"), 0.0),
        "stdev_r": fnum(r.get("stdev_r"), 1.0),
        "lcb95_r": fnum(r.get("lcb95_r"), -0.5),
        "cvar10_r": fnum(r.get("cvar10_r"), -1.0),
        "max_dd_r": fnum(r.get("max_dd_r"), 0.0),
        "profit_factor": fnum(r.get("profit_factor")),
        "payoff_ratio": fnum(r.get("payoff_ratio")),
        "posterior_prob_win": fnum(r.get("posterior_prob_win"), 0.5),
        "posterior_exp_r": fnum(r.get("posterior_exp_r"), -0.04),
        "sample_quality": r.get("sample_quality") or "NO_SAMPLE",
    }

def data_confidence(market, health):
    summary = str(health.get("summary") or "").upper()
    live = fnum(health.get("live_count"), 0) or 0
    miss = fnum(health.get("miss_count"), 0) or 0
    invalid = fnum(health.get("invalid_count"), 0) or 0

    core = [
        "BTC_PRICE","ETH_PRICE","BTC_FUNDING","ETH_FUNDING","BTC_OI","ETH_OI",
        "BTC_CVD","ETH_CVD","BTC_LIQUIDATIONS","ETH_LIQUIDATIONS",
        "VIX","DXY","NASDAQ","NASDAQ_CHANGE","US10Y","FEAR_GREED"
    ]

    ok = sum(1 for k in core if mst(market, k) == "LIVE")
    conf = ok / len(core)
    if summary != "OK":
        conf -= 0.15
    conf -= 0.05 * miss
    conf -= 0.15 * invalid
    return clamp(conf, 0.0, 1.0)

def market_alignment(sym, side, market):
    coin = "BTC" if sym == "BTCUSDT" else "ETH"
    sign = 1 if side == "LONG" else -1

    chg = mval(market, f"{coin}_CHANGE_24H", 0.0) or 0.0
    cvd = mval(market, f"{coin}_CVD", 0.0) or 0.0
    funding = mval(market, f"{coin}_FUNDING", 0.0) or 0.0
    long_short = mval(market, f"{coin}_LONG_SHORT", 1.0) or 1.0
    liq = mval(market, f"{coin}_LIQUIDATIONS", 0.0) or 0.0
    fear = mval(market, "FEAR_GREED", 50.0) or 50.0
    vix = mval(market, "VIX", 18.0) or 18.0
    nas = mval(market, "NASDAQ_CHANGE", 0.0) or 0.0
    dxy = mval(market, "DXY", 101.0) or 101.0

    s = 0.0
    s += sign * clamp(chg, -4, 4) * 3.5
    s += sign * clamp(cvd / 650000.0, -1, 1) * 8.0
    s += -sign * clamp(funding / 0.00020, -1, 1) * 3.5

    if long_short > 2.2:
        s += -sign * 4.0
    elif long_short < 0.9:
        s += sign * 3.0

    if fear < 25:
        s += -sign * 3.5
    elif fear > 70:
        s += sign * 2.0

    if vix > 20:
        s += -sign * 2.5
    elif vix < 15:
        s += sign * 1.5

    s += sign * clamp(nas, -3, 3) * 1.2

    if dxy > 102:
        s += -sign * 1.5

    # Liquidation total 0 és neutre; spikes poden indicar capitulació, però sense direcció exacta no sobreponderem.
    if liq > 5_000_000:
        s += 1.0

    return round(clamp(s, -22.0, 22.0), 4)

def payoff_penalty(p):
    health = str(p.get("payoff_health") or "").upper()
    n = fnum(p.get("closed_n"), 0) or 0
    lcb = fnum(p.get("lcb95_r"), -0.2) or -0.2
    pf = fnum(p.get("profit_factor"), None)

    pen = 0.0
    if health == "BAD":
        pen -= 6.0
    if n < 20:
        pen -= 4.0
    elif n < 40:
        pen -= 2.0
    if lcb <= 0:
        pen -= 4.0
    if pf is None or pf < 1.10:
        pen -= 3.0
    return pen

def evaluate(con, r, market, mh, p):
    sym = str(r.get("symbol") or "").upper()
    side = str(r.get("side") or "").upper()
    setup = str(r.get("setup") or "")

    mem = memory(con, sym, side, setup)
    bscore = brain_score(r)

    bmean = fnum(r.get("robust_mean_r"), fnum(r.get("mean_r"), 0.0)) or 0.0
    blcb = fnum(r.get("institutional_lcb_r"), fnum(r.get("lcb95_r"), 0.0)) or 0.0
    bpf = fnum(r.get("profit_factor"), None)
    prob = fnum(r.get("prob_edge_gt_zero"), fnum(r.get("prob"), None))
    bq = fnum(r.get("fdr_q"), fnum(r.get("q"), None))

    fatal = has(r, HARD)
    structural = has(r, STRUCTURAL_NEG)
    positive = has(r, POSITIVE)

    align = market_alignment(sym, side, market)
    dconf = data_confidence(market, mh)
    ppen = payoff_penalty(p)

    reasons = []
    if fatal:
        reasons.append("FATAL_HARD_TOKEN")
    if structural:
        reasons.append("STRUCTURAL_NEGATIVE_TOKEN")
    if positive:
        reasons.append("POSITIVE_RESEARCH_TAG")
    if mem["n"] < 5:
        reasons.append("LOW_SETUP_MEMORY")
    if mem["n"] >= 5 and mem["lcb95_r"] < -0.45:
        reasons.append("SETUP_LCB_DAMAGED")
    if mem["cvar10_r"] < -1.30:
        reasons.append("TAIL_RISK_HIGH")
    if dconf < 0.90:
        reasons.append("DATA_CONFIDENCE_NOT_FULL")
    if ppen < 0:
        reasons.append("GLOBAL_PAYOFF_CONSERVATIVE")
    if bq is not None and bq > 0.35:
        reasons.append("BRAIN_FDR_HIGH")
    if prob is not None and prob < 0.55:
        reasons.append("BRAIN_PROB_LOW")

    # Score institucional: combina cervell, memòria, cua, mercat i qualitat de dades.
    qscore = 30.0
    qscore += clamp(bscore, 0, 100) * 0.34
    qscore += clamp(bmean * 45.0, -10, 14)
    qscore += clamp(blcb * 28.0, -12, 10)

    if bpf is not None:
        qscore += clamp((bpf - 1.0) * 10.0, -8, 10)
    if prob is not None:
        qscore += clamp((prob - 0.50) * 24.0, -8, 8)
    if bq is not None:
        qscore -= clamp((bq - 0.18) * 28.0, 0, 10)

    if mem["n"] >= 3:
        qscore += clamp(mem["posterior_exp_r"] * 65.0, -12, 16)
        qscore += clamp(mem["lcb95_r"] * 25.0, -14, 10)
        qscore += clamp(mem["cvar10_r"] * 8.0, -12, 3)
        if mem["profit_factor"] is not None:
            qscore += clamp((mem["profit_factor"] - 1.0) * 8.0, -6, 8)
        qscore += clamp((mem["posterior_prob_win"] - 0.50) * 20.0, -5, 6)
    else:
        qscore -= 4.0

    qscore += align
    qscore += dconf * 7.0
    qscore += ppen

    if positive:
        qscore += 4.0
    if structural:
        qscore -= 8.0
    if fatal:
        qscore -= 120.0

    qscore = round(qscore, 4)

    # State.
    if fatal:
        state = "BLOCK_FATAL"
        action = "NO_EMIT"
    elif mem["n"] >= 5 and mem["lcb95_r"] < -0.45:
        state = "BLOCK_DAMAGED_SETUP_MEMORY"
        action = "NO_EMIT"
    elif qscore >= 68 and dconf >= 0.90:
        state = "ADAPTER_READY_MICRO_CANARY"
        action = "EMIT_MICRO_CANARY"
    elif qscore >= 57 and dconf >= 0.90:
        state = "RESEARCH_CANARY_CANDIDATE"
        action = "EMIT_MICRO_CANARY"
    elif qscore >= 43:
        state = "WATCHLIST"
        action = "WATCHLIST"
    else:
        state = "BLOCK_QUANT_EDGE_INSUFFICIENT"
        action = "NO_EMIT"

    # Size: micro paper-only. Penalitza cua, mostra baixa i payoff global.
    base = 0.0035 + clamp((qscore - 55.0) / 35.0, 0, 1) * 0.0045
    if mem["n"] < 5:
        base *= 0.75
    if mem["cvar10_r"] < -1.0:
        base *= 0.80
    if ppen < 0:
        base *= 0.85
    size = round(clamp(base, 0.0035, 0.0085), 6)

    return {
        "state": state,
        "action": action,
        "symbol": sym,
        "side": side,
        "setup": setup,
        "brain_score": bscore,
        "quant_score": qscore,
        "fdr_q": None,
        "posterior_exp_r": mem["posterior_exp_r"],
        "lcb95_r": mem["lcb95_r"],
        "cvar10_r": mem["cvar10_r"],
        "profit_factor": mem["profit_factor"],
        "posterior_prob_win": mem["posterior_prob_win"],
        "market_alignment": align,
        "data_confidence": dconf,
        "global_payoff_penalty": ppen,
        "size_mult": size,
        "reasons": reasons,
        "payload": {
            "candidate": r,
            "memory": mem,
            "brain_inputs": {
                "brain_score": bscore, "brain_mean": bmean, "brain_lcb": blcb,
                "brain_pf": bpf, "brain_prob": prob, "brain_q": bq,
            },
            "market_alignment": align,
            "data_confidence": dconf,
            "payoff": p,
            "method": "Bayesian shrinkage + conservative LCB + CVaR + FDR + market alignment + adapter contract gate",
        }
    }

def apply_fdr(decisions):
    ranked = sorted(decisions, key=lambda d: d["quant_score"], reverse=True)
    m = max(1, len(ranked))
    for i, d in enumerate(ranked, 1):
        confidence = sigmoid((d["quant_score"] - 50.0) / 10.0)
        q = min(1.0, (i / m) * (1.0 - confidence) * 2.0)
        d["fdr_q"] = round(q, 4)
        if d["action"] == "EMIT_MICRO_CANARY" and q > 0.40:
            d["state"] = "WATCHLIST_FDR_CONTROLLED"
            d["action"] = "WATCHLIST"
            d["reasons"].append("FDR_CONTROLLED_NO_EMIT")
    return ranked

def open_positions(con):
    out = []
    for t in [PAPER, POSITIONS]:
        if not exists(con, t):
            continue
        cs = cols(con, t)
        wh = []
        if "status" in cs:
            wh.append("UPPER(COALESCE(status,'')) LIKE 'OPEN%'")
            wh.append("UPPER(COALESCE(status,'')) IN ('ACTIVE','RUNNING','OPEN_MANAGED')")
        if "closed_at" in cs and "opened_at" in cs:
            wh.append("(closed_at IS NULL AND opened_at IS NOT NULL)")
        if wh:
            out += rows(con, f"SELECT * FROM {qid(t)} WHERE {' OR '.join(wh)} LIMIT 5")
    return out

def pending_intents(con):
    if not exists(con, INTENTS) or "adapter_status" not in cols(con, INTENTS):
        return []
    return rows(con, f"""
        SELECT *
        FROM {qid(INTENTS)}
        WHERE UPPER(COALESCE(adapter_status,'')) IN ('PENDING_ADAPTER_BINDING','PENDING','NEW')
           OR (
                UPPER(COALESCE(intent_state,'')) LIKE '%PENDING%'
                AND UPPER(COALESCE(adapter_status,'')) NOT LIKE '%REJECT%'
                AND UPPER(COALESCE(adapter_status,'')) NOT LIKE '%OPENED%'
           )
        ORDER BY id DESC LIMIT 10
    """)

def adapter_anchor(con):
    if not exists(con, INTENTS):
        return None
    cs = cols(con, INTENTS)
    if "requested_mode" not in cs or "execution_permission" not in cs:
        return None
    r = one(con, f"""
        SELECT *
        FROM {qid(INTENTS)}
        WHERE (
              UPPER(COALESCE(adapter_status,'')) LIKE '%OPENED%'
           OR UPPER(COALESCE(intent_state,'')) LIKE '%ADAPTER_BOUND%'
        )
        AND requested_mode IS NOT NULL
        AND execution_permission IS NOT NULL
        ORDER BY id DESC LIMIT 1
    """)
    return r if r else None

def recent_emits(con, minutes):
    cutoff = datetime.fromtimestamp(time.time() - minutes*60, tz=timezone.utc).isoformat()
    return one(con, f"SELECT COUNT(*) n FROM {qid(EMISSIONS)} WHERE ts>=? AND action='EMIT_MICRO_CANARY'", (cutoff,)).get("n") or 0

def insert_dynamic(con, table, values):
    meta = table_info(con, table)
    names, vals = [], []
    for m in meta:
        name = m["name"]
        typ = str(m["type"] or "").upper()
        if m["pk"] and "INT" in typ:
            continue

        if name in values:
            val = values[name]
        elif m["dflt_value"] is not None:
            continue
        elif m["notnull"]:
            if "INT" in typ:
                val = 0
            elif "REAL" in typ or "FLOA" in typ or "DOUB" in typ:
                val = 0.0
            else:
                val = "V21_0_DEFAULT"
        else:
            continue

        names.append(name)
        vals.append(val)

    if not names:
        raise RuntimeError("NO_INSERTABLE_COLUMNS")

    sql = f"INSERT INTO {qid(table)} ({','.join(qid(n) for n in names)}) VALUES ({','.join('?' for _ in names)})"
    con.execute(sql, vals)
    return con.execute("SELECT last_insert_rowid()").fetchone()[0]

def emit(con, decision):
    if not exists(con, INTENTS):
        return {"status": "NO_EMIT", "reason": "INTENTS_TABLE_MISSING"}

    if open_positions(con):
        return {"status": "NO_EMIT", "reason": "OPEN_POSITION_EXISTS"}

    if pending_intents(con):
        return {"status": "NO_EMIT", "reason": "PENDING_INTENT_EXISTS"}

    if recent_emits(con, 24*60) >= 3:
        return {"status": "NO_EMIT", "reason": "DAILY_LIMIT_3"}

    if recent_emits(con, 45) >= 1:
        return {"status": "NO_EMIT", "reason": "COOLDOWN_45M"}

    anchor = adapter_anchor(con)
    if not anchor:
        return {"status": "NO_EMIT", "reason": "NO_SUCCESSFUL_ADAPTER_ANCHOR"}

    h = hashlib.sha256(
        f"{VERSION}:{decision['symbol']}:{decision['side']}:{decision['setup']}:{decision['quant_score']}:{now_iso()}".encode()
    ).hexdigest()[:32]

    payload = {
        "version": VERSION,
        "decision": decision,
        "anchor": {
            "id": anchor.get("id"),
            "requested_mode": anchor.get("requested_mode"),
            "execution_permission": anchor.get("execution_permission"),
        },
        "safety": {
            "paper_only": True,
            "no_real_execution": True,
            "max_daily_emits": 3,
            "cooldown_min": 45,
            "size_mult_cap": [0.0035, 0.0085],
        }
    }

    values = {
        "ts": now_iso(),
        "version": VERSION,
        "intent_hash": h,
        "key": f"V21::{decision['symbol']}::{decision['side']}::{decision['setup']}::{h[:10]}",
        "intent_state": "MAX_QUANT_MANUAL_APPROVED_PENDING_PAPER_ADAPTER",
        "adapter_status": "PENDING_ADAPTER_BINDING",
        "requested_mode": anchor.get("requested_mode"),
        "execution_permission": anchor.get("execution_permission"),
        "symbol": decision["symbol"],
        "side": decision["side"],
        "setup": decision["setup"],
        "profile": "INSTITUTIONAL_MICRO_RESEARCH_CANARY",
        "horizon_min": 60,
        "requested_size_mult": decision["size_mult"],
        "recommended_size_mult": decision["size_mult"],
        "size_mult": decision["size_mult"],
        "size_multiplier": decision["size_mult"],
        "requested_size_usd": round(decision["size_mult"] * 100000, 2),
        "institutional_priority": decision["quant_score"],
        "priority": decision["quant_score"],
        "source_tier": "V21_1_ADAPTER_CANONICAL_QUANT_KERNEL",
        "source": "V21_1_ADAPTER_CANONICAL_QUANT_KERNEL",
        "reason": "V21_POSTERIOR_LCB_CVAR_FDR_MARKET_ALIGNED_PAPER_CANARY",
        "reasons": json.dumps(decision["reasons"], sort_keys=True),
        "payload": json.dumps(payload, sort_keys=True, default=str),
    }

    intent_id = insert_dynamic(con, INTENTS, values)

    con.execute(f"""
    INSERT INTO {qid(EMISSIONS)}
    (ts, version, emission_hash, intent_id, symbol, side, setup, size_mult, action, status, reasons, payload)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        now_iso(), VERSION, h, intent_id,
        decision["symbol"], decision["side"], decision["setup"], decision["size_mult"],
        "EMIT_MICRO_CANARY", "PENDING_ADAPTER",
        json.dumps(decision["reasons"], sort_keys=True),
        json.dumps(payload, sort_keys=True, default=str),
    ))

    return {"status": "EMITTED", "intent_id": intent_id, "hash": h}

def write_decisions(con, decisions):
    for d in decisions:
        con.execute(f"""
        INSERT INTO {qid(KERNEL)}
        (ts, version, state, action, symbol, side, setup, brain_score, quant_score, fdr_q,
         posterior_exp_r, lcb95_r, cvar10_r, profit_factor, posterior_prob_win,
         market_alignment, data_confidence, global_payoff_penalty, size_mult, reasons, payload)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            now_iso(), VERSION, d["state"], d["action"], d["symbol"], d["side"], d["setup"],
            d["brain_score"], d["quant_score"], d["fdr_q"],
            d["posterior_exp_r"], d["lcb95_r"], d["cvar10_r"], d["profit_factor"],
            d["posterior_prob_win"], d["market_alignment"], d["data_confidence"],
            d["global_payoff_penalty"], d["size_mult"],
            json.dumps(d["reasons"], sort_keys=True),
            json.dumps(d["payload"], sort_keys=True, default=str),
        ))


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except Exception:
        return False

class PortableKernelLock:
    """
    Termux-safe lock.
    Avoids fcntl.flock because /storage/emulated/0 can return Errno 38.
    Uses atomic mkdir + pid file + stale-lock recovery.
    """
    def __init__(self):
        self.acquired = False

    def __enter__(self):
        OUT.mkdir(parents=True, exist_ok=True)

        try:
            os.mkdir(LOCKDIR)
            (LOCKDIR / "pid").write_text(str(os.getpid()))
            (LOCKDIR / "ts").write_text(now_iso())
            self.acquired = True
            return True
        except FileExistsError:
            pid = None
            try:
                pid = int((LOCKDIR / "pid").read_text().strip())
            except Exception:
                pid = None

            if pid and _pid_alive(pid):
                self.acquired = False
                return False

            # Stale lock: previous process died.
            try:
                shutil.rmtree(LOCKDIR, ignore_errors=True)
                os.mkdir(LOCKDIR)
                (LOCKDIR / "pid").write_text(str(os.getpid()))
                (LOCKDIR / "ts").write_text(now_iso())
                self.acquired = True
                return True
            except FileExistsError:
                self.acquired = False
                return False

    def __exit__(self, *args):
        if self.acquired:
            shutil.rmtree(LOCKDIR, ignore_errors=True)
        return False


def run(emit_enabled=False):
    OUT.mkdir(parents=True, exist_ok=True)

    with PortableKernelLock() as locked:
        if not locked:
            print("V21_KERNEL_ALREADY_RUNNING")
            return 0

        con = connect(write=True)

        def work():
            ensure(con)
            qc = con.execute("PRAGMA quick_check").fetchone()[0]
            build_memory(con)

            market = load_market(con)
            mh = market_health(con)
            p = payoff(con)
            brain = latest_brain(con)

            candidates = []
            for r in brain:
                sym = str(r.get("symbol") or "").upper()
                side = str(r.get("side") or "").upper()
                setup = str(r.get("setup") or "")
                if sym in ("BTCUSDT","ETHUSDT") and side in ("LONG","SHORT") and setup:
                    candidates.append(evaluate(con, r, market, mh, p))

            decisions = apply_fdr(candidates)[:60]
            write_decisions(con, decisions)

            ready = [d for d in decisions if d["action"] == "EMIT_MICRO_CANARY"]
            emission = {"status": "NOT_REQUESTED"}
            if emit_enabled:
                emission = emit(con, ready[0]) if ready else {"status": "NO_EMIT", "reason": "NO_READY_DECISION"}

            problems = []
            if qc != "ok":
                problems.append("DB_QUICK_CHECK_NOT_OK")
            if str(mh.get("summary","")).upper() != "OK":
                problems.append("MARKET_HEALTH_NOT_OK")
            if not brain:
                problems.append("NO_BRAIN_ROWS")
            if emit_enabled and emission.get("status") not in ("EMITTED","NO_EMIT"):
                problems.append("EMISSION_UNKNOWN")

            verdict = "OK_KERNEL_ACTIVE" if not problems else "DEGRADED_KERNEL_ACTIVE"

            con.execute(f"""
            INSERT INTO {qid(HEALTH)}
            (ts, version, quick_check, market_summary, payoff_health, brain_rows,
             decisions, ready, emitted, verdict, problems, payload)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                now_iso(), VERSION, qc, mh.get("summary"), p.get("payoff_health"),
                len(brain), len(decisions), len(ready),
                1 if emission.get("status") == "EMITTED" else 0,
                verdict, json.dumps(problems, sort_keys=True),
                json.dumps({
                    "emission": emission,
                    "top_decisions": decisions[:20],
                    "market_health": mh,
                    "payoff": p,
                }, sort_keys=True, default=str)
            ))

            report = {
                "utc": now_iso(),
                "version": VERSION,
                "quick_check": qc,
                "market_health": mh,
                "payoff": p,
                "brain_rows": len(brain),
                "decisions": len(decisions),
                "ready": len(ready),
                "emission": emission,
                "verdict": verdict,
                "problems": problems,
                "top_decisions": [
                    {
                        "state": d["state"],
                        "action": d["action"],
                        "symbol": d["symbol"],
                        "side": d["side"],
                        "setup": d["setup"],
                        "brain_score": d["brain_score"],
                        "quant_score": d["quant_score"],
                        "fdr_q": d["fdr_q"],
                        "posterior_exp_r": d["posterior_exp_r"],
                        "lcb95_r": d["lcb95_r"],
                        "cvar10_r": d["cvar10_r"],
                        "pf": d["profit_factor"],
                        "align": d["market_alignment"],
                        "data_conf": d["data_confidence"],
                        "size": d["size_mult"],
                        "reasons": d["reasons"],
                    }
                    for d in decisions[:20]
                ]
            }

            (OUT / "kernel_report.json").write_text(json.dumps(report, indent=2, sort_keys=True, default=str))

            print(f"===== {VERSION} =====")
            print("quick_check:", qc)
            print("market_health:", mh.get("summary"), "live=", mh.get("live_count"), "miss=", mh.get("miss_count"), "invalid=", mh.get("invalid_count"))
            print("payoff:", p.get("payoff_health"), "closed=", p.get("closed_n"), "lcb=", p.get("lcb95_r"), "pf=", p.get("profit_factor"))
            print("brain_rows:", len(brain), "decisions:", len(decisions), "ready:", len(ready))
            print("emission:", emission)
            print("verdict:", verdict, "problems:", problems)
            print("")
            print("## TOP DECISIONS")
            for d in report["top_decisions"][:15]:
                print(
                    f"{d['state']} | {d['symbol']} {d['side']} {d['setup']} "
                    f"q={d['quant_score']} fdr={d['fdr_q']} brain={d['brain_score']} "
                    f"post={d['posterior_exp_r']:.4f} lcb={d['lcb95_r']:.4f} "
                    f"cvar={d['cvar10_r']:.4f} pf={d['pf']} align={d['align']} "
                    f"size={d['size']} action={d['action']} reasons={','.join(d['reasons'][:5])}"
                )

            return 0 if qc == "ok" else 2

        try:
            return retry(work)
        finally:
            con.close()

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--emit", action="store_true")
    args = ap.parse_args()
    return run(emit_enabled=args.emit)

if __name__ == "__main__":
    raise SystemExit(main())
