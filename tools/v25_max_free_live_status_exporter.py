#!/usr/bin/env python3
from __future__ import annotations

import html
import json
import os
import re
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path("/storage/emulated/0/Download/joan_crypto_ai_pro_v14")
DB = ROOT / "data" / "joanbot_v14.sqlite"
OUT = ROOT / "live_export"
OUT.mkdir(parents=True, exist_ok=True)

BASE_EQUITY = 100000.0
VERSION = "V25_MAX_FREE_LIVE_STATUS_EXPORTER"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def fnum(x: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if x is None:
            return default
        return float(x)
    except Exception:
        return default


def qid(x: str) -> str:
    return '"' + str(x).replace('"', '""') + '"'


def connect() -> sqlite3.Connection:
    con = sqlite3.connect(str(DB), timeout=20)
    con.row_factory = sqlite3.Row
    return con


def table_exists(con: sqlite3.Connection, table: str) -> bool:
    try:
        return con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone() is not None
    except Exception:
        return False


def cols(con: sqlite3.Connection, table: str) -> List[str]:
    if not table_exists(con, table):
        return []
    return [r[1] for r in con.execute(f"PRAGMA table_info({qid(table)})")]


def safe_query_one(con: sqlite3.Connection, sql: str, params=()) -> Dict[str, Any]:
    try:
        r = con.execute(sql, params).fetchone()
        return dict(r) if r else {}
    except Exception as e:
        return {"error": repr(e)}


def safe_query_all(con: sqlite3.Connection, sql: str, params=()) -> List[Dict[str, Any]]:
    try:
        return [dict(r) for r in con.execute(sql, params).fetchall()]
    except Exception as e:
        return [{"error": repr(e)}]


def process_count(pattern: str) -> int:
    try:
        out = subprocess.check_output(
            ["sh", "-c", f"ps -ef | grep -Ei '{pattern}' | grep -v grep | wc -l"],
            text=True,
        )
        return int(out.strip() or "0")
    except Exception:
        return -1


def process_lines(pattern: str) -> List[str]:
    try:
        out = subprocess.check_output(
            ["sh", "-c", f"ps -ef | grep -Ei '{pattern}' | grep -v grep || true"],
            text=True,
        )
        lines = []
        for line in out.splitlines():
            # Sanitized: no full env, no secrets, just process command line.
            lines.append(line[:300])
        return lines
    except Exception:
        return []


def read_runtime_summary() -> Dict[str, Any]:
    p = ROOT / "data" / "v22_1_runtime_manager" / "runtime_summary.md"
    if not p.exists():
        return {
            "exists": False,
            "verdict": None,
            "problems": None,
            "raw_head": [],
        }

    txt = p.read_text(errors="ignore")
    lines = txt.splitlines()
    verdict = None
    problems = None

    for line in lines:
        if "Verdict:" in line:
            verdict = line.strip()
        if "Problems:" in line:
            problems = line.strip()

    return {
        "exists": True,
        "verdict": verdict,
        "problems": problems,
        "raw_head": lines[:45],
    }


def price_health(con: sqlite3.Connection) -> Dict[str, Any]:
    try:
        from joanbot.institutional.canonical_market_data_contract_v24_9_final import canonical_market_health
        return canonical_market_health(con)
    except Exception as e:
        # Fallback to latest table if import fails.
        latest = []
        if table_exists(con, "institutional_v24_market_price_latest"):
            c = cols(con, "institutional_v24_market_price_latest")
            keep = [x for x in ["symbol", "ts", "version", "price", "source", "reason", "source_age_min"] if x in c]
            if keep:
                latest = safe_query_all(
                    con,
                    f'SELECT {",".join(qid(x) for x in keep)} FROM "institutional_v24_market_price_latest" ORDER BY symbol',
                )
        return {
            "ok": False,
            "reason": "PRICE_HEALTH_IMPORT_EXCEPTION",
            "error": repr(e),
            "fallback_latest": latest,
        }


def read_prices(con: sqlite3.Connection) -> Dict[str, Any]:
    table = "institutional_v24_market_price_latest"
    if not table_exists(con, table):
        return {"rows": [], "prices": {}}

    c = cols(con, table)
    keep = [x for x in ["symbol", "ts", "version", "price", "source", "reason", "source_age_min"] if x in c]
    rows = []
    prices = {}

    if keep:
        rows = safe_query_all(con, f'SELECT {",".join(qid(x) for x in keep)} FROM {qid(table)} ORDER BY symbol')
        for r in rows:
            sym = r.get("symbol")
            px = fnum(r.get("price"))
            if sym and px is not None:
                prices[str(sym).upper()] = px

    return {"rows": rows, "prices": prices}


def compute_money(con: sqlite3.Connection, prices: Dict[str, float]) -> Dict[str, Any]:
    table = "paper_micro_canary_positions_v11"
    if not table_exists(con, table):
        return {"ok": False, "reason": "NO_POSITION_TABLE"}

    c = cols(con, table)
    pnl_col = "net_pnl_usd" if "net_pnl_usd" in c else ("pnl_usd" if "pnl_usd" in c else None)

    select_cols = [x for x in [
        "id", "opened_at", "closed_at", "symbol", "side", "setup", "status",
        "entry_price", "stop_price", "take_profit_price",
        "size_usd", "pnl_usd", "net_pnl_usd", "pnl_r", "net_pnl_r", "reason",
        "control_id", "source_edge_id",
    ] if x in c]

    rows = []
    if select_cols:
        rows = safe_query_all(
            con,
            f'SELECT {",".join(qid(x) for x in select_cols)} FROM {qid(table)} ORDER BY id ASC',
        )

    closed = []
    open_pos = []

    realized_pnl = 0.0
    open_stored_pnl = 0.0
    open_mark_pnl = 0.0
    open_mark_count = 0
    open_exposure = 0.0
    open_risk = 0.0

    wins = []
    losses = []
    cumulative = 0.0
    peak = 0.0
    max_dd = 0.0

    for r in rows:
        status = str(r.get("status") or "").upper()
        pnl = fnum(r.get("net_pnl_usd"), None)
        if pnl is None:
            pnl = fnum(r.get("pnl_usd"), 0.0) or 0.0

        if status == "CLOSED":
            closed.append(r)
            realized_pnl += pnl
            if pnl > 0:
                wins.append(pnl)
            elif pnl < 0:
                losses.append(pnl)

            cumulative += pnl
            peak = max(peak, cumulative)
            max_dd = min(max_dd, cumulative - peak)

        if status == "OPEN":
            open_pos.append(r)
            open_stored_pnl += pnl

            size = fnum(r.get("size_usd"), 0.0) or 0.0
            entry = fnum(r.get("entry_price"), None)
            stop = fnum(r.get("stop_price"), None)
            symbol = str(r.get("symbol") or "").upper()
            side = str(r.get("side") or "").upper()
            current = prices.get(symbol)

            open_exposure += size

            if entry and stop and size:
                open_risk += abs(stop - entry) / entry * size

            if current and entry and size:
                if side == "SHORT":
                    mpnl = (entry - current) / entry * size
                else:
                    mpnl = (current - entry) / entry * size
                open_mark_pnl += mpnl
                open_mark_count += 1

    unrealized_pnl = open_mark_pnl if open_mark_count > 0 else open_stored_pnl
    cash_balance = BASE_EQUITY + realized_pnl
    marked_equity = cash_balance + unrealized_pnl
    total_pnl = marked_equity - BASE_EQUITY

    closed_count = len(closed)
    win_count = len(wins)
    loss_count = len(losses)
    win_rate = (win_count / closed_count * 100.0) if closed_count else 0.0

    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = (gross_win / gross_loss) if gross_loss > 0 else (None if gross_win == 0 else 999.0)
    expectancy = (realized_pnl / closed_count) if closed_count else 0.0

    last_closed = closed[-1] if closed else None

    return {
        "ok": True,
        "base_equity": round(BASE_EQUITY, 6),
        "cash_balance_realized": round(cash_balance, 6),
        "marked_equity": round(marked_equity, 6),
        "realized_pnl_usd": round(realized_pnl, 6),
        "unrealized_pnl_usd": round(unrealized_pnl, 6),
        "total_pnl_usd": round(total_pnl, 6),
        "realized_return_pct": round((realized_pnl / BASE_EQUITY) * 100.0, 6),
        "marked_return_pct": round((total_pnl / BASE_EQUITY) * 100.0, 6),
        "open_positions": len(open_pos),
        "closed_positions": closed_count,
        "wins": win_count,
        "losses": loss_count,
        "win_rate_pct": round(win_rate, 4),
        "gross_win_usd": round(gross_win, 6),
        "gross_loss_usd": round(gross_loss, 6),
        "profit_factor": None if profit_factor is None else round(profit_factor, 6),
        "expectancy_usd_per_trade": round(expectancy, 6),
        "max_realized_drawdown_usd": round(max_dd, 6),
        "open_exposure_usd": round(open_exposure, 6),
        "open_risk_usd_approx": round(open_risk, 6),
        "pnl_source_column": pnl_col,
        "last_closed_trade": last_closed,
    }


def latest_positions(con: sqlite3.Connection) -> List[Dict[str, Any]]:
    table = "paper_micro_canary_positions_v11"
    if not table_exists(con, table):
        return []
    c = cols(con, table)
    keep = [x for x in [
        "id", "opened_at", "closed_at", "symbol", "side", "setup", "status",
        "entry_price", "stop_price", "take_profit_price",
        "size_usd", "pnl_usd", "net_pnl_usd", "pnl_r", "net_pnl_r", "reason",
        "control_id", "source_edge_id",
    ] if x in c]
    if not keep:
        return []
    return safe_query_all(
        con,
        f'SELECT {",".join(qid(x) for x in keep)} FROM {qid(table)} ORDER BY id DESC LIMIT 15',
    )


def latest_intents(con: sqlite3.Connection) -> List[Dict[str, Any]]:
    table = "institutional_quant_canary_execution_intents_v17_7_2"
    if not table_exists(con, table):
        return []
    c = cols(con, table)
    keep = [x for x in [
        "id", "ts", "intent_state", "adapter_status", "symbol", "side", "setup",
        "requested_size_mult", "position_row_id", "stable_position_id", "reject_reason",
    ] if x in c]
    if not keep:
        return []
    return safe_query_all(
        con,
        f'SELECT {",".join(qid(x) for x in keep)} FROM {qid(table)} ORDER BY id DESC LIMIT 15',
    )


def adapter_health(con: sqlite3.Connection) -> List[Dict[str, Any]]:
    table = "institutional_v24_4_canonical_adapter_health"
    if not table_exists(con, table):
        return []
    c = cols(con, table)
    keep = [x for x in [
        "id", "ts", "quick_check", "pending_intents", "opened_positions", "managed_positions",
        "closed_positions", "rejected_intents", "errors",
    ] if x in c]
    if not keep:
        return []
    return safe_query_all(
        con,
        f'SELECT {",".join(qid(x) for x in keep)} FROM {qid(table)} ORDER BY id DESC LIMIT 5',
    )


def db_quick_check(con: sqlite3.Connection) -> str:
    try:
        return con.execute("PRAGMA quick_check").fetchone()[0]
    except Exception as e:
        return "ERROR:" + repr(e)


def recent_error_summary() -> Dict[str, Any]:
    base = ROOT / "data"
    patterns = {
        "traceback": re.compile(r"Traceback", re.I),
        "database_locked": re.compile(r"database is locked", re.I),
        "abort": re.compile(r"\bABORT[_A-Z0-9]*", re.I),
        "fatal": re.compile(r"fatal|FATAL", re.I),
        "exception": re.compile(r"exception|Exception", re.I),
        "integrity": re.compile(r"IntegrityError|NOT NULL constraint", re.I),
    }

    counts = {k: 0 for k in patterns}
    files_hit = []

    if not base.exists():
        return {"counts": counts, "files_hit": files_hit}

    now_ts = datetime.now(timezone.utc).timestamp()
    for p in base.rglob("*"):
        if not p.is_file():
            continue
        name = p.name.lower()
        if not any(tok in name for tok in ["stderr", "error", "errors", "fatal"]):
            continue
        try:
            st = p.stat()
            age_min = (now_ts - st.st_mtime) / 60.0
            if age_min > 240:
                continue

            with p.open("rb") as f:
                try:
                    f.seek(max(0, st.st_size - 30000))
                except Exception:
                    pass
                txt = f.read().decode("utf-8", errors="ignore")

            file_has = False
            for key, rgx in patterns.items():
                n = len(rgx.findall(txt))
                if n:
                    counts[key] += n
                    file_has = True

            if file_has:
                files_hit.append({
                    "file": str(p.relative_to(ROOT)),
                    "age_min": round(age_min, 3),
                    "size_kb": round(st.st_size / 1024.0, 2),
                })
        except Exception:
            continue

    return {"counts": counts, "files_hit": files_hit[:20]}


def health_verdict(db_ok: str, price: Dict[str, Any], runtime: Dict[str, Any], adapter: List[Dict[str, Any]], processes: Dict[str, int], errors: Dict[str, Any]) -> Dict[str, Any]:
    problems = []

    if db_ok != "ok":
        problems.append("DB_NOT_OK")

    if not price.get("ok"):
        problems.append("PRICE_NOT_OK")

    runtime_problems = str(runtime.get("problems") or "")
    runtime_verdict = str(runtime.get("verdict") or "")

    # Runtime summary is Markdown and may format the empty list as:
    # Problems: []
    # Problems: `[]`
    # - Problems: `[]`
    normalized_runtime_problems = (
        runtime_problems
        .replace("`", "")
        .replace(" ", "")
        .strip()
    )

    runtime_problems_empty = (
        "Problems:[]" in normalized_runtime_problems
        or normalized_runtime_problems in {"[]", "Problems:[]", "-Problems:[]"}
    )

    if not runtime_problems_empty:
        problems.append("RUNTIME_PROBLEMS_NOT_EMPTY")
    if "OK_RUNTIME_MANAGER_ACTIVE" not in runtime_verdict:
        problems.append("RUNTIME_NOT_ACTIVE")

    if adapter:
        last = adapter[0]
        if str(last.get("quick_check")).lower() != "ok":
            problems.append("ADAPTER_QUICK_NOT_OK")
        if int(last.get("errors") or 0) != 0:
            problems.append("ADAPTER_ERRORS")
        if int(last.get("pending_intents") or 0) > 3:
            problems.append("PENDING_INTENTS_HIGH")
    else:
        problems.append("NO_ADAPTER_HEALTH")

    if processes.get("old_v17_adapter", 0) > 0:
        problems.append("OLD_V17_ADAPTER_RUNNING")
    if processes.get("old_v23", 0) > 0:
        problems.append("OLD_V23_RUNNING")

    err_counts = errors.get("counts") or {}
    if err_counts.get("traceback", 0) > 0 or err_counts.get("integrity", 0) > 0:
        problems.append("RECENT_ERROR_LOGS_PRESENT")

    state = "GREEN" if not problems else "RED"

    return {
        "state": state,
        "problems": problems,
    }


def money_symbol(x: Any) -> str:
    try:
        v = float(x)
    except Exception:
        return "`n/a`"
    sign = "+" if v > 0 else ""
    return f"`{sign}{v:,.2f} $`"


def pct_symbol(x: Any) -> str:
    try:
        v = float(x)
    except Exception:
        return "`n/a`"
    sign = "+" if v > 0 else ""
    return f"`{sign}{v:.6f} %`"


def write_markdown(status: Dict[str, Any]) -> None:
    m = status["money"]
    verdict = status["health"]["state"]

    md = []
    md.append("# LIVE BOT STATUS")
    md.append("")
    md.append(f"- Updated UTC: `{status['ts']}`")
    md.append(f"- Exporter: `{VERSION}`")
    md.append(f"- Health: `{verdict}`")
    md.append(f"- DB quick_check: `{status['db_quick_check']}`")
    md.append("")
    md.append("## Money / Cash / PnL")
    md.append("")
    md.append("| Metric | Value |")
    md.append("|---|---:|")
    md.append(f"| Base equity | {money_symbol(m.get('base_equity'))} |")
    md.append(f"| Cash balance realized | {money_symbol(m.get('cash_balance_realized'))} |")
    md.append(f"| Marked equity | {money_symbol(m.get('marked_equity'))} |")
    md.append(f"| Realized PnL | {money_symbol(m.get('realized_pnl_usd'))} |")
    md.append(f"| Unrealized PnL | {money_symbol(m.get('unrealized_pnl_usd'))} |")
    md.append(f"| Total PnL | {money_symbol(m.get('total_pnl_usd'))} |")
    md.append(f"| Realized return | {pct_symbol(m.get('realized_return_pct'))} |")
    md.append(f"| Marked return | {pct_symbol(m.get('marked_return_pct'))} |")
    md.append(f"| Open positions | `{m.get('open_positions')}` |")
    md.append(f"| Closed positions | `{m.get('closed_positions')}` |")
    md.append(f"| Win rate | `{m.get('win_rate_pct')} %` |")
    md.append(f"| Profit factor | `{m.get('profit_factor')}` |")
    md.append(f"| Expectancy / trade | {money_symbol(m.get('expectancy_usd_per_trade'))} |")
    md.append(f"| Max realized drawdown | {money_symbol(m.get('max_realized_drawdown_usd'))} |")
    md.append(f"| Open exposure | {money_symbol(m.get('open_exposure_usd'))} |")
    md.append(f"| Approx open risk | {money_symbol(m.get('open_risk_usd_approx'))} |")
    md.append("")
    md.append("## Price")
    md.append("")
    ph = status["price_health"]
    md.append(f"- market_health_ok: `{ph.get('ok')}`")
    md.append(f"- reason: `{ph.get('reason')}`")
    for sym, d in (ph.get("details") or {}).items():
        md.append(f"- {sym}: ok=`{d.get('ok')}` price=`{d.get('price')}` source=`{d.get('source')}` age=`{d.get('age_min')}` reason=`{d.get('reason')}`")
    md.append("")
    md.append("## Adapter")
    for a in status["adapter_health"][:3]:
        md.append(f"- id={a.get('id')} quick={a.get('quick_check')} pending={a.get('pending_intents')} opened={a.get('opened_positions')} managed={a.get('managed_positions')} closed={a.get('closed_positions')} rejected={a.get('rejected_intents')} errors={a.get('errors')}")
    md.append("")
    md.append("## Processes")
    for k, v in status["processes"].items():
        md.append(f"- {k}: `{v}`")
    md.append("")
    md.append("## Latest positions")
    for p in status["positions_latest"][:10]:
        md.append(f"- id={p.get('id')} {p.get('symbol')} {p.get('side')} {p.get('setup')} status={p.get('status')} net={p.get('net_pnl_usd')} r={p.get('net_pnl_r')} reason={p.get('reason')}")
    md.append("")
    md.append("## Latest intents")
    for i in status["intents_latest"][:10]:
        md.append(f"- id={i.get('id')} state={i.get('intent_state')} adapter={i.get('adapter_status')} {i.get('symbol')} {i.get('side')} {i.get('setup')}")
    md.append("")
    md.append("## Recent error summary")
    md.append("")
    md.append("Raw logs are not published. Only sanitized counts.")
    for k, v in (status["recent_errors"].get("counts") or {}).items():
        md.append(f"- {k}: `{v}`")
    md.append("")
    md.append("## Health problems")
    if status["health"]["problems"]:
        for p in status["health"]["problems"]:
            md.append(f"- `{p}`")
    else:
        md.append("- `NONE`")

    (OUT / "status.md").write_text("\n".join(md))

    money_md = []
    money_md.append("# MONEY DASHBOARD")
    money_md.append("")
    money_md.append(f"- Updated UTC: `{status['ts']}`")
    money_md.append(f"- Health: `{verdict}`")
    money_md.append("")
    money_md.append("| Metric | Value |")
    money_md.append("|---|---:|")
    money_md.append(f"| Base equity | {money_symbol(m.get('base_equity'))} |")
    money_md.append(f"| Cash balance realized | {money_symbol(m.get('cash_balance_realized'))} |")
    money_md.append(f"| Marked equity | {money_symbol(m.get('marked_equity'))} |")
    money_md.append(f"| Realized PnL | {money_symbol(m.get('realized_pnl_usd'))} |")
    money_md.append(f"| Unrealized PnL | {money_symbol(m.get('unrealized_pnl_usd'))} |")
    money_md.append(f"| Total PnL | {money_symbol(m.get('total_pnl_usd'))} |")
    money_md.append(f"| Realized return | {pct_symbol(m.get('realized_return_pct'))} |")
    money_md.append(f"| Marked return | {pct_symbol(m.get('marked_return_pct'))} |")
    money_md.append(f"| Closed trades | `{m.get('closed_positions')}` |")
    money_md.append(f"| Wins / losses | `{m.get('wins')} / {m.get('losses')}` |")
    money_md.append(f"| Win rate | `{m.get('win_rate_pct')} %` |")
    money_md.append(f"| Profit factor | `{m.get('profit_factor')}` |")
    money_md.append(f"| Expectancy / trade | {money_symbol(m.get('expectancy_usd_per_trade'))} |")
    money_md.append(f"| Max realized drawdown | {money_symbol(m.get('max_realized_drawdown_usd'))} |")
    money_md.append(f"| Open exposure | {money_symbol(m.get('open_exposure_usd'))} |")
    money_md.append(f"| Approx open risk | {money_symbol(m.get('open_risk_usd_approx'))} |")
    (OUT / "money.md").write_text("\n".join(money_md))


def write_html(status: Dict[str, Any]) -> None:
    m = status["money"]
    state = status["health"]["state"]
    state_class = "green" if state == "GREEN" else "red"

    rows = ""
    metrics = [
        ("Base equity", m.get("base_equity")),
        ("Cash balance realized", m.get("cash_balance_realized")),
        ("Marked equity", m.get("marked_equity")),
        ("Realized PnL", m.get("realized_pnl_usd")),
        ("Unrealized PnL", m.get("unrealized_pnl_usd")),
        ("Total PnL", m.get("total_pnl_usd")),
        ("Realized return %", m.get("realized_return_pct")),
        ("Marked return %", m.get("marked_return_pct")),
        ("Open positions", m.get("open_positions")),
        ("Closed positions", m.get("closed_positions")),
        ("Win rate %", m.get("win_rate_pct")),
        ("Profit factor", m.get("profit_factor")),
        ("Expectancy/trade", m.get("expectancy_usd_per_trade")),
        ("Max realized drawdown", m.get("max_realized_drawdown_usd")),
        ("Open exposure", m.get("open_exposure_usd")),
        ("Approx open risk", m.get("open_risk_usd_approx")),
    ]

    for k, v in metrics:
        rows += f"<tr><td>{html.escape(str(k))}</td><td>{html.escape(str(v))}</td></tr>\n"

    problems = status["health"]["problems"] or ["NONE"]
    problems_html = "".join(f"<li>{html.escape(str(p))}</li>" for p in problems)

    doc = f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta http-equiv="refresh" content="180">
<title>JoanBot Live Status</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif; margin: 24px; background:#0b0f14; color:#e6edf3; }}
.card {{ background:#111827; border:1px solid #263244; border-radius:14px; padding:18px; margin:14px 0; }}
.green {{ color:#00d18f; font-weight:700; }}
.red {{ color:#ff4d4d; font-weight:700; }}
table {{ border-collapse: collapse; width:100%; }}
td, th {{ border-bottom:1px solid #263244; padding:9px; text-align:left; }}
td:last-child {{ text-align:right; font-family: monospace; }}
small, code {{ color:#9fb0c0; }}
</style>
</head>
<body>
<h1>JoanBot Live Status</h1>
<div class="card">
<p>Updated UTC: <code>{html.escape(status['ts'])}</code></p>
<p>Health: <span class="{state_class}">{html.escape(state)}</span></p>
<p>DB: <code>{html.escape(str(status['db_quick_check']))}</code></p>
</div>
<div class="card">
<h2>Money / Cash / PnL</h2>
<table>{rows}</table>
</div>
<div class="card">
<h2>Health Problems</h2>
<ul>{problems_html}</ul>
</div>
<div class="card">
<h2>Files</h2>
<p>See <code>status.md</code>, <code>money.md</code>, and <code>status.json</code> in the live-status branch.</p>
</div>
</body>
</html>
"""
    (OUT / "dashboard.html").write_text(doc)


def main() -> int:
    ts = utc_now()
    con = connect()

    db_ok = db_quick_check(con)
    prices = read_prices(con)
    ph = price_health(con)
    money = compute_money(con, prices["prices"])
    runtime = read_runtime_summary()
    adapter = adapter_health(con)
    positions = latest_positions(con)
    intents = latest_intents(con)

    processes = {
        "runtime_manager": process_count("v22_1_runtime_manager"),
        "price_contract": process_count("v24_1_market_price_contract"),
        "quant_authority": process_count("v24_0_quant_production_authority"),
        "canonical_adapter": process_count("v24_4_canonical_paper_adapter"),
        "canonical_equity": process_count("v24_5_canonical_equity"),
        "live_status_sync": process_count("v25_max_free_live_status_sync"),
        "old_v17_adapter": process_count("v17_8_1"),
        "old_v23": process_count("v23_3|v23_4"),
    }

    errors = recent_error_summary()
    health = health_verdict(db_ok, ph, runtime, adapter, processes, errors)

    status = {
        "version": VERSION,
        "ts": ts,
        "db_quick_check": db_ok,
        "health": health,
        "money": money,
        "price_health": ph,
        "price_latest": prices["rows"],
        "runtime": runtime,
        "adapter_health": adapter,
        "positions_latest": positions,
        "intents_latest": intents,
        "processes": processes,
        "recent_errors": errors,
    }

    con.close()

    (OUT / "status.json").write_text(json.dumps(status, indent=2, sort_keys=True, default=str))
    (OUT / "heartbeat.txt").write_text(ts + "\n")
    write_markdown(status)
    write_html(status)

    print(json.dumps({
        "ok": True,
        "health": health,
        "marked_equity": money.get("marked_equity"),
        "realized_pnl_usd": money.get("realized_pnl_usd"),
        "total_pnl_usd": money.get("total_pnl_usd"),
        "open_positions": money.get("open_positions"),
        "closed_positions": money.get("closed_positions"),
        "ts": ts,
    }, indent=2, sort_keys=True, default=str))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
