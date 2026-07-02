#!/usr/bin/env python3
from __future__ import annotations

import sqlite3
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path("/storage/emulated/0/Download/joan_crypto_ai_pro_v14")
DB = ROOT / "data/joanbot_v14.sqlite"
START_EQUITY = 100000.0

BAD_TABLE_TOKENS = ("shadow", "audit", "health", "market_data", "intent", "queue", "log", "cache")

def money(x):
    try:
        return f"{float(x):,.2f}$"
    except Exception:
        return "N/A"

def pct(x):
    try:
        return f"{float(x):+.3f}%"
    except Exception:
        return "N/A"

def bar(value_pct):
    try:
        v = float(value_pct)
    except Exception:
        return "░" * 24

    # escala visual: +/-2% ocupa tota la barra
    scale = max(-2.0, min(2.0, v))
    filled = int(abs(scale) / 2.0 * 24)
    if v >= 0:
        return "▓" * filled + "░" * (24 - filled)
    return "▒" * filled + "░" * (24 - filled)

def tables(con):
    return [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]

def cols(con, table):
    return [r[1] for r in con.execute(f'PRAGMA table_info("{table}")')]

def order_col(cs):
    for c in ("ts", "updated_at", "closed_at", "created_at", "opened_at", "id"):
        if c in cs:
            return c
    return None

def latest_explicit_equity(con):
    candidates = []

    for t in tables(con):
        tl = t.lower()
        if any(x in tl for x in BAD_TABLE_TOKENS):
            continue

        try:
            cs = cols(con, t)
            eq_cols = [
                c for c in cs
                if any(k in c.lower() for k in ("equity", "balance", "account_value", "portfolio_value", "net_liq"))
            ]
            if not eq_cols:
                continue

            oc = order_col(cs)
            order = f'ORDER BY "{oc}" DESC' if oc else ""
            for c in eq_cols:
                row = con.execute(f'SELECT "{c}" FROM "{t}" {order} LIMIT 1').fetchone()
                if not row:
                    continue
                v = row[0]
                if isinstance(v, (int, float)) and 1000 <= abs(float(v)) <= 10000000:
                    score = 0
                    name = f"{t}.{c}".lower()
                    if "equity" in name:
                        score += 100
                    if "balance" in name:
                        score += 60
                    if "account" in name:
                        score += 40
                    if "portfolio" in name:
                        score += 30
                    candidates.append((score, t, c, float(v)))
        except Exception:
            continue

    if not candidates:
        return None

    candidates.sort(reverse=True)
    _, t, c, v = candidates[0]
    return {
        "equity": v,
        "source": f"{t}.{c}",
        "mode": "explicit_equity"
    }

def closed_pnl_source(con):
    candidates = []

    pnl_cols = ("net_pnl_usd", "pnl_usd", "realized_pnl_usd", "realized_pnl")

    for t in tables(con):
        tl = t.lower()
        if any(x in tl for x in ("shadow", "audit", "health", "market", "intent", "queue", "log", "cache")):
            continue

        try:
            cs = cols(con, t)
            available = [c for c in pnl_cols if c in cs]
            if not available:
                continue

            status_col = None
            for sc in ("status", "state", "position_state", "trade_state"):
                if sc in cs:
                    status_col = sc
                    break

            for c in available:
                if status_col:
                    where = f'''
                    WHERE "{c}" IS NOT NULL
                    AND lower(CAST("{status_col}" AS TEXT)) IN
                    ('closed','close','done','finished','tp','sl','take_profit','stop_loss','settled')
                    '''
                else:
                    where = f'WHERE "{c}" IS NOT NULL'

                row = con.execute(f'SELECT SUM("{c}"), COUNT(*) FROM "{t}" {where}').fetchone()
                if not row:
                    continue

                s, n = row
                if s is None or not n:
                    continue

                score = 0
                name = f"{t}.{c}".lower()
                if "paper" in name:
                    score += 100
                if "trade" in name:
                    score += 70
                if "position" in name:
                    score += 70
                if "closed" in name:
                    score += 50
                if c == "net_pnl_usd":
                    score += 40

                candidates.append({
                    "score": score,
                    "table": t,
                    "column": c,
                    "sum": float(s),
                    "n": int(n),
                    "source": f"{t}.{c}"
                })
        except Exception:
            continue

    if not candidates:
        return None

    candidates.sort(key=lambda x: (x["score"], x["n"]), reverse=True)
    return candidates[0]

def open_positions_count(con):
    total = 0
    detail = []

    for t in tables(con):
        tl = t.lower()
        if not any(k in tl for k in ("position", "trade", "paper")):
            continue
        if any(x in tl for x in BAD_TABLE_TOKENS):
            continue

        try:
            cs = cols(con, t)
            status_col = None
            for sc in ("status", "state", "position_state", "trade_state"):
                if sc in cs:
                    status_col = sc
                    break

            if not status_col:
                continue

            n = con.execute(f'''
                SELECT COUNT(*)
                FROM "{t}"
                WHERE lower(CAST("{status_col}" AS TEXT)) IN ('open','opened','active','running')
            ''').fetchone()[0]

            if n:
                total += int(n)
                detail.append(f"{t}:{n}")
        except Exception:
            continue

    return total, detail[:3]

def compute_equity():
    if not DB.exists():
        return {
            "ok": False,
            "error": "DB missing",
            "equity": None,
            "pnl": None,
            "return_pct": None
        }

    try:
        con = sqlite3.connect(str(DB), timeout=20)
        con.row_factory = sqlite3.Row

        explicit = latest_explicit_equity(con)
        pnl_src = closed_pnl_source(con)
        open_n, open_detail = open_positions_count(con)

        if explicit:
            equity = explicit["equity"]
            source = explicit["source"]
            mode = explicit["mode"]
            closed_pnl = pnl_src["sum"] if pnl_src else equity - START_EQUITY
            closed_n = pnl_src["n"] if pnl_src else None
        elif pnl_src:
            closed_pnl = pnl_src["sum"]
            closed_n = pnl_src["n"]
            equity = START_EQUITY + closed_pnl
            source = pnl_src["source"]
            mode = "start_equity_plus_closed_pnl"
        else:
            equity = START_EQUITY
            closed_pnl = 0.0
            closed_n = 0
            source = "fallback_start_equity"
            mode = "fallback"

        con.close()

        pnl = equity - START_EQUITY
        ret = pnl / START_EQUITY * 100.0

        return {
            "ok": True,
            "equity": equity,
            "pnl": pnl,
            "return_pct": ret,
            "closed_pnl": closed_pnl,
            "closed_n": closed_n,
            "open_n": open_n,
            "open_detail": open_detail,
            "source": source,
            "mode": mode
        }

    except Exception as e:
        return {
            "ok": False,
            "error": repr(e),
            "equity": None,
            "pnl": None,
            "return_pct": None
        }

def print_equity_panel():
    r = compute_equity()
    now = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")

    if not r.get("ok"):
        print("╔══════════════════════════════════════════════════════════════════════╗")
        print(f"║ EQUITY PANEL ERROR | {r.get('error')} ")
        print("╚══════════════════════════════════════════════════════════════════════╝")
        return

    ret = r["return_pct"]

    print("╔══════════════════════════════════════════════════════════════════════╗")
    print(
        "║ SIM BALANCE "
        f"{money(r['equity'])} | PnL {money(r['pnl'])} | Return {pct(ret)} | {now}"
    )
    print(
        "║ "
        f"{bar(ret)} | Closed trades: {r.get('closed_n')} | Open: {r.get('open_n')} | Mode: {r.get('mode')}"
    )
    print(
        "║ Source: "
        f"{r.get('source')} | Open detail: {', '.join(r.get('open_detail') or []) or 'none'}"
    )
    print("╚══════════════════════════════════════════════════════════════════════╝")

if __name__ == "__main__":
    print_equity_panel()
