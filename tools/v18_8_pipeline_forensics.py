#!/usr/bin/env python3
from __future__ import annotations

import os, re, sqlite3, subprocess, json
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List

DB = Path("data/joanbot_v14.sqlite")
OUT = Path("data/v18_8_forensics")
VERSION = "V18.8_PIPELINE_FORENSICS_READ_ONLY"

TABLES = {
    "brain": "institutional_quant_brain_v17_5_1",
    "promotion": "institutional_promotion_controller_v17_6_1",
    "queue": "institutional_micro_canary_contract_queue_v17_6_1",
    "intents": "institutional_quant_canary_execution_intents_v17_7_2",
    "intent_map": "institutional_paper_canary_intent_map_v17_8_1",
    "adapter_health": "institutional_paper_canary_adapter_health_v17_8_1",
    "positions": "paper_micro_canary_positions_v11",
    "payoff": "institutional_payoff_snapshot_v18_6",
    "macro": "market_context_v18_2",
    "derivatives": "derivatives_context_v18_2",
}

SERVICES = {
    "Brain": "run_v17_5_1_quant_brain_forever|quant_brain_v17_5_1",
    "Promotion": "run_v17_6_1_promotion_controller_forever|promotion_controller_v17_6_1",
    "Adapter": "run_v17_8_1_paper_canary_adapter_forever|institutional_paper_canary_adapter_v17_8_1",
    "Macro": "run_v18_2_market_context_forever|v18_2_market_context_collector",
    "Payoff": "run_v18_6_payoff_intelligence_forever|v18_6_payoff_intelligence",
}

LOGS = {
    "brain": "data/v17_5_1/quant_brain_stdout.log",
    "promotion": "data/v17_6_1/promotion_controller_stdout.log",
    "adapter": "data/v17_8_1/adapter_stdout.log",
    "macro": "data/v18_2_visual/market_context_stdout.log",
    "payoff": "data/v18_6_payoff/payoff_stdout.log",
}

def now_utc():
    return datetime.now(timezone.utc)

def qid(x):
    return '"' + str(x).replace('"', '""') + '"'

def fnum(x, default=None):
    try:
        if x is None:
            return default
        return float(x)
    except Exception:
        return default

def exists(con, table):
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,)
    ).fetchone() is not None

def cols(con, table):
    if not exists(con, table):
        return []
    return [r[1] for r in con.execute(f"PRAGMA table_info({qid(table)})")]

def one(con, sql, args=()):
    con.row_factory = sqlite3.Row
    try:
        r = con.execute(sql, args).fetchone()
        return dict(r) if r else None
    except Exception as e:
        return {"_error": repr(e)}

def many(con, sql, args=()):
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute(sql, args).fetchall()]
    except Exception as e:
        return [{"_error": repr(e)}]

def ps_count(pattern):
    try:
        out = subprocess.check_output(
            ["sh", "-c", f"ps -ef | grep -Ei '{pattern}' | grep -v grep | wc -l"],
            text=True,
            stderr=subprocess.STDOUT,
        )
        return int(out.strip() or 0)
    except Exception:
        return 0

def table_count(con, table):
    if not exists(con, table):
        return None
    try:
        return con.execute(f"SELECT COUNT(*) FROM {qid(table)}").fetchone()[0]
    except Exception:
        return None

def order_col(con, table):
    c = cols(con, table)
    for x in ["ts", "created_at", "updated_at", "resolved_at", "opened_at", "closed_at"]:
        if x in c:
            return x
    for x in ["id", "rowid"]:
        if x in c:
            return x
    return "rowid"

def latest_rows(con, table, limit=5):
    if not exists(con, table):
        return []
    oc = order_col(con, table)
    try:
        return many(con, f"SELECT * FROM {qid(table)} ORDER BY {qid(oc) if oc!='rowid' else 'rowid'} DESC LIMIT ?", (limit,))
    except Exception:
        return []

def max_ts(con, table):
    if not exists(con, table):
        return None
    c = cols(con, table)
    for tc in ["ts", "created_at", "updated_at", "resolved_at", "opened_at", "closed_at"]:
        if tc in c:
            r = one(con, f"SELECT MAX({qid(tc)}) AS mx FROM {qid(table)}")
            if r and r.get("mx"):
                return tc, r.get("mx")
    return None, None

def recent_count(con, table, hours=12):
    if not exists(con, table):
        return None
    c = cols(con, table)
    tc = next((x for x in ["ts", "created_at", "updated_at", "resolved_at", "opened_at", "closed_at"] if x in c), None)
    if not tc:
        return None
    cutoff = (now_utc() - timedelta(hours=hours)).isoformat()
    try:
        return con.execute(
            f"SELECT COUNT(*) FROM {qid(table)} WHERE {qid(tc)} >= ?",
            (cutoff,)
        ).fetchone()[0]
    except Exception:
        return None

def group_latest(con, table, group_cols: List[str], limit=12):
    if not exists(con, table):
        return []
    c = cols(con, table)
    use = [x for x in group_cols if x in c]
    if not use:
        return []
    tc = next((x for x in ["ts", "created_at", "updated_at"] if x in c), None)
    where = ""
    args = ()
    if tc:
        mx = one(con, f"SELECT MAX({qid(tc)}) AS mx FROM {qid(table)}")
        if mx and mx.get("mx"):
            where = f"WHERE {qid(tc)}=?"
            args = (mx["mx"],)
    fields = ", ".join(qid(x) for x in use)
    sql = f"""
        SELECT {fields}, COUNT(*) AS n
        FROM {qid(table)}
        {where}
        GROUP BY {fields}
        ORDER BY n DESC
        LIMIT {int(limit)}
    """
    return many(con, sql, args)

def compact_row(row: Dict[str, Any], keys: List[str]) -> str:
    parts = []
    for k in keys:
        if k in row and row.get(k) is not None:
            v = str(row.get(k))
            if len(v) > 42:
                v = v[:39] + "..."
            parts.append(f"{k}={v}")
    if not parts:
        raw = {k: row[k] for k in list(row.keys())[:8]}
        return str(raw)
    return " | ".join(parts)

def log_mtime(path):
    p = Path(path)
    if not p.exists():
        return "MISSING"
    ts = datetime.fromtimestamp(p.stat().st_mtime, timezone.utc)
    age_min = (now_utc() - ts).total_seconds() / 60
    return f"{ts.isoformat()} age_min={age_min:.1f}"

def tail_text(path, n=60):
    p = Path(path)
    if not p.exists():
        return []
    try:
        data = p.read_text(errors="ignore").splitlines()
        return data[-n:]
    except Exception:
        return []

def extract_reason_counts(lines):
    txt = "\n".join(lines)
    keys = [
        "LCB", "LOW_SAMPLE", "LOW_LIVE", "NO_PROMOTION_EDGE",
        "HARD_VETO", "DRAWDOWN", "PAYOFF", "BLOCK",
        "REVIEW_MICRO_CANARY", "RESEARCH_ONLY",
        "QUARANTINE", "MANUAL_REVIEW_REQUIRED",
        "PROMOTION_BLOCK", "BAD", "WATCH", "GOOD"
    ]
    out = {}
    for k in keys:
        out[k] = len(re.findall(k, txt, flags=re.I))
    return {k:v for k,v in out.items() if v}

def infer_bottleneck(report):
    reasons = []

    services = report["services"]
    dead = [k for k,v in services.items() if v <= 0]
    if dead:
        reasons.append("DEAD_SERVICE:" + ",".join(dead))

    payoff = report.get("latest", {}).get("payoff") or {}
    if payoff:
        if str(payoff.get("promotion_block")) == "1":
            reasons.append("PAYOFF_PROMOTION_BLOCK")
        if str(payoff.get("payoff_health", "")).upper() in {"BAD", "INSUFFICIENT_SAMPLE"}:
            reasons.append("PAYOFF_NOT_ROBUST")

    brain_recent = report["tables"].get("brain", {}).get("recent_12h")
    promo_recent = report["tables"].get("promotion", {}).get("recent_12h")
    queue_total = report["tables"].get("queue", {}).get("count")
    intents_total = report["tables"].get("intents", {}).get("count")

    adapter = report.get("latest", {}).get("adapter_health") or {}
    pending = fnum(adapter.get("pending_intents"), 0)
    opened = fnum(adapter.get("opened_positions"), 0)
    errors = fnum(adapter.get("errors"), 0)

    if brain_recent == 0:
        reasons.append("BRAIN_NO_RECENT_ROWS")
    elif promo_recent == 0:
        reasons.append("PROMOTION_NO_RECENT_ROWS")
    elif pending == 0 and opened == 0:
        reasons.append("ADAPTER_IDLE_NO_INTENTS")

    if errors and errors > 0:
        reasons.append("ADAPTER_ERRORS")

    promo_groups = report.get("groups", {}).get("promotion", [])
    if promo_groups:
        s = str(promo_groups)
        if "QUARANTINE" in s or "RESEARCH_ONLY" in s or "REVIEW" in s:
            reasons.append("PROMOTION_CLASSIFYING_NOT_OPENING")

    if not reasons:
        reasons.append("NO_CRITICAL_BOTTLENECK_DETECTED")

    return reasons

def main():
    OUT.mkdir(parents=True, exist_ok=True)
    report = {
        "version": VERSION,
        "utc": now_utc().isoformat(),
        "db": {},
        "services": {},
        "logs": {},
        "tables": {},
        "groups": {},
        "latest": {},
        "bottleneck": [],
    }

    lines = []
    lines.append(f"# {VERSION}")
    lines.append("")
    lines.append(f"- UTC: `{report['utc']}`")
    lines.append("")

    if not DB.exists():
        lines.append("## DB")
        lines.append("- `DB_MISSING`")
        (OUT / "pipeline_forensics_summary.md").write_text("\n".join(lines))
        print("\n".join(lines))
        return 2

    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row

    qc = con.execute("PRAGMA quick_check").fetchone()[0]
    report["db"]["quick_check"] = qc
    report["db"]["size_mb"] = round(DB.stat().st_size / 1024 / 1024, 2)

    lines.append("## DB")
    lines.append(f"- quick_check: `{qc}`")
    lines.append(f"- size_mb: `{report['db']['size_mb']}`")
    lines.append("")

    lines.append("## Services")
    for name, pattern in SERVICES.items():
        cnt = ps_count(pattern)
        report["services"][name] = cnt
        state = "OK" if cnt >= 1 else "DEAD"
        lines.append(f"- {name}: `{state}` count={cnt}")
    lines.append("")

    lines.append("## Logs recency")
    for name, path in LOGS.items():
        mt = log_mtime(path)
        report["logs"][name] = mt
        lines.append(f"- {name}: `{mt}`")
    lines.append("")

    lines.append("## Table activity")
    for key, table in TABLES.items():
        info = {
            "table": table,
            "exists": exists(con, table),
            "count": None,
            "latest_ts": None,
            "recent_12h": None,
        }
        if info["exists"]:
            info["count"] = table_count(con, table)
            tc, mx = max_ts(con, table)
            info["latest_ts"] = {"column": tc, "value": mx}
            info["recent_12h"] = recent_count(con, table, 12)
        report["tables"][key] = info
        lines.append(
            f"- {key}: exists={info['exists']} count={info['count']} "
            f"latest={info['latest_ts']} recent_12h={info['recent_12h']}"
        )
    lines.append("")

    # Latest rows
    key_sets = {
        "brain": ["ts","authority_state","state","symbol","side","setup","brain_score","robust_mean_r","institutional_lcb_r","profit_factor","reasons"],
        "promotion": ["ts","action","tier","queue_state","symbol","side","setup","brain","score","priority","reasons","source_tier"],
        "queue": ["id","ts","queue_state","symbol","side","setup","requested_size_mult","institutional_priority","source_tier"],
        "intents": ["id","ts","intent_state","adapter_status","symbol","side","setup","requested_size_mult"],
        "adapter_health": ["ts","pending_intents","opened_positions","managed_positions","closed_positions","rejected_intents","errors"],
        "positions": ["id","opened_at","closed_at","symbol","side","setup","status","pnl_r","net_pnl_r","pnl_usd","net_pnl_usd","manager_state"],
        "payoff": ["ts","scope","payoff_health","promotion_block","closed_n","expectancy_all_r","lcb95_r","profit_factor","payoff_ratio","reasons"],
    }

    lines.append("## Latest rows")
    for key, keys in key_sets.items():
        table = TABLES.get(key)
        if not table or not exists(con, table):
            continue
        rows = latest_rows(con, table, 5)
        if rows:
            report["latest"][key] = rows[0]
        lines.append(f"### {key}")
        for r in rows:
            lines.append("- " + compact_row(r, keys))
        lines.append("")

    # Groups
    lines.append("## Latest group counts")
    groups = {
        "brain": ["authority_state","state","symbol","side"],
        "promotion": ["action","tier","queue_state","symbol","side"],
        "queue": ["queue_state","symbol","side"],
        "intents": ["intent_state","adapter_status","symbol","side"],
        "positions": ["status","symbol","side"],
        "payoff": ["payoff_health","promotion_block","scope"],
    }
    for key, gc in groups.items():
        table = TABLES.get(key)
        if not table or not exists(con, table):
            continue
        g = group_latest(con, table, gc)
        report["groups"][key] = g
        lines.append(f"### {key}")
        for r in g[:12]:
            lines.append("- " + str(r))
        lines.append("")

    # Logs reason counts
    lines.append("## Recent log reason counts")
    for name, path in LOGS.items():
        rc = extract_reason_counts(tail_text(path, 300))
        lines.append(f"- {name}: `{rc}`")
    lines.append("")

    report["bottleneck"] = infer_bottleneck(report)
    lines.append("## Bottleneck inference")
    for x in report["bottleneck"]:
        lines.append(f"- `{x}`")
    lines.append("")

    lines.append("## Operational verdict")
    if any(x.startswith("DEAD_SERVICE") for x in report["bottleneck"]):
        lines.append("- `NOT_OK`: hi ha serveis morts.")
    elif "ADAPTER_ERRORS" in report["bottleneck"]:
        lines.append("- `NOT_OK`: adapter amb errors.")
    elif "PAYOFF_PROMOTION_BLOCK" in report["bottleneck"]:
        lines.append("- `COHERENT_BLOCK`: pipeline viu, però payoff bloqueja promoció institucional.")
    elif "ADAPTER_IDLE_NO_INTENTS" in report["bottleneck"]:
        lines.append("- `IDLE`: pipeline viu, però no arriben intents operables a l'adapter.")
    else:
        lines.append("- `OK_READ_ONLY`: no hi ha incidència crítica detectada.")
    lines.append("")

    (OUT / "pipeline_forensics_report.json").write_text(json.dumps(report, indent=2, sort_keys=True))
    (OUT / "pipeline_forensics_summary.md").write_text("\n".join(lines))

    print("\n".join(lines))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
