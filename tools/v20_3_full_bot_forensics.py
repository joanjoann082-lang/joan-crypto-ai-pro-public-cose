#!/usr/bin/env python3
from __future__ import annotations

import json, os, re, sqlite3, subprocess, time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

DB = Path("data/joanbot_v14.sqlite")
OUT = Path("data/v20_3_bot_check")
VERSION = "V20.3_FULL_BOT_FORENSICS_READ_ONLY"

TABLES = {
    "market": "institutional_market_data_latest_v18_9",
    "market_health": "institutional_market_data_health_v18_9",
    "liq": "institutional_liquidation_rollup_latest_v18_10",
    "brain": "institutional_quant_brain_v17_5_1",
    "promotion": "institutional_promotion_controller_v17_6_1",
    "queue": "institutional_micro_canary_contract_queue_v17_6_1",
    "intents": "institutional_quant_canary_execution_intents_v17_7_2",
    "adapter_health": "institutional_paper_canary_adapter_health_v17_8_1",
    "paper": "paper_micro_canary_positions_v11",
    "positions": "positions",
    "payoff": "institutional_payoff_intelligence_v18_6",
    "contract": "institutional_contract_governor_health_v19_2",
}

SERVICES = {
    "DATA_PLANE": "run_v18_9_1_data_plane|semantic_data_plane",
    "LIQUIDATIONS": "run_v18_10_liquidation_collector|v18_10_liquidation_collector",
    "QUANT_BRAIN": "run_v17_5_1_quant_brain|quant_brain_v17_5_1",
    "PROMOTION": "run_v17_6_1_promotion_controller|promotion_controller",
    "CONTRACT_GOVERNOR": "run_v19_2_contract_governor|contract_governor",
    "PAPER_ADAPTER": "run_v17_8_1_paper_canary_adapter|paper_canary_adapter",
    "MARKET_CONTEXT": "run_v18_2_market_context|market_context",
    "PAYOFF": "run_v18_6_payoff_intelligence|payoff_intelligence",
}

LOGS = {
    "data_plane": "data/v18_9_1_data_plane/gateway_stderr.log",
    "liq": "data/v18_10_liquidations/collector_stderr.log",
    "brain": "data/v17_5_1/quant_brain_stderr.log",
    "promotion": "data/v17_6_1/promotion_controller_stderr.log",
    "contract": "data/v19_2_contract_governor/governor_stderr.log",
    "adapter": "data/v17_8_1/adapter_stderr.log",
    "market": "data/v18_2_market_context/market_context_stderr.log",
    "payoff": "data/v18_6_payoff/payoff_stderr.log",
}

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def qid(x):
    return '"' + str(x).replace('"','""') + '"'

def fnum(x, default=None):
    try:
        if x is None or x == "":
            return default
        return float(str(x).replace(",", ""))
    except Exception:
        return default

def parse_ts(ts):
    if not ts:
        return None
    try:
        d = datetime.fromisoformat(str(ts).replace("Z","+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.astimezone(timezone.utc)
    except Exception:
        return None

def age_min(ts):
    d = parse_ts(ts)
    if not d:
        return None
    return max(0.0, (datetime.now(timezone.utc) - d).total_seconds() / 60.0)

def fmt(v, nd=2):
    z = fnum(v)
    if z is None:
        return "N/A"
    if abs(z) >= 1_000_000:
        return f"{z/1_000_000:.2f}M"
    if abs(z) >= 10_000:
        return f"{z:,.0f}"
    return f"{z:.{nd}f}"

def shell(cmd):
    try:
        return subprocess.check_output(cmd, shell=True, text=True, stderr=subprocess.STDOUT, timeout=8)
    except Exception as e:
        return str(e)

def ps_count(pattern):
    out = shell(f"ps -ef | grep -Ei '{pattern}' | grep -v grep || true")
    lines = [x for x in out.splitlines() if x.strip()]
    return len(lines), lines[:4]

def connect():
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=10)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA busy_timeout=10000")
    except Exception:
        pass
    return con

def exists(con, table):
    return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None

def cols(con, table):
    if not exists(con, table):
        return set()
    return {r[1] for r in con.execute(f"PRAGMA table_info({qid(table)})")}

def rows(con, sql, args=()):
    try:
        return [dict(r) for r in con.execute(sql, args).fetchall()]
    except Exception:
        return []

def one(con, sql, args=()):
    r = rows(con, sql, args)
    return r[0] if r else {}

def max_ts(con, table):
    if not exists(con, table) or "ts" not in cols(con, table):
        return None
    return one(con, f"SELECT MAX(ts) ts FROM {qid(table)}").get("ts")

def count_recent(con, table, hours=12):
    if not exists(con, table) or "ts" not in cols(con, table):
        return None
    cutoff = datetime.fromtimestamp(time.time() - hours*3600, tz=timezone.utc).isoformat()
    return one(con, f"SELECT COUNT(*) n FROM {qid(table)} WHERE ts >= ?", (cutoff,)).get("n")

def table_count(con, table):
    if not exists(con, table):
        return None
    return one(con, f"SELECT COUNT(*) n FROM {qid(table)}").get("n")

def status_counts(con, table, col):
    if not exists(con, table) or col not in cols(con, table):
        return {}
    rs = rows(con, f"SELECT {qid(col)} k, COUNT(*) n FROM {qid(table)} GROUP BY {qid(col)} ORDER BY n DESC")
    return {str(r["k"]): r["n"] for r in rs}

def recent_errors(path, n=80):
    p = Path(path)
    if not p.exists():
        return {"exists": False, "hits": 0, "tail": []}
    txt = "\n".join(p.read_text(errors="ignore").splitlines()[-n:])
    keys = ["Traceback", "Error", "ERROR", "OperationalError", "IntegrityError", "locked", "no column", "Exception"]
    hits = sum(len(re.findall(k, txt, flags=re.I)) for k in keys)
    tail = [x for x in txt.splitlines() if re.search("|".join(keys), x, re.I)][-12:]
    return {"exists": True, "hits": hits, "tail": tail}

def latest_by_id(con, table, limit=5):
    if not exists(con, table):
        return []
    cs = cols(con, table)
    order = "id DESC" if "id" in cs else "rowid DESC"
    return rows(con, f"SELECT * FROM {qid(table)} ORDER BY {order} LIMIT {limit}")

def load_market(con):
    t = TABLES["market"]
    if not exists(con, t):
        return {}
    rs = rows(con, f"SELECT metric,value,status,age_min,source,source_detail,ts FROM {qid(t)}")
    return {r["metric"]: r for r in rs}

def load_brain(con):
    t = TABLES["brain"]
    if not exists(con, t):
        return []
    ts = max_ts(con, t)
    if ts:
        rs = rows(con, f"SELECT * FROM {qid(t)} WHERE ts=? ORDER BY COALESCE(brain_score,0) DESC LIMIT 80", (ts,))
        if rs:
            return rs
    return latest_by_id(con, t, 80)

def score(r):
    return fnum(r.get("brain_score"), fnum(r.get("score"), 0.0)) or 0.0

def top_brain_summary(brain):
    out = []
    for r in sorted(brain, key=score, reverse=True)[:12]:
        out.append({
            "symbol": r.get("symbol"),
            "side": r.get("side"),
            "setup": r.get("setup"),
            "state": r.get("authority_state") or r.get("state"),
            "score": round(score(r), 2),
            "mean": fnum(r.get("robust_mean_r")),
            "lcb": fnum(r.get("institutional_lcb_r")),
            "pf": fnum(r.get("profit_factor")),
            "reasons": str(r.get("reasons") or "")[:160],
        })
    return out

def side_bias(brain):
    res = {}
    for sym in ["BTCUSDT", "ETHUSDT"]:
        rs = [r for r in brain if str(r.get("symbol") or "").upper() == sym]
        l = max([score(r) for r in rs if str(r.get("side") or "").upper()=="LONG"] or [0])
        s = max([score(r) for r in rs if str(r.get("side") or "").upper()=="SHORT"] or [0])
        res[sym] = {"long": round(l,2), "short": round(s,2), "bias": "SHORT" if s>l else "LONG" if l>s else "FLAT"}
    return res

def position_summary(con):
    out = {"open": [], "closed_recent": [], "closed_n": 0, "net_r": 0.0, "net_usd": 0.0}
    for t in [TABLES["paper"], TABLES["positions"]]:
        if not exists(con, t):
            continue
        cs = cols(con, t)
        order = "id DESC" if "id" in cs else "rowid DESC"

        if "status" in cs:
            op = rows(con, f"""
                SELECT * FROM {qid(t)}
                WHERE UPPER(COALESCE(status,'')) IN ('OPEN','ACTIVE','RUNNING','OPEN_MANAGED')
                   OR (closed_at IS NULL AND opened_at IS NOT NULL)
                ORDER BY {order} LIMIT 5
            """)
        else:
            op = []
        for r in op:
            r["_table"] = t
            out["open"].append(r)

        wh = ""
        if "status" in cs:
            wh = "WHERE UPPER(COALESCE(status,'')) IN ('CLOSED','DONE','EXITED')"
        elif "closed_at" in cs:
            wh = "WHERE closed_at IS NOT NULL"
        cl = rows(con, f"SELECT * FROM {qid(t)} {wh} ORDER BY {order} LIMIT 20")
        for r in cl:
            r["_table"] = t
            out["closed_recent"].append(r)

    for r in out["closed_recent"]:
        out["closed_n"] += 1
        out["net_r"] += fnum(r.get("net_pnl_r"), fnum(r.get("pnl_r"), fnum(r.get("gross_r"), 0.0))) or 0.0
        out["net_usd"] += fnum(r.get("net_pnl_usd"), fnum(r.get("pnl_usd"), 0.0)) or 0.0
    out["net_r"] = round(out["net_r"], 4)
    out["net_usd"] = round(out["net_usd"], 4)
    return out

def infer_verdict(report):
    problems = []

    dead = [k for k,v in report["services"].items() if v["count"] == 0]
    if dead:
        problems.append("DEAD_SERVICES:" + ",".join(dead))

    h = report.get("health", {})
    if str(h.get("summary","")).startswith("BAD"):
        problems.append("BAD_DATA_PLANE")
    if fnum(h.get("invalid_count"),0) > 0:
        problems.append("INVALID_MARKET_DATA")
    if fnum(h.get("miss_count"),0) > 0:
        problems.append("MISSING_MARKET_DATA")

    for name, info in report["table_activity"].items():
        age = info.get("age_min")
        if age is None:
            continue
        limit = {
            "market": 3,
            "market_health": 3,
            "liq": 3,
            "brain": 15,
            "promotion": 15,
            "adapter_health": 5,
            "payoff": 5,
        }.get(name, 60)
        if age > limit:
            problems.append(f"STALE_{name.upper()}:{age:.1f}m")

    if report["logs_error_hits_total"] > 0:
        problems.append("RECENT_LOG_ERRORS")

    payoff = report.get("payoff", {})
    if str(payoff.get("payoff_health","")).upper() == "BAD":
        problems.append("PAYOFF_BAD")

    if not problems:
        return "OK_RUN_FOR_HOURS", []
    if all(p.startswith("PAYOFF") or p.startswith("RECENT_LOG_ERRORS") for p in problems):
        return "DEGRADED_BUT_RUNNING", problems
    return "NEEDS_FIX_BEFORE_LONG_RUN", problems

def main():
    OUT.mkdir(parents=True, exist_ok=True)
    report = {
        "version": VERSION,
        "utc": now_iso(),
        "db": {},
        "services": {},
        "table_activity": {},
        "health": {},
        "market_key": {},
        "liq_rollup": [],
        "brain": {},
        "promotion": {},
        "queue": {},
        "intents": {},
        "adapter": {},
        "positions": {},
        "payoff": {},
        "contract": {},
        "logs": {},
        "logs_error_hits_total": 0,
    }

    print(f"===== {VERSION} =====")
    print("UTC:", report["utc"])

    if not DB.exists():
        print("DB_MISSING")
        return 2

    for name, pattern in SERVICES.items():
        cnt, lines = ps_count(pattern)
        report["services"][name] = {"count": cnt, "sample": lines}

    con = connect()
    qc = con.execute("PRAGMA quick_check").fetchone()[0]
    report["db"] = {"quick_check": qc, "size_mb": round(DB.stat().st_size/1024/1024,2)}

    for name, table in TABLES.items():
        info = {
            "table": table,
            "exists": exists(con, table),
            "count": table_count(con, table),
            "latest_ts": max_ts(con, table),
            "recent_12h": count_recent(con, table, 12),
        }
        info["age_min"] = age_min(info["latest_ts"])
        report["table_activity"][name] = info

    report["health"] = one(con, f"""
        SELECT ts, version, summary, live_count, stale_count, miss_count, invalid_count, error_count
        FROM {qid(TABLES['market_health'])}
        ORDER BY id DESC LIMIT 1
    """) if exists(con, TABLES["market_health"]) else {}

    m = load_market(con)
    for k in [
        "BTC_PRICE","ETH_PRICE","BTC_CHANGE_24H","ETH_CHANGE_24H",
        "BTC_FUNDING","ETH_FUNDING","BTC_OI","ETH_OI",
        "BTC_LONG_SHORT","ETH_LONG_SHORT","BTC_CVD","ETH_CVD",
        "BTC_LIQUIDATIONS","ETH_LIQUIDATIONS",
        "VIX","DXY","NASDAQ","NASDAQ_CHANGE","US10Y","FEAR_GREED"
    ]:
        r = m.get(k) or {}
        report["market_key"][k] = {
            "value": r.get("value"),
            "status": r.get("status"),
            "age_min": r.get("age_min"),
            "source": r.get("source"),
        }

    if exists(con, TABLES["liq"]):
        report["liq_rollup"] = rows(con, f"""
            SELECT symbol, connection_state, events_15m, total_15m_usd,
                   long_liq_15m_usd, short_liq_15m_usd, ts
            FROM {qid(TABLES['liq'])}
            ORDER BY symbol
        """)

    brain = load_brain(con)
    report["brain"] = {
        "rows_loaded": len(brain),
        "state_counts": {},
        "side_bias": side_bias(brain),
        "top": top_brain_summary(brain),
    }
    for r in brain:
        st = str(r.get("authority_state") or r.get("state") or "UNKNOWN")
        report["brain"]["state_counts"][st] = report["brain"]["state_counts"].get(st, 0) + 1

    if exists(con, TABLES["promotion"]):
        report["promotion"] = {
            "action_counts": status_counts(con, TABLES["promotion"], "action"),
            "tier_counts": status_counts(con, TABLES["promotion"], "tier"),
            "queue_state_counts": status_counts(con, TABLES["promotion"], "queue_state"),
            "latest": latest_by_id(con, TABLES["promotion"], 8),
        }

    if exists(con, TABLES["queue"]):
        report["queue"] = {
            "queue_state_counts": status_counts(con, TABLES["queue"], "queue_state"),
            "latest": latest_by_id(con, TABLES["queue"], 8),
        }

    if exists(con, TABLES["intents"]):
        report["intents"] = {
            "intent_state_counts": status_counts(con, TABLES["intents"], "intent_state"),
            "adapter_status_counts": status_counts(con, TABLES["intents"], "adapter_status"),
            "latest": latest_by_id(con, TABLES["intents"], 8),
        }

    if exists(con, TABLES["adapter_health"]):
        report["adapter"] = latest_by_id(con, TABLES["adapter_health"], 5)

    report["positions"] = position_summary(con)

    if exists(con, TABLES["payoff"]):
        report["payoff"] = latest_by_id(con, TABLES["payoff"], 1)[0] if latest_by_id(con, TABLES["payoff"], 1) else {}

    if exists(con, TABLES["contract"]):
        report["contract"] = latest_by_id(con, TABLES["contract"], 3)

    con.close()

    for name,path in LOGS.items():
        info = recent_errors(path)
        report["logs"][name] = info
        report["logs_error_hits_total"] += info["hits"]

    verdict, problems = infer_verdict(report)
    report["verdict"] = verdict
    report["problems"] = problems

    (OUT / "full_bot_forensics_report.json").write_text(json.dumps(report, indent=2, sort_keys=True, default=str))

    lines = []
    lines.append(f"# {VERSION}")
    lines.append(f"- UTC: `{report['utc']}`")
    lines.append(f"- DB: `{report['db']}`")
    lines.append(f"- VERDICT: `{verdict}`")
    if problems:
        lines.append(f"- Problems: `{problems}`")
    lines.append("")

    lines.append("## Services")
    for k,v in report["services"].items():
        lines.append(f"- {k}: count={v['count']}")
    lines.append("")

    lines.append("## Data Plane Health")
    lines.append(str(report["health"]))
    lines.append("")

    lines.append("## Table recency")
    for k,v in report["table_activity"].items():
        lines.append(f"- {k}: exists={v['exists']} count={v['count']} latest={v['latest_ts']} age_min={None if v['age_min'] is None else round(v['age_min'],1)} recent12h={v['recent_12h']}")
    lines.append("")

    lines.append("## Market key")
    for k,v in report["market_key"].items():
        lines.append(f"- {k}: value={fmt(v.get('value'))} status={v.get('status')} age={fmt(v.get('age_min'),1)}m source={v.get('source')}")
    lines.append("")

    lines.append("## Brain")
    lines.append(f"- states: `{report['brain']['state_counts']}`")
    lines.append(f"- side_bias: `{report['brain']['side_bias']}`")
    for r in report["brain"]["top"][:10]:
        lines.append(f"- {r['symbol']} {r['side']} {r['setup']} state={r['state']} score={r['score']} mean={fmt(r['mean'])} lcb={fmt(r['lcb'])} pf={fmt(r['pf'])}")
    lines.append("")

    lines.append("## Promotion / Queue / Intents")
    lines.append(f"- promotion actions: `{report['promotion'].get('action_counts')}`")
    lines.append(f"- queue states: `{report['queue'].get('queue_state_counts')}`")
    lines.append(f"- intent states: `{report['intents'].get('intent_state_counts')}`")
    lines.append(f"- adapter statuses: `{report['intents'].get('adapter_status_counts')}`")
    lines.append("")

    lines.append("## Positions")
    ps = report["positions"]
    lines.append(f"- open_n={len(ps['open'])} closed_recent_n={ps['closed_n']} net_r={ps['net_r']} net_usd={ps['net_usd']}")
    for r in ps["open"][:5]:
        lines.append(f"- OPEN {r.get('symbol')} {r.get('side')} {r.get('setup')} status={r.get('status')} pnl_r={r.get('pnl_r') or r.get('net_pnl_r')}")
    for r in ps["closed_recent"][:6]:
        lines.append(f"- CLOSED {r.get('symbol')} {r.get('side')} {r.get('setup')} pnl_r={r.get('pnl_r') or r.get('net_pnl_r')} pnl_usd={r.get('pnl_usd') or r.get('net_pnl_usd')}")
    lines.append("")

    lines.append("## Logs")
    for k,v in report["logs"].items():
        lines.append(f"- {k}: error_hits={v['hits']}")
        for e in v["tail"][:4]:
            lines.append(f"  - {e[:180]}")
    lines.append("")

    (OUT / "full_bot_forensics_summary.md").write_text("\n".join(lines))
    print("\n".join(lines))
    return 0 if verdict != "NEEDS_FIX_BEFORE_LONG_RUN" else 3

if __name__ == "__main__":
    raise SystemExit(main())
