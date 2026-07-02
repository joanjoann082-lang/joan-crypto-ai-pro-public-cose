#!/usr/bin/env python3
from __future__ import annotations

import curses
import math
import sqlite3
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

DB = Path("data/joanbot_v14.sqlite")
VERSION = "V18.2 VISUAL TERMUX COMMAND CENTER"
REFRESH_SEC = 60

SERVICE_PATTERNS = {
    "Brain": "run_v17_5_1_quant_brain_forever|quant_brain_v17_5_1",
    "Promo": "run_v17_6_1_promotion_controller_forever|promotion_controller_v17_6_1",
    "Adapter": "run_v17_8_1_paper_canary_adapter_forever|institutional_paper_canary_adapter_v17_8_1",
    "Macro": "run_v18_2_market_context_forever|v18_2_market_context_collector",
}

BLOCKS = "▁▂▃▄▅▆▇█"


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S UTC")


def qid(x: str) -> str:
    return '"' + str(x).replace('"', '""') + '"'


def fnum(x: Any, default=None):
    try:
        if x is None:
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
        return "NO DATA"
    if v >= 1000:
        return f"{v:,.0f}"
    if v >= 10:
        return f"{v:,.2f}"
    return f"{v:.5f}"


def fmt_pct(x: Any) -> str:
    v = fnum(x)
    if v is None:
        return "NO DATA"
    return f"{v*100:+.2f}%"


def fmt_r(x: Any) -> str:
    v = fnum(x)
    if v is None:
        return "N/A"
    return f"{v:+.2f}R"


def fmt_num(x: Any, nd=2) -> str:
    v = fnum(x)
    if v is None:
        return "NO DATA"
    return f"{v:,.{nd}f}"


def short(s: Any, n: int) -> str:
    s = "" if s is None else str(s)
    return s if len(s) <= n else s[: max(0, n - 1)] + "…"


def exists(con, table):
    return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def cols(con, table):
    if not exists(con, table):
        return []
    return [r[1] for r in con.execute(f"PRAGMA table_info({qid(table)})")]


def one(con, sql, args=()):
    try:
        con.row_factory = sqlite3.Row
        r = con.execute(sql, args).fetchone()
        return dict(r) if r else None
    except Exception:
        return None


def many(con, sql, args=()):
    try:
        con.row_factory = sqlite3.Row
        return [dict(r) for r in con.execute(sql, args).fetchall()]
    except Exception:
        return []


def ps_count(pattern):
    try:
        out = subprocess.check_output(
            ["sh", "-c", f"ps -ef | grep -Ei '{pattern}' | grep -v grep | wc -l"],
            text=True,
        )
        return int(out.strip() or 0)
    except Exception:
        return 0


def latest_market_price(con, symbol):
    if exists(con, "derivatives_context_v18_2"):
        r = one(con, """
            SELECT value, ts
            FROM derivatives_context_v18_2
            WHERE symbol=? AND metric='mark_price' AND value IS NOT NULL
            ORDER BY id DESC LIMIT 1
        """, (symbol,))
        if r:
            return r["value"], r["ts"], "binance"

    for table in ["market_snapshots", "features", "decisions"]:
        if not exists(con, table):
            continue
        c = cols(con, table)
        if "symbol" not in c:
            continue
        pc = next((x for x in ["price", "close", "last_price", "mark_price", "mid_price", "c", "entry_price"] if x in c), None)
        tc = next((x for x in ["ts", "timestamp", "time", "created_at", "updated_at"] if x in c), None)
        if not pc:
            continue
        order = f"{qid(tc)} DESC" if tc else "rowid DESC"
        r = one(con, f"""
            SELECT {qid(pc)} AS price, {qid(tc) if tc else 'NULL'} AS ts
            FROM {qid(table)}
            WHERE UPPER(COALESCE(symbol,''))=?
            ORDER BY {order}
            LIMIT 1
        """, (symbol,))
        if r and r.get("price") is not None:
            return r["price"], r.get("ts"), table

    return None, None, None


def price_series(con, symbol, limit=80):
    vals = []

    if exists(con, "market_snapshots"):
        c = cols(con, "market_snapshots")
        if "symbol" in c:
            pc = next((x for x in ["price", "close", "last_price", "mark_price", "mid_price", "c"] if x in c), None)
            tc = next((x for x in ["ts", "timestamp", "time", "created_at", "updated_at"] if x in c), None)
            if pc:
                order = f"{qid(tc)} DESC" if tc else "rowid DESC"
                rows = many(con, f"""
                    SELECT {qid(pc)} AS p
                    FROM market_snapshots
                    WHERE UPPER(COALESCE(symbol,''))=?
                    ORDER BY {order}
                    LIMIT ?
                """, (symbol, limit))
                vals = [fnum(r.get("p")) for r in rows]
                vals = [v for v in vals if v and v > 0]
                vals = list(reversed(vals))

    if len(vals) < 5 and exists(con, "derivatives_context_v18_2"):
        rows = many(con, """
            SELECT value AS p
            FROM derivatives_context_v18_2
            WHERE symbol=? AND metric='mark_price' AND value IS NOT NULL
            ORDER BY id DESC LIMIT ?
        """, (symbol, limit))
        vals = [fnum(r.get("p")) for r in rows]
        vals = [v for v in vals if v and v > 0]
        vals = list(reversed(vals))

    return vals


def spark(vals: List[float], width: int) -> str:
    if not vals:
        return "NO DATA".ljust(width)
    if len(vals) > width:
        step = len(vals) / width
        sampled = []
        for i in range(width):
            sampled.append(vals[int(i * step)])
        vals = sampled

    lo, hi = min(vals), max(vals)
    if hi <= lo:
        return "─" * min(width, len(vals))

    out = ""
    for v in vals:
        idx = int((v - lo) / (hi - lo) * (len(BLOCKS) - 1))
        out += BLOCKS[max(0, min(len(BLOCKS) - 1, idx))]
    return out.ljust(width)


def bar_ratio(ratio: Any, width: int, fill="█", empty="░") -> str:
    v = fnum(ratio, 0.0) or 0.0
    v = max(0.0, min(1.0, v))
    n = int(v * width)
    return fill * n + empty * (width - n)


def r_bar(r: Any, width: int) -> str:
    v = fnum(r, 0.0) or 0.0
    v = max(-1.5, min(1.5, v))
    mid = width // 2
    arr = ["░"] * width
    arr[mid] = "│"
    if v >= 0:
        n = int((v / 1.5) * (width - mid - 1))
        for i in range(mid + 1, min(width, mid + 1 + n)):
            arr[i] = "█"
    else:
        n = int((abs(v) / 1.5) * mid)
        for i in range(max(0, mid - n), mid):
            arr[i] = "█"
    return "".join(arr)


def trade_progress(entry, stop, tp, price, width):
    e, s, t, p = map(fnum, [entry, stop, tp, price])
    if None in (e, s, t, p):
        return "NO DATA".ljust(width)
    lo, hi = min(s, t), max(s, t)
    if hi <= lo:
        return "NO RANGE".ljust(width)
    ratio = (p - lo) / (hi - lo)
    ratio = max(0.0, min(1.0, ratio))
    n = int(ratio * (width - 1))
    arr = ["─"] * width
    arr[0] = "S"
    arr[-1] = "T"
    arr[n] = "●"
    return "".join(arr)


def latest_macro(con, label):
    if not exists(con, "market_context_v18_2"):
        return None
    return one(con, """
        SELECT value, change_pct, status, source, ts
        FROM market_context_v18_2
        WHERE label=?
        ORDER BY id DESC LIMIT 1
    """, (label,))


def latest_deriv(con, symbol, metric):
    if not exists(con, "derivatives_context_v18_2"):
        return None
    return one(con, """
        SELECT value, status, source, ts
        FROM derivatives_context_v18_2
        WHERE symbol=? AND metric=?
        ORDER BY id DESC LIMIT 1
    """, (symbol, metric))


def load():
    d = {
        "db": "MISSING",
        "services": {},
        "btc": {},
        "eth": {},
        "btc_series": [],
        "macro": {},
        "deriv": {},
        "open": [],
        "pnl": {},
        "quant": {},
        "adapter": {},
        "errors": [],
    }

    if not DB.exists():
        return d

    try:
        con = sqlite3.connect(DB)
        con.row_factory = sqlite3.Row
        d["db"] = con.execute("PRAGMA quick_check").fetchone()[0]

        d["services"] = {k: ps_count(v) for k, v in SERVICE_PATTERNS.items()}

        px, ts, src = latest_market_price(con, "BTCUSDT")
        d["btc"] = {"price": px, "ts": ts, "src": src}
        d["btc_series"] = price_series(con, "BTCUSDT", 100)

        epx, ets, esrc = latest_market_price(con, "ETHUSDT")
        d["eth"] = {"price": epx, "ts": ets, "src": esrc}

        for label in ["VIX", "SPX", "NASDAQ", "DXY", "US10Y", "FEAR_GREED"]:
            d["macro"][label] = latest_macro(con, label)

        for metric in ["funding_rate", "open_interest", "price_change_pct_24h", "volume_24h"]:
            d["deriv"][metric] = latest_deriv(con, "BTCUSDT", metric)

        if exists(con, "paper_micro_canary_positions_v11"):
            d["open"] = many(con, """
                SELECT id, opened_at, symbol, side, setup, status, entry_price, stop_price,
                       take_profit_price, size_usd, pnl_r, net_pnl_r, mfe_r, mae_r, manager_state
                FROM paper_micro_canary_positions_v11
                WHERE UPPER(COALESCE(status,'')) IN ('OPEN','ACTIVE','RUNNING')
                   OR (closed_at IS NULL AND opened_at IS NOT NULL)
                ORDER BY id DESC LIMIT 3
            """)

            d["pnl"] = one(con, """
                SELECT COUNT(*) AS n,
                       SUM(COALESCE(net_pnl_usd,pnl_usd,0)) AS pnl_usd,
                       SUM(COALESCE(net_pnl_r,pnl_r,0)) AS pnl_r,
                       AVG(CASE WHEN COALESCE(net_pnl_r,pnl_r,0)>0 THEN 1.0 ELSE 0.0 END) AS winrate
                FROM paper_micro_canary_positions_v11
                WHERE closed_at IS NOT NULL OR UPPER(COALESCE(status,''))='CLOSED'
            """) or {}

        if exists(con, "institutional_quant_brain_v17_5_1"):
            tsrow = one(con, "SELECT MAX(ts) AS ts FROM institutional_quant_brain_v17_5_1")
            latest = tsrow["ts"] if tsrow else None
            states = many(con, """
                SELECT authority_state, COUNT(*) AS n
                FROM institutional_quant_brain_v17_5_1
                WHERE ts=?
                GROUP BY authority_state
            """, (latest,))
            top = one(con, """
                SELECT symbol, side, setup, brain_score, robust_mean_r, institutional_lcb_r, profit_factor
                FROM institutional_quant_brain_v17_5_1
                WHERE ts=?
                ORDER BY brain_score DESC LIMIT 1
            """, (latest,))
            d["quant"] = {"ts": latest, "states": states, "top": top}

        if exists(con, "institutional_paper_canary_adapter_health_v17_8_1"):
            d["adapter"] = one(con, """
                SELECT ts, pending_intents, opened_positions, managed_positions,
                       closed_positions, rejected_intents, errors
                FROM institutional_paper_canary_adapter_health_v17_8_1
                ORDER BY id DESC LIMIT 1
            """) or {}

        con.close()
    except Exception as e:
        d["errors"].append(repr(e))

    return d


def init_colors():
    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(1, curses.COLOR_GREEN, -1)
    curses.init_pair(2, curses.COLOR_RED, -1)
    curses.init_pair(3, curses.COLOR_YELLOW, -1)
    curses.init_pair(4, curses.COLOR_CYAN, -1)
    curses.init_pair(5, curses.COLOR_MAGENTA, -1)
    curses.init_pair(6, curses.COLOR_WHITE, -1)
    return {
        "green": curses.color_pair(1),
        "red": curses.color_pair(2),
        "yellow": curses.color_pair(3),
        "cyan": curses.color_pair(4),
        "magenta": curses.color_pair(5),
        "white": curses.color_pair(6),
    }


def add(stdscr, y, x, text, attr=0):
    h, w = stdscr.getmaxyx()
    if y < 0 or y >= h or x >= w:
        return
    try:
        stdscr.addnstr(y, x, str(text), max(0, w - x - 1), attr)
    except Exception:
        pass


def box_title(stdscr, y, title, color):
    h, w = stdscr.getmaxyx()
    add(stdscr, y, 0, "═" * (w - 1), color)
    add(stdscr, y, 2, f" {title} ", color | curses.A_BOLD)


def metric_line(stdscr, y, label, value, bar=None, color=0):
    add(stdscr, y, 2, f"{label:<11}", curses.A_BOLD)
    add(stdscr, y, 14, value, color)
    if bar:
        add(stdscr, y, 28, bar, color)


def draw(stdscr):
    curses.curs_set(0)
    stdscr.nodelay(True)
    C = init_colors()

    data = load()
    last = time.time()

    while True:
        if time.time() - last >= REFRESH_SEC:
            data = load()
            last = time.time()

        d = data
        stdscr.erase()
        h, w = stdscr.getmaxyx()
        graph_w = max(18, w - 4)
        bar_w = max(16, w - 30)

        add(stdscr, 0, 0, " " * (w - 1), curses.A_REVERSE)
        add(stdscr, 0, 1, short(f"{VERSION} | {utc_now()} | q sortir | r refresh", w - 3), curses.A_REVERSE | curses.A_BOLD)

        y = 2

        # BTC hero
        btc = d["btc"]
        px = fnum(btc.get("price"))
        series = d["btc_series"]
        last_old = series[0] if len(series) > 2 else None
        chg = (px / last_old - 1.0) if px and last_old else None
        col = C["green"] if (chg or 0) >= 0 else C["red"]

        box_title(stdscr, y, "BTCUSDT", C["cyan"])
        y += 1
        add(stdscr, y, 2, fmt_price(px), col | curses.A_BOLD)
        add(stdscr, y, max(22, w - 18), fmt_pct(chg), col | curses.A_BOLD)
        y += 1
        add(stdscr, y, 2, spark(series, graph_w), col)
        y += 1
        add(stdscr, y, 2, f"src={btc.get('src') or 'NO DATA'}  ETH={fmt_price(d['eth'].get('price'))}")
        y += 2

        # System row
        box_title(stdscr, y, "SYSTEM", C["white"])
        y += 1
        dbcol = C["green"] if d["db"] == "ok" else C["red"]
        add(stdscr, y, 2, f"DB:{d['db']}", dbcol | curses.A_BOLD)
        x = 12
        for k, v in d["services"].items():
            ok = v >= 1
            add(stdscr, y, x, f"{k}:{'OK' if ok else 'OFF'}", C["green"] if ok else C["red"])
            x += len(k) + 8
            if x > w - 12:
                y += 1
                x = 2
        y += 2

        # Open trade
        box_title(stdscr, y, "OPEN PAPER TRADE", C["green"])
        y += 1
        if d["open"]:
            r = d["open"][0]
            current = px if r.get("symbol") == "BTCUSDT" else d["eth"].get("price")
            rr = fnum(r.get("net_pnl_r"), fnum(r.get("pnl_r"), 0.0)) or 0.0
            rc = C["green"] if rr >= 0 else C["red"]

            add(stdscr, y, 2, short(f"#{r['id']} {r['symbol']} {r['side']} {r['setup']}", w - 4), curses.A_BOLD)
            y += 1
            metric_line(stdscr, y, "R now", fmt_r(rr), r_bar(rr, bar_w), rc)
            y += 1
            metric_line(
                stdscr,
                y,
                "SL→TP",
                "",
                trade_progress(r.get("entry_price"), r.get("stop_price"), r.get("take_profit_price"), current, bar_w + 10),
                C["cyan"],
            )
            y += 1
            add(stdscr, y, 2, f"Entry {fmt_price(r.get('entry_price'))} | Now {fmt_price(current)}")
            y += 1
            add(stdscr, y, 2, f"SL {fmt_price(r.get('stop_price'))} | TP {fmt_price(r.get('take_profit_price'))} | Size {fmt_num(r.get('size_usd'),2)}$")
            y += 1
            add(stdscr, y, 2, f"MFE {fmt_r(r.get('mfe_r'))} | MAE {fmt_r(r.get('mae_r'))}")
            y += 1
            add(stdscr, y, 2, short(str(r.get("manager_state")), w - 4), C["cyan"])
            y += 1
        else:
            add(stdscr, y, 2, "No open paper trade", C["yellow"])
            y += 1
        y += 1

        # Macro visual
        box_title(stdscr, y, "MACRO / RISK", C["yellow"])
        y += 1
        vix = d["macro"].get("VIX") or {}
        fng = d["macro"].get("FEAR_GREED") or {}
        dxy = d["macro"].get("DXY") or {}
        nq = d["macro"].get("NASDAQ") or {}

        vixv = fnum(vix.get("value"))
        vix_ratio = None if vixv is None else min(1.0, max(0.0, (vixv - 10) / 30))
        metric_line(stdscr, y, "VIX", fmt_num(vixv, 2), bar_ratio(vix_ratio, bar_w) if vix_ratio is not None else "NO DATA", C["red"] if vixv and vixv > 22 else C["green"])
        y += 1
        fngv = fnum(fng.get("value"))
        metric_line(stdscr, y, "Fear", fmt_num(fngv, 0), bar_ratio((fngv or 0) / 100, bar_w) if fngv is not None else "NO DATA", C["cyan"])
        y += 1
        add(stdscr, y, 2, f"DXY {fmt_num(dxy.get('value'),2)} {fmt_pct(dxy.get('change_pct'))} | Nasdaq {fmt_pct(nq.get('change_pct'))}")
        y += 2

        # Derivatives
        box_title(stdscr, y, "BTC DERIVATIVES", C["magenta"])
        y += 1
        funding = d["deriv"].get("funding_rate") or {}
        oi = d["deriv"].get("open_interest") or {}
        ch24 = d["deriv"].get("price_change_pct_24h") or {}
        vol24 = d["deriv"].get("volume_24h") or {}

        metric_line(stdscr, y, "Funding", fmt_pct(funding.get("value")), bar_ratio(abs(fnum(funding.get("value"),0) or 0) / 0.001, bar_w), C["yellow"])
        y += 1
        add(stdscr, y, 2, f"OI {fmt_num(oi.get('value'),0)} | 24h {fmt_pct(ch24.get('value'))}")
        y += 1
        add(stdscr, y, 2, f"Vol24 {fmt_num(vol24.get('value'),0)}")
        y += 2

        # Quant + perf
        box_title(stdscr, y, "BOT EDGE / PNL", C["blue"])
        y += 1
        states = ", ".join([f"{x.get('authority_state')}={x.get('n')}" for x in d["quant"].get("states", [])])
        add(stdscr, y, 2, short(states or "NO DATA", w - 4))
        y += 1
        top = d["quant"].get("top") or {}
        add(stdscr, y, 2, short(f"Top {top.get('symbol','')} {top.get('side','')} {top.get('setup','')} score={fmt_num(top.get('brain_score'),1)} PF={fmt_num(top.get('profit_factor'),2)}", w - 4))
        y += 1
        p = d["pnl"] or {}
        add(stdscr, y, 2, f"Closed {p.get('n',0)} | PnL {fmt_num(p.get('pnl_usd'),2)}$ | R {fmt_r(p.get('pnl_r'))} | WR {fmt_pct(p.get('winrate'))}")
        y += 2

        # Adapter
        box_title(stdscr, y, "ADAPTER", C["green"])
        y += 1
        a = d["adapter"] or {}
        err = fnum(a.get("errors"), 0) or 0
        ac = C["green"] if err == 0 else C["red"]
        add(stdscr, y, 2, short(f"pending={a.get('pending_intents')} opened={a.get('opened_positions')} managed={a.get('managed_positions')} closed={a.get('closed_positions')} rejected={a.get('rejected_intents')} errors={a.get('errors')}", w - 4), ac)
        y += 1

        # Footer
        add(stdscr, h - 2, 0, " " * (w - 1), curses.A_REVERSE)
        add(stdscr, h - 2, 1, short("q sortir | r refresh | collector macro cada 5 min | dashboard cada 60s", w - 3), curses.A_REVERSE)

        if d["errors"]:
            add(stdscr, h - 3, 1, short("ERR " + str(d["errors"][-1]), w - 3), C["red"])

        stdscr.refresh()

        for _ in range(REFRESH_SEC * 10):
            c = stdscr.getch()
            if c in (ord("q"), ord("Q")):
                return
            if c in (ord("r"), ord("R")):
                data = load()
                last = time.time()
                break
            time.sleep(0.1)


def main():
    curses.wrapper(draw)


if __name__ == "__main__":
    main()
