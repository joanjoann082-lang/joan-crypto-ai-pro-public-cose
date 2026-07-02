#!/usr/bin/env python3
from __future__ import annotations

import json, math, sqlite3
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Dict, List

DB = Path("data/joanbot_v14.sqlite")
OUT = Path("data/v18_6_payoff")
SNAPSHOT_TABLE = "institutional_payoff_snapshot_v18_6"
VERSION = "V18.6_PAYOFF_INTELLIGENCE_REPAIRED"

STRONG_WIN_R = 0.75
WEAK_WIN_R = 0.10
NOISE_R = 0.10
HARD_LOSS_R = -1.00

def utc_now():
    return datetime.now(timezone.utc).isoformat()

def qid(x):
    return '"' + str(x).replace('"', '""') + '"'

def fnum(x, default=None):
    try:
        if x is None:
            return default
        v = float(x)
        if math.isnan(v) or math.isinf(v):
            return default
        return v
    except Exception:
        return default

def exists(con, table):
    try:
        return con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,)
        ).fetchone() is not None
    except Exception:
        return False

def cols(con, table):
    if not exists(con, table):
        return []
    return [r[1] for r in con.execute(f"PRAGMA table_info({qid(table)})")]

def many(con, sql, args=()):
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute(sql, args).fetchall()]
    except Exception:
        return []

def classify_trade(row: Dict[str, Any]) -> str:
    status = str(row.get("status") or "").upper()
    closed_at = row.get("closed_at")
    net_r = fnum(row.get("net_r"), fnum(row.get("net_pnl_r"), fnum(row.get("pnl_r"))))

    if status in {"OPEN", "ACTIVE", "RUNNING"} or not closed_at:
        return "OPEN"
    if net_r is None:
        return "UNKNOWN"
    if net_r >= STRONG_WIN_R:
        return "STRONG_WIN"
    if net_r > WEAK_WIN_R:
        return "WEAK_WIN"
    if net_r >= -NOISE_R:
        return "NOISE"
    if net_r <= HARD_LOSS_R:
        return "HARD_LOSS"
    return "LOSS"

def load_trades(con, limit=800) -> List[Dict[str, Any]]:
    rows = []

    if exists(con, "paper_micro_canary_positions_v11"):
        rows.extend(many(con, """
            SELECT id, opened_at, closed_at, symbol, side, setup, status,
                   entry_price, exit_price, size_usd,
                   COALESCE(net_pnl_r,pnl_r,0) AS net_r,
                   COALESCE(net_pnl_usd,pnl_usd,0) AS net_usd,
                   reason, manager_state,
                   'paper_micro_canary' AS source
            FROM paper_micro_canary_positions_v11
            ORDER BY COALESCE(closed_at, opened_at) DESC, id DESC
            LIMIT ?
        """, (limit,)))

    if exists(con, "trades"):
        c = cols(con, "trades")
        if "symbol" in c and "side" in c:
            ts_col = next((x for x in ["closed_at", "ts", "opened_at", "created_at", "updated_at", "id"] if x in c), "id")
            pnl_r_col = next((x for x in ["net_pnl_r", "pnl_r", "r", "result_r"] if x in c), None)
            pnl_usd_col = next((x for x in ["net_pnl_usd", "pnl_usd", "net_usd", "pnl"] if x in c), None)

            rows.extend(many(con, f"""
                SELECT id,
                       NULL AS opened_at,
                       {qid(ts_col)} AS closed_at,
                       symbol,
                       side,
                       {"setup" if "setup" in c else "''"} AS setup,
                       {"status" if "status" in c else "'CLOSED'"} AS status,
                       {"entry_price" if "entry_price" in c else "NULL"} AS entry_price,
                       {"exit_price" if "exit_price" in c else "NULL"} AS exit_price,
                       {"size_usd" if "size_usd" in c else "NULL"} AS size_usd,
                       {qid(pnl_r_col) if pnl_r_col else "NULL"} AS net_r,
                       {qid(pnl_usd_col) if pnl_usd_col else "NULL"} AS net_usd,
                       {"reason" if "reason" in c else "''"} AS reason,
                       '' AS manager_state,
                       'trades' AS source
                FROM trades
                ORDER BY {qid(ts_col)} DESC
                LIMIT ?
            """, (limit,)))

    seen = set()
    out = []
    for r in rows:
        key = (r.get("source"), r.get("id"))
        if key in seen:
            continue
        seen.add(key)
        r["quality_label"] = classify_trade(r)
        out.append(r)

    out.sort(key=lambda r: str(r.get("closed_at") or r.get("opened_at") or ""), reverse=True)
    return out[:limit]

def mean(xs):
    return sum(xs) / len(xs) if xs else None

def stdev(xs):
    if len(xs) < 2:
        return None
    m = mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))

def cumulative_curve(xs):
    out, s = [], 0.0
    for x in xs:
        s += x
        out.append(s)
    return out

def max_drawdown(curve):
    if not curve:
        return 0.0
    peak = curve[0]
    dd = 0.0
    for x in curve:
        peak = max(peak, x)
        dd = min(dd, x - peak)
    return dd

def profit_factor(rs):
    gw = sum(x for x in rs if x > WEAK_WIN_R)
    gl = abs(sum(x for x in rs if x < -NOISE_R))
    return None if gl <= 0 else gw / gl

def aggregate(rows, scope="GLOBAL", symbol=None, side=None, setup=None):
    closed = [r for r in rows if classify_trade(r) not in {"OPEN", "UNKNOWN"}]
    rs = [fnum(r.get("net_r")) for r in closed]
    rs = [x for x in rs if x is not None]

    wins = [x for x in rs if x > WEAK_WIN_R]
    strong = [x for x in rs if x >= STRONG_WIN_R]
    weak = [x for x in rs if WEAK_WIN_R < x < STRONG_WIN_R]
    noise = [x for x in rs if -NOISE_R <= x <= WEAK_WIN_R]
    losses = [x for x in rs if x < -NOISE_R]
    hard = [x for x in rs if x <= HARD_LOSS_R]

    avg_win = mean(wins)
    avg_loss = abs(mean(losses)) if losses else None
    active = len(wins) + len(losses)
    wr = len(wins) / active if active else None
    payoff_ratio = avg_win / avg_loss if avg_win is not None and avg_loss else None
    be = avg_loss / (avg_win + avg_loss) if avg_win is not None and avg_loss else None
    exp_all = mean(rs)
    total_r = sum(rs) if rs else 0.0
    total_usd = sum(fnum(r.get("net_usd"), 0.0) or 0.0 for r in closed)

    sd = stdev(rs)
    lcb95 = exp_all - 1.96 * sd / math.sqrt(len(rs)) if exp_all is not None and sd is not None and len(rs) >= 2 else None
    pf = profit_factor(rs)

    reasons = []
    block = 0

    if len(rs) < 10:
        health = "INSUFFICIENT_SAMPLE"
        block = 1
        reasons.append("closed_n_lt_10")
    elif exp_all is not None and exp_all > 0 and lcb95 is not None and lcb95 > 0 and pf is not None and pf >= 1.25 and wr is not None and be is not None and wr > be:
        health = "GOOD"
    elif exp_all is not None and exp_all > 0 and pf is not None and pf >= 1.0:
        health = "WATCH"
        reasons.append("positive_but_not_robust")
    else:
        health = "BAD"
        block = 1
        if exp_all is not None and exp_all <= 0:
            reasons.append("expectancy_not_positive")
        if lcb95 is not None and lcb95 <= 0:
            reasons.append("lcb95_not_positive")
        if pf is not None and pf < 1.0:
            reasons.append("profit_factor_below_1")
        if wr is not None and be is not None and wr <= be:
            reasons.append("winrate_below_breakeven")

    return {
        "ts": utc_now(),
        "version": VERSION,
        "scope": scope,
        "symbol": symbol,
        "side": side,
        "setup": setup,
        "closed_n": len(rs),
        "wins": len(wins),
        "strong_wins": len(strong),
        "weak_wins": len(weak),
        "noise": len(noise),
        "losses": len(losses),
        "hard_losses": len(hard),
        "avg_win_r": avg_win,
        "avg_loss_r": avg_loss,
        "payoff_ratio": payoff_ratio,
        "winrate": wr,
        "breakeven_winrate": be,
        "expectancy_r": exp_all,
        "expectancy_all_r": exp_all,
        "lcb95_r": lcb95,
        "profit_factor": pf,
        "max_dd_r": max_drawdown(cumulative_curve(rs)),
        "total_net_r": total_r,
        "total_net_usd": total_usd,
        "payoff_health": health,
        "promotion_block": block,
        "reasons": reasons,
    }

def create_tables(con):
    con.execute(f"""
        CREATE TABLE IF NOT EXISTS {qid(SNAPSHOT_TABLE)} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            version TEXT NOT NULL,
            scope TEXT NOT NULL,
            symbol TEXT,
            side TEXT,
            setup TEXT,
            closed_n INTEGER,
            wins INTEGER,
            strong_wins INTEGER,
            weak_wins INTEGER,
            noise INTEGER,
            losses INTEGER,
            hard_losses INTEGER,
            avg_win_r REAL,
            avg_loss_r REAL,
            payoff_ratio REAL,
            winrate REAL,
            breakeven_winrate REAL,
            expectancy_r REAL,
            expectancy_all_r REAL,
            lcb95_r REAL,
            profit_factor REAL,
            max_dd_r REAL,
            total_net_r REAL,
            total_net_usd REAL,
            payoff_health TEXT,
            promotion_block INTEGER,
            reasons TEXT,
            payload TEXT
        )
    """)

def save_snapshot(con, s):
    con.execute(f"""
        INSERT INTO {qid(SNAPSHOT_TABLE)}
        (ts, version, scope, symbol, side, setup, closed_n, wins, strong_wins,
         weak_wins, noise, losses, hard_losses, avg_win_r, avg_loss_r,
         payoff_ratio, winrate, breakeven_winrate, expectancy_r,
         expectancy_all_r, lcb95_r, profit_factor, max_dd_r,
         total_net_r, total_net_usd, payoff_health, promotion_block, reasons, payload)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        s["ts"], s["version"], s["scope"], s.get("symbol"), s.get("side"), s.get("setup"),
        s["closed_n"], s["wins"], s["strong_wins"], s["weak_wins"], s["noise"],
        s["losses"], s["hard_losses"], s["avg_win_r"], s["avg_loss_r"],
        s["payoff_ratio"], s["winrate"], s["breakeven_winrate"], s["expectancy_r"],
        s["expectancy_all_r"], s["lcb95_r"], s["profit_factor"], s["max_dd_r"],
        s["total_net_r"], s["total_net_usd"], s["payoff_health"],
        s["promotion_block"], ",".join(s["reasons"]), json.dumps(s, sort_keys=True)
    ))

def write_summary(s, trades):
    OUT.mkdir(parents=True, exist_ok=True)
    lines = [
        "# V18.6 Payoff Intelligence",
        "",
        f"- UTC: `{utc_now()}`",
        f"- Health: `{s.get('payoff_health')}`",
        f"- Closed n: `{s.get('closed_n')}`",
        f"- Avg win R: `{s.get('avg_win_r')}`",
        f"- Avg loss R: `{s.get('avg_loss_r')}`",
        f"- Payoff ratio: `{s.get('payoff_ratio')}`",
        f"- Winrate: `{s.get('winrate')}`",
        f"- Breakeven WR: `{s.get('breakeven_winrate')}`",
        f"- Expectancy R: `{s.get('expectancy_all_r')}`",
        f"- LCB95 R: `{s.get('lcb95_r')}`",
        f"- Profit factor: `{s.get('profit_factor')}`",
        f"- MaxDD R: `{s.get('max_dd_r')}`",
        f"- Promotion block: `{s.get('promotion_block')}`",
        f"- Reasons: `{','.join(s.get('reasons') or []) or 'none'}`",
        "",
        "## Last trades",
    ]
    for r in trades[:10]:
        lines.append(f"- `{r.get('quality_label')}` {r.get('symbol')} {r.get('side')} {r.get('setup')} netR={r.get('net_r')} netUSD={r.get('net_usd')}")
    (OUT / "payoff_summary.md").write_text("\n".join(lines))

def run_once():
    if not DB.exists():
        raise RuntimeError("DB_MISSING")
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    create_tables(con)
    trades = load_trades(con, 800)
    snap = aggregate(trades, "GLOBAL")
    save_snapshot(con, snap)
    con.commit()
    con.close()
    write_summary(snap, trades)
    return snap, trades

def main():
    snap, trades = run_once()
    print("===== V18.6 PAYOFF INTELLIGENCE REPAIRED =====")
    print("health:", snap["payoff_health"])
    print("closed_n:", snap["closed_n"])
    print("expectancy_r:", snap["expectancy_all_r"])
    print("lcb95_r:", snap["lcb95_r"])
    print("profit_factor:", snap["profit_factor"])
    print("payoff_ratio:", snap["payoff_ratio"])
    print("promotion_block:", snap["promotion_block"])
    print("summary: data/v18_6_payoff/payoff_summary.md")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
