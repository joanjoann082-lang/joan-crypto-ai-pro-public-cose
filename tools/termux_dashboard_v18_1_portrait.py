#!/usr/bin/env python3
from __future__ import annotations

import curses
import json
import math
import sqlite3
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

DB = Path("data/joanbot_v14.sqlite")
VERSION = "V18.1 PORTRAIT TERMUX COMMAND CENTER"
REFRESH_SEC = 60

SYMBOLS = ["BTCUSDT", "ETHUSDT"]

SERVICE_PATTERNS = {
    "Brain": "run_v17_5_1_quant_brain_forever|quant_brain_v17_5_1",
    "Promotion": "run_v17_6_1_promotion_controller_forever|promotion_controller_v17_6_1",
    "Adapter": "run_v17_8_1_paper_canary_adapter_forever|institutional_paper_canary_adapter_v17_8_1",
}

PRICE_TABLE_PRIORITY = [
    "market_snapshots",
    "features",
    "decisions",
]

MACRO_HINTS = {
    "VIX": ["vix", "volatility_index"],
    "DXY": ["dxy", "dollar_index"],
    "SPX": ["spx", "s&p", "spy"],
    "NASDAQ": ["nasdaq", "ndx", "qqq"],
    "FEAR": ["fear", "greed", "fng"],
}

DERIVATIVE_HINTS = {
    "funding": ["funding"],
    "open_interest": ["open_interest", "oi"],
    "liquidations": ["liquidation", "liq"],
    "cvd": ["cvd", "delta"],
}


def utc_now_short() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S UTC")


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


def fmt_price(x: Any) -> str:
    v = fnum(x)
    if v is None:
        return "N/A"
    if v >= 1000:
        return f"{v:,.0f}"
    if v >= 10:
        return f"{v:,.2f}"
    return f"{v:,.4f}"


def fmt_num(x: Any, nd: int = 2) -> str:
    v = fnum(x)
    if v is None:
        return "N/A"
    return f"{v:,.{nd}f}"


def fmt_r(x: Any) -> str:
    v = fnum(x)
    if v is None:
        return "N/A"
    return f"{v:+.3f}R"


def fmt_usd(x: Any) -> str:
    v = fnum(x)
    if v is None:
        return "N/A"
    return f"{v:+,.2f}$"


def short(s: Any, n: int) -> str:
    s = "" if s is None else str(s)
    if len(s) <= n:
        return s
    return s[: max(0, n - 1)] + "…"


def exists(con: sqlite3.Connection, table: str) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone() is not None


def tables(con: sqlite3.Connection) -> List[str]:
    return [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]


def cols(con: sqlite3.Connection, table: str) -> List[str]:
    if not exists(con, table):
        return []
    return [r[1] for r in con.execute(f"PRAGMA table_info({qid(table)})")]


def one(con: sqlite3.Connection, sql: str, args: tuple = ()) -> Optional[Dict[str, Any]]:
    try:
        con.row_factory = sqlite3.Row
        row = con.execute(sql, args).fetchone()
        return dict(row) if row else None
    except Exception:
        return None


def many(con: sqlite3.Connection, sql: str, args: tuple = ()) -> List[Dict[str, Any]]:
    try:
        con.row_factory = sqlite3.Row
        return [dict(r) for r in con.execute(sql, args).fetchall()]
    except Exception:
        return []


def ps_count(pattern: str) -> int:
    try:
        out = subprocess.check_output(
            ["sh", "-c", f"ps -ef | grep -Ei '{pattern}' | grep -v grep | wc -l"],
            text=True,
        )
        return int(out.strip() or 0)
    except Exception:
        return 0


def latest_price(con: sqlite3.Connection, symbol: str) -> Dict[str, Any]:
    price_cols = ["price", "last_price", "mark_price", "mid_price", "close", "c", "entry_price"]
    ts_cols = ["ts", "timestamp", "time", "created_at", "updated_at"]

    for table in PRICE_TABLE_PRIORITY:
        if not exists(con, table):
            continue

        c = cols(con, table)
        if "symbol" not in c:
            continue

        pc = next((x for x in price_cols if x in c), None)
        tc = next((x for x in ts_cols if x in c), None)
        if not pc:
            continue

        order = f"{qid(tc)} DESC" if tc else "rowid DESC"
        row = one(
            con,
            f"""
            SELECT {qid(pc)} AS price, {qid(tc) if tc else "NULL"} AS ts
            FROM {qid(table)}
            WHERE UPPER(COALESCE(symbol,''))=?
            ORDER BY {order}
            LIMIT 1
            """,
            (symbol.upper(),),
        )

        if row and row.get("price") is not None:
            return {"symbol": symbol, "price": row["price"], "ts": row.get("ts"), "src": table}

    return {"symbol": symbol, "price": None, "ts": None, "src": None}


def recent_price_change(con: sqlite3.Connection, symbol: str, n: int = 20) -> Optional[float]:
    table = "market_snapshots"
    if not exists(con, table):
        return None
    c = cols(con, table)
    if "symbol" not in c:
        return None

    pc = next((x for x in ["price", "close", "last_price", "mark_price", "mid_price", "c"] if x in c), None)
    tc = next((x for x in ["ts", "timestamp", "time", "created_at", "updated_at"] if x in c), None)
    if not pc:
        return None

    order = f"{qid(tc)} DESC" if tc else "rowid DESC"
    rows = many(
        con,
        f"""
        SELECT {qid(pc)} AS price
        FROM {qid(table)}
        WHERE UPPER(COALESCE(symbol,''))=?
        ORDER BY {order}
        LIMIT ?
        """,
        (symbol.upper(), n),
    )
    vals = [fnum(r.get("price")) for r in rows]
    vals = [v for v in vals if v and v > 0]
    if len(vals) < 2:
        return None
    last = vals[0]
    old = vals[-1]
    return (last / old) - 1.0 if old else None


def discover_last_metric(con: sqlite3.Connection, label: str, hints: List[str]) -> Dict[str, Any]:
    all_tables = tables(con)
    hints_l = [h.lower() for h in hints]

    candidates = []
    for table in all_tables:
        tl = table.lower()
        c = cols(con, table)
        joined = " ".join([table] + c).lower()

        if not any(h in joined or h in tl for h in hints_l):
            continue

        numeric_cols = []
        for col in c:
            cl = col.lower()
            if any(h in cl for h in hints_l) or cl in {"value", "score", "price", "close", "index_value"}:
                numeric_cols.append(col)

        ts_col = next((x for x in ["ts", "timestamp", "time", "created_at", "updated_at", "date"] if x in c), None)

        for col in numeric_cols[:4]:
            candidates.append((table, col, ts_col))

    for table, col, ts_col in candidates[:20]:
        order = f"{qid(ts_col)} DESC" if ts_col else "rowid DESC"
        try:
            row = one(
                con,
                f"""
                SELECT {qid(col)} AS value, {qid(ts_col) if ts_col else "NULL"} AS ts
                FROM {qid(table)}
                WHERE {qid(col)} IS NOT NULL
                ORDER BY {order}
                LIMIT 1
                """,
            )
            if row and row.get("value") not in (None, ""):
                return {"label": label, "value": row["value"], "ts": row.get("ts"), "src": table}
        except Exception:
            pass

    return {"label": label, "value": None, "ts": None, "src": None}


def latest_by_symbol_metric(con: sqlite3.Connection, symbol: str, hints: List[str]) -> Dict[str, Any]:
    all_tables = tables(con)
    hints_l = [h.lower() for h in hints]

    for table in all_tables:
        c = cols(con, table)
        if "symbol" not in c:
            continue

        joined = " ".join([table] + c).lower()
        if not any(h in joined for h in hints_l):
            continue

        metric_col = next((col for col in c if any(h in col.lower() for h in hints_l)), None)
        if not metric_col:
            metric_col = next((col for col in c if col.lower() in {"value", "score", "amount"}), None)
        if not metric_col:
            continue

        ts_col = next((x for x in ["ts", "timestamp", "time", "created_at", "updated_at"] if x in c), None)
        order = f"{qid(ts_col)} DESC" if ts_col else "rowid DESC"

        row = one(
            con,
            f"""
            SELECT {qid(metric_col)} AS value, {qid(ts_col) if ts_col else "NULL"} AS ts
            FROM {qid(table)}
            WHERE UPPER(COALESCE(symbol,''))=?
              AND {qid(metric_col)} IS NOT NULL
            ORDER BY {order}
            LIMIT 1
            """,
            (symbol.upper(),),
        )
        if row and row.get("value") not in (None, ""):
            return {"value": row["value"], "ts": row.get("ts"), "src": table}

    return {"value": None, "ts": None, "src": None}


def load_data() -> Dict[str, Any]:
    d: Dict[str, Any] = {
        "db": "MISSING",
        "services": {},
        "prices": {},
        "macro": {},
        "derivatives": {},
        "open": [],
        "closed": [],
        "pnl": {},
        "quant": {},
        "promotion": {},
        "adapter": {},
        "intents": [],
        "errors": [],
    }

    if not DB.exists():
        return d

    try:
        con = sqlite3.connect(str(DB))
        con.row_factory = sqlite3.Row
        d["db"] = con.execute("PRAGMA quick_check").fetchone()[0]
    except Exception as e:
        d["db"] = "ERROR"
        d["errors"].append(repr(e))
        return d

    try:
        d["services"] = {
            name: ps_count(pattern) for name, pattern in SERVICE_PATTERNS.items()
        }

        for sym in SYMBOLS:
            px = latest_price(con, sym)
            px["change"] = recent_price_change(con, sym, 30)
            d["prices"][sym] = px

        for label, hints in MACRO_HINTS.items():
            d["macro"][label] = discover_last_metric(con, label, hints)

        for sym in SYMBOLS:
            d["derivatives"][sym] = {}
            for name, hints in DERIVATIVE_HINTS.items():
                d["derivatives"][sym][name] = latest_by_symbol_metric(con, sym, hints)

        if exists(con, "paper_micro_canary_positions_v11"):
            d["open"] = many(
                con,
                """
                SELECT id, opened_at, symbol, side, setup, status,
                       entry_price, exit_price, stop_price, take_profit_price,
                       size_usd, pnl_r, net_pnl_r, mfe_r, mae_r, manager_state, reason
                FROM paper_micro_canary_positions_v11
                WHERE UPPER(COALESCE(status,'')) IN ('OPEN','ACTIVE','RUNNING')
                   OR (closed_at IS NULL AND opened_at IS NOT NULL)
                ORDER BY id DESC
                LIMIT 5
                """,
            )

            d["closed"] = many(
                con,
                """
                SELECT id, closed_at, symbol, side, setup, status,
                       entry_price, exit_price, size_usd, pnl_r, net_pnl_r, reason
                FROM paper_micro_canary_positions_v11
                WHERE closed_at IS NOT NULL OR UPPER(COALESCE(status,''))='CLOSED'
                ORDER BY id DESC
                LIMIT 5
                """,
            )

            d["pnl"] = one(
                con,
                """
                SELECT COUNT(*) AS n,
                       SUM(COALESCE(net_pnl_usd,pnl_usd,0)) AS pnl_usd,
                       SUM(COALESCE(net_pnl_r,pnl_r,0)) AS pnl_r,
                       AVG(CASE WHEN COALESCE(net_pnl_r,pnl_r,0)>0 THEN 1.0 ELSE 0.0 END) AS winrate
                FROM paper_micro_canary_positions_v11
                WHERE closed_at IS NOT NULL OR UPPER(COALESCE(status,''))='CLOSED'
                """,
            ) or {}

        if exists(con, "institutional_quant_brain_v17_5_1"):
            ts = one(con, "SELECT MAX(ts) AS ts FROM institutional_quant_brain_v17_5_1")
            latest = ts.get("ts") if ts else None
            states = many(
                con,
                """
                SELECT authority_state, COUNT(*) AS n
                FROM institutional_quant_brain_v17_5_1
                WHERE ts=?
                GROUP BY authority_state
                """,
                (latest,),
            )
            top = many(
                con,
                """
                SELECT symbol, side, setup, authority_state, brain_score,
                       robust_mean_r, institutional_lcb_r, profit_factor
                FROM institutional_quant_brain_v17_5_1
                WHERE ts=?
                ORDER BY brain_score DESC
                LIMIT 3
                """,
                (latest,),
            )
            d["quant"] = {"ts": latest, "states": states, "top": top}

        if exists(con, "institutional_promotion_controller_v17_6_1"):
            ts = one(con, "SELECT MAX(ts) AS ts FROM institutional_promotion_controller_v17_6_1")
            latest = ts.get("ts") if ts else None
            actions = many(
                con,
                """
                SELECT action, COUNT(*) AS n
                FROM institutional_promotion_controller_v17_6_1
                WHERE ts=?
                GROUP BY action
                """,
                (latest,),
            )
            d["promotion"] = {"ts": latest, "actions": actions}

        if exists(con, "institutional_paper_canary_adapter_health_v17_8_1"):
            d["adapter"] = one(
                con,
                """
                SELECT ts, quick_check, pending_intents, opened_positions,
                       managed_positions, closed_positions, rejected_intents,
                       reconciled_items, errors
                FROM institutional_paper_canary_adapter_health_v17_8_1
                ORDER BY id DESC
                LIMIT 1
                """,
            ) or {}

        if exists(con, "institutional_quant_canary_execution_intents_v17_7_2"):
            d["intents"] = many(
                con,
                """
                SELECT id, ts, intent_state, symbol, side, setup,
                       requested_size_mult, adapter_status
                FROM institutional_quant_canary_execution_intents_v17_7_2
                ORDER BY id DESC
                LIMIT 3
                """,
            )

        con.close()

    except Exception as e:
        d["errors"].append(repr(e))

    return d


def init_colors():
    curses.start_color()
    curses.use_default_colors()
    pairs = {
        "green": 1,
        "red": 2,
        "yellow": 3,
        "cyan": 4,
        "blue": 5,
        "magenta": 6,
        "white": 7,
    }
    curses.init_pair(pairs["green"], curses.COLOR_GREEN, -1)
    curses.init_pair(pairs["red"], curses.COLOR_RED, -1)
    curses.init_pair(pairs["yellow"], curses.COLOR_YELLOW, -1)
    curses.init_pair(pairs["cyan"], curses.COLOR_CYAN, -1)
    curses.init_pair(pairs["blue"], curses.COLOR_BLUE, -1)
    curses.init_pair(pairs["magenta"], curses.COLOR_MAGENTA, -1)
    curses.init_pair(pairs["white"], curses.COLOR_WHITE, -1)
    return {k: curses.color_pair(v) for k, v in pairs.items()}


def add(stdscr, y: int, x: int, text: Any, attr: int = 0):
    h, w = stdscr.getmaxyx()
    if y < 0 or y >= h or x >= w:
        return
    try:
        stdscr.addnstr(y, x, str(text), max(0, w - x - 1), attr)
    except Exception:
        pass


def hline(stdscr, y: int, ch: str = "─", attr: int = 0):
    h, w = stdscr.getmaxyx()
    if y < 0 or y >= h:
        return
    add(stdscr, y, 0, ch * max(1, w - 1), attr)


def pill(value: bool) -> str:
    return "OK" if value else "OFF"


def pnl_bar(v: Any, width: int) -> str:
    x = fnum(v, 0.0) or 0.0
    x = max(-1.0, min(1.0, x))
    fill = int(abs(x) * width)
    if x >= 0:
        return "▰" * fill + "▱" * (width - fill)
    return "▰" * fill + "▱" * (width - fill)


def draw_card_title(stdscr, y: int, title: str, color: int):
    hline(stdscr, y, "─", color)
    add(stdscr, y, 1, f" {title} ", color | curses.A_BOLD)


def format_pct_change(x: Any) -> str:
    v = fnum(x)
    if v is None:
        return "N/A"
    return f"{v*100:+.2f}%"


def draw_dashboard(stdscr):
    curses.curs_set(0)
    stdscr.nodelay(True)
    colors = init_colors()

    last_data = load_data()
    last_load = time.time()

    while True:
        if time.time() - last_load >= REFRESH_SEC:
            last_data = load_data()
            last_load = time.time()

        d = last_data
        stdscr.erase()
        h, w = stdscr.getmaxyx()

        compact = w < 56
        value_w = max(10, min(18, w // 3))

        add(stdscr, 0, 0, " " * (w - 1), curses.A_REVERSE)
        add(
            stdscr,
            0,
            1,
            short(f"{VERSION} | {utc_now_short()} | q sortir | r refresh", w - 3),
            curses.A_REVERSE | curses.A_BOLD,
        )

        y = 2

        # BTC HERO
        btc = d["prices"].get("BTCUSDT", {})
        eth = d["prices"].get("ETHUSDT", {})
        btc_change = fnum(btc.get("change"))
        btc_color = colors["green"] if (btc_change or 0) >= 0 else colors["red"]

        draw_card_title(stdscr, y, "BTC PRICE", colors["cyan"])
        y += 1
        add(stdscr, y, 2, "BTCUSDT", curses.A_BOLD)
        add(stdscr, y, max(14, w - 18), fmt_price(btc.get("price")), btc_color | curses.A_BOLD)
        y += 1
        add(stdscr, y, 2, f"Δ recent: {format_pct_change(btc.get('change'))}", btc_color)
        add(stdscr, y, max(26, w - 20), f"src: {short(btc.get('src'), 12)}")
        y += 1
        add(stdscr, y, 2, f"ETH: {fmt_price(eth.get('price'))}  Δ {format_pct_change(eth.get('change'))}")
        y += 2

        # SYSTEM
        draw_card_title(stdscr, y, "SYSTEM", colors["blue"])
        y += 1
        db_ok = d["db"] == "ok"
        add(stdscr, y, 2, f"DB {pill(db_ok)}", colors["green"] if db_ok else colors["red"])
        sx = 12
        for name, cnt in d["services"].items():
            ok = cnt >= 1
            add(stdscr, y, sx, f"{name}:{pill(ok)}", colors["green"] if ok else colors["red"])
            sx += len(name) + 7
            if sx > w - 12:
                y += 1
                sx = 2
        y += 2

        # OPEN POSITION
        draw_card_title(stdscr, y, "OPEN PAPER CANARY", colors["green"])
        y += 1
        if not d["open"]:
            add(stdscr, y, 2, "No open paper canary", colors["yellow"])
            y += 1
        else:
            r = d["open"][0]
            rr = fnum(r.get("net_pnl_r"), fnum(r.get("pnl_r"), 0.0)) or 0.0
            c = colors["green"] if rr >= 0 else colors["red"]
            add(stdscr, y, 2, short(f"#{r.get('id')} {r.get('symbol')} {r.get('side')} {r.get('setup')}", w - 4), curses.A_BOLD)
            y += 1
            add(stdscr, y, 2, f"R: {fmt_r(rr)}", c | curses.A_BOLD)
            add(stdscr, y, 14, pnl_bar(rr, max(8, w - 24)), c)
            y += 1
            add(stdscr, y, 2, f"Entry {fmt_price(r.get('entry_price'))}")
            add(stdscr, y, max(25, w // 2), f"Size {fmt_num(r.get('size_usd'), 2)}$")
            y += 1
            add(stdscr, y, 2, f"SL {fmt_price(r.get('stop_price'))}")
            add(stdscr, y, max(25, w // 2), f"TP {fmt_price(r.get('take_profit_price'))}")
            y += 1
            add(stdscr, y, 2, f"MFE {fmt_r(r.get('mfe_r'))}  MAE {fmt_r(r.get('mae_r'))}")
            y += 1
            add(stdscr, y, 2, short(str(r.get("manager_state")), w - 4), colors["cyan"])
            y += 1
        y += 1

        # PNL
        draw_card_title(stdscr, y, "PERFORMANCE", colors["magenta"])
        y += 1
        p = d.get("pnl") or {}
        add(stdscr, y, 2, f"Closed: {p.get('n', 0)}")
        add(stdscr, y, max(16, w // 3), f"PnL: {fmt_usd(p.get('pnl_usd'))}")
        add(stdscr, y, max(34, (w * 2) // 3), f"R: {fmt_r(p.get('pnl_r'))}")
        y += 1
        add(stdscr, y, 2, f"Winrate: {format_pct_change(p.get('winrate')) if p.get('winrate') is not None else 'N/A'}")
        y += 2

        # MARKET CONTEXT
        draw_card_title(stdscr, y, "MARKET / MACRO", colors["yellow"])
        y += 1
        macro_order = ["VIX", "DXY", "SPX", "NASDAQ", "FEAR"]
        for i, label in enumerate(macro_order):
            if y >= h - 8:
                break
            m = d["macro"].get(label, {})
            val = fmt_num(m.get("value"), 2)
            src = short(m.get("src") or "N/A", 12)
            add(stdscr, y, 2, f"{label:<7} {val:<12} {src}")
            y += 1

        # DERIVATIVES
        if y < h - 8:
            y += 1
            draw_card_title(stdscr, y, "BTC DERIVATIVES", colors["cyan"])
            y += 1
            btc_der = d["derivatives"].get("BTCUSDT", {})
            for name in ["funding", "open_interest", "liquidations", "cvd"]:
                if y >= h - 5:
                    break
                item = btc_der.get(name, {})
                add(stdscr, y, 2, f"{name:<14} {fmt_num(item.get('value'), 4):<14} {short(item.get('src') or 'N/A', 14)}")
                y += 1

        # QUANT
        if y < h - 6:
            y += 1
            draw_card_title(stdscr, y, "QUANT BRAIN", colors["blue"])
            y += 1
            states = ", ".join([f"{r.get('authority_state')}={r.get('n')}" for r in d["quant"].get("states", [])])
            add(stdscr, y, 2, short(states or "N/A", w - 4))
            y += 1
            top = d["quant"].get("top", [])
            if top and y < h - 4:
                t = top[0]
                add(
                    stdscr,
                    y,
                    2,
                    short(
                        f"Top: {t.get('symbol')} {t.get('side')} {t.get('setup')} score={fmt_num(t.get('brain_score'),1)} LCB={fmt_r(t.get('institutional_lcb_r'))}",
                        w - 4,
                    ),
                    colors["cyan"],
                )
                y += 1

        # ADAPTER
        if y < h - 4:
            y += 1
            draw_card_title(stdscr, y, "ADAPTER", colors["green"])
            y += 1
            a = d.get("adapter") or {}
            add(
                stdscr,
                y,
                2,
                short(
                    f"pending={a.get('pending_intents')} open={a.get('opened_positions')} managed={a.get('managed_positions')} closed={a.get('closed_positions')} errors={a.get('errors')}",
                    w - 4,
                ),
            )
            y += 1

        # Bottom
        add(stdscr, h - 2, 0, " " * (w - 1), curses.A_REVERSE)
        add(stdscr, h - 2, 1, short("q sortir | r refrescar | portrait dashboard | no web required", w - 3), curses.A_REVERSE)

        if d["errors"]:
            add(stdscr, h - 3, 1, short("ERR: " + str(d["errors"][-1]), w - 3), colors["red"])

        stdscr.refresh()

        for _ in range(REFRESH_SEC * 10):
            c = stdscr.getch()
            if c in (ord("q"), ord("Q")):
                return
            if c in (ord("r"), ord("R")):
                last_data = load_data()
                last_load = time.time()
                break
            time.sleep(0.1)


def main():
    curses.wrapper(draw_dashboard)


if __name__ == "__main__":
    main()
