#!/usr/bin/env python3
from __future__ import annotations

import curses, math, sqlite3, subprocess, time
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

DB = Path("data/joanbot_v14.sqlite")
VERSION = "V18.3 VISUAL QUANT COMMAND CENTER PRO"
REFRESH_SEC = 60
BLOCKS = "▁▂▃▄▅▆▇█"

SERVICES = {
    "Brain": "run_v17_5_1_quant_brain_forever|quant_brain_v17_5_1",
    "Promo": "run_v17_6_1_promotion_controller_forever|promotion_controller_v17_6_1",
    "Adapt": "run_v17_8_1_paper_canary_adapter_forever|institutional_paper_canary_adapter_v17_8_1",
    "Macro": "run_v18_2_market_context_forever|v18_2_market_context_collector",
}

def utc_now():
    return datetime.now(timezone.utc).strftime("%H:%M:%S UTC")

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

def clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))

def fmt_price(x):
    v = fnum(x)
    if v is None:
        return "NO DATA"
    if v >= 1000:
        return f"{v:,.0f}"
    if v >= 10:
        return f"{v:,.2f}"
    return f"{v:.5f}"

def fmt_pct(x):
    v = fnum(x)
    if v is None:
        return "NO DATA"
    return f"{v*100:+.2f}%"

def fmt_r(x):
    v = fnum(x)
    if v is None:
        return "N/A"
    return f"{v:+.2f}R"

def fmt_num(x, nd=2):
    v = fnum(x)
    if v is None:
        return "NO DATA"
    return f"{v:,.{nd}f}"

def short(s, n):
    s = "" if s is None else str(s)
    return s if len(s) <= n else s[:max(0, n-1)] + "…"

def exists(con, t):
    return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (t,)).fetchone() is not None

def cols(con, t):
    if not exists(con, t):
        return []
    return [r[1] for r in con.execute(f"PRAGMA table_info({qid(t)})")]

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

def latest_price(con, symbol):
    if exists(con, "derivatives_context_v18_2"):
        r = one(con, """
            SELECT value, ts FROM derivatives_context_v18_2
            WHERE symbol=? AND metric='mark_price' AND value IS NOT NULL
            ORDER BY id DESC LIMIT 1
        """, (symbol,))
        if r:
            return r["value"], r["ts"], "binance"

    for t in ["market_snapshots", "features", "decisions"]:
        if not exists(con, t):
            continue
        c = cols(con, t)
        if "symbol" not in c:
            continue
        pc = next((x for x in ["price","close","last_price","mark_price","mid_price","c","entry_price"] if x in c), None)
        tc = next((x for x in ["ts","timestamp","time","created_at","updated_at"] if x in c), None)
        if not pc:
            continue
        order = f"{qid(tc)} DESC" if tc else "rowid DESC"
        r = one(con, f"""
            SELECT {qid(pc)} AS p, {qid(tc) if tc else 'NULL'} AS ts
            FROM {qid(t)}
            WHERE UPPER(COALESCE(symbol,''))=?
            ORDER BY {order}
            LIMIT 1
        """, (symbol.upper(),))
        if r and r.get("p") is not None:
            return r["p"], r.get("ts"), t
    return None, None, None

def price_series(con, symbol, limit=90):
    vals = []
    if exists(con, "market_snapshots"):
        c = cols(con, "market_snapshots")
        if "symbol" in c:
            pc = next((x for x in ["price","close","last_price","mark_price","mid_price","c"] if x in c), None)
            tc = next((x for x in ["ts","timestamp","time","created_at","updated_at"] if x in c), None)
            if pc:
                order = f"{qid(tc)} DESC" if tc else "rowid DESC"
                rows = many(con, f"""
                    SELECT {qid(pc)} AS p
                    FROM market_snapshots
                    WHERE UPPER(COALESCE(symbol,''))=?
                    ORDER BY {order}
                    LIMIT ?
                """, (symbol.upper(), limit))
                vals = [fnum(r.get("p")) for r in rows]
                vals = [v for v in vals if v and v > 0]
                vals = list(reversed(vals))

    if len(vals) < 5 and exists(con, "derivatives_context_v18_2"):
        rows = many(con, """
            SELECT value AS p FROM derivatives_context_v18_2
            WHERE symbol=? AND metric='mark_price' AND value IS NOT NULL
            ORDER BY id DESC LIMIT ?
        """, (symbol.upper(), limit))
        vals = [fnum(r.get("p")) for r in rows]
        vals = [v for v in vals if v and v > 0]
        vals = list(reversed(vals))

    return vals

def spark(vals, width):
    if not vals:
        return "NO DATA".ljust(width)
    if len(vals) > width:
        step = len(vals) / width
        vals = [vals[int(i * step)] for i in range(width)]
    lo, hi = min(vals), max(vals)
    if hi <= lo:
        return "─" * min(width, len(vals))
    out = ""
    for v in vals:
        idx = int((v - lo) / (hi - lo) * (len(BLOCKS)-1))
        out += BLOCKS[max(0, min(len(BLOCKS)-1, idx))]
    return out.ljust(width)

def bar01(v, width):
    v = fnum(v, 0.0) or 0.0
    v = clamp(v)
    n = int(v * width)
    return "█" * n + "░" * (width - n)

def rbar(r, width):
    v = fnum(r, 0.0) or 0.0
    v = clamp(v, -1.5, 1.5)
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

def trade_line(entry, sl, tp2, price, width):
    e, s, t, p = map(fnum, [entry, sl, tp2, price])
    if None in (e, s, t, p):
        return "NO DATA".ljust(width)
    lo, hi = min(s, t), max(s, t)
    if hi <= lo:
        return "NO RANGE".ljust(width)
    ratio = clamp((p - lo) / (hi - lo))
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

def estimate_risk_pct(series):
    if len(series) < 25:
        return 0.006
    rets = []
    for a, b in zip(series[:-1], series[1:]):
        if a and b and a > 0 and b > 0:
            rets.append(abs(math.log(b/a)))
    if len(rets) < 20:
        return 0.006
    rets = sorted(rets)
    q75 = rets[int(0.75 * (len(rets)-1))]
    q90 = rets[int(0.90 * (len(rets)-1))]
    return clamp(2.0*q75 + 0.6*q90, 0.004, 0.018)

def compute_levels(price, side, risk_pct):
    p = fnum(price)
    if p is None:
        return None, None, None
    r = p * risk_pct
    if str(side).upper() == "SHORT":
        sl = p + r
        tp1 = p - 1.0*r
        tp2 = p - 1.8*r
    else:
        sl = p - r
        tp1 = p + 1.0*r
        tp2 = p + 1.8*r
    return sl, tp1, tp2

def maturity_score(row):
    clean = fnum(row.get("clean_n"), fnum(row.get("clean"), 0)) or 0
    live = fnum(row.get("live_n"), fnum(row.get("live"), 0)) or 0
    shadow = fnum(row.get("shadow_n"), fnum(row.get("shadow"), 0)) or 0
    mean = fnum(row.get("robust_mean_r"), fnum(row.get("mean_r"), 0)) or 0
    lcb = fnum(row.get("institutional_lcb_r"), fnum(row.get("inst_lcb"), -0.6)) or -0.6
    pf = fnum(row.get("profit_factor"), 0) or 0
    prob = fnum(row.get("prob_edge_gt_zero"), fnum(row.get("prob0"), 0.5)) or 0.5
    brain = fnum(row.get("brain_score"), fnum(row.get("quant_score"), 0)) or 0

    score = 0
    score += 18 * clamp(clean / 250)
    score += 12 * clamp(live / 20)
    score += 10 * clamp(shadow / 250)
    score += 18 * clamp((mean + 0.005) / 0.065)
    score += 18 * clamp((lcb + 0.55) / 0.55)
    score += 14 * clamp((pf - 0.70) / 1.0)
    score += 10 * clamp((prob - 0.50) / 0.35)
    score += 8 * clamp(brain / 70)
    return round(clamp(score, 0, 100), 1)

def candidate_action(score, row):
    lcb = fnum(row.get("institutional_lcb_r"), -1) or -1
    pf = fnum(row.get("profit_factor"), 0) or 0
    live = fnum(row.get("live_n"), 0) or 0
    if score >= 78 and lcb > -0.10 and pf >= 1.35 and live >= 10:
        return "TRADE POSSIBLE"
    if score >= 58:
        return "CANARY / REVIEW"
    return "NO TRADE"

def load_candidates(con):
    out = []

    if exists(con, "institutional_quant_brain_v17_5_1"):
        c = cols(con, "institutional_quant_brain_v17_5_1")
        ts = one(con, "SELECT MAX(ts) AS ts FROM institutional_quant_brain_v17_5_1")
        latest = ts.get("ts") if ts else None
        order_col = "brain_score" if "brain_score" in c else "quant_score" if "quant_score" in c else "id"
        rows = many(con, f"""
            SELECT *
            FROM institutional_quant_brain_v17_5_1
            WHERE ts=?
            ORDER BY {qid(order_col)} DESC
            LIMIT 8
        """, (latest,))
        out += rows

    if exists(con, "institutional_quant_canary_governance_v17_7_2"):
        c = cols(con, "institutional_quant_canary_governance_v17_7_2")
        ts = one(con, "SELECT MAX(ts) AS ts FROM institutional_quant_canary_governance_v17_7_2")
        latest = ts.get("ts") if ts else None
        rows = many(con, """
            SELECT *
            FROM institutional_quant_canary_governance_v17_7_2
            WHERE ts=?
            ORDER BY quant_score DESC
            LIMIT 4
        """, (latest,))
        out = rows + out

    # dedupe symbol-side-setup
    seen = set()
    final = []
    for r in out:
        k = (r.get("symbol"), r.get("side"), r.get("setup"))
        if k in seen:
            continue
        seen.add(k)
        r["_maturity"] = maturity_score(r)
        final.append(r)

    final.sort(key=lambda x: x.get("_maturity", 0), reverse=True)
    return final[:5]

def load():
    d = {
        "db": "MISSING",
        "services": {},
        "btc": {},
        "eth": {},
        "btc_series": [],
        "eth_series": [],
        "macro": {},
        "deriv": {},
        "open": [],
        "pnl": {},
        "adapter": {},
        "candidates": [],
        "errors": [],
    }

    if not DB.exists():
        return d

    try:
        con = sqlite3.connect(DB)
        con.row_factory = sqlite3.Row
        d["db"] = con.execute("PRAGMA quick_check").fetchone()[0]

        d["services"] = {k: ps_count(v) for k, v in SERVICES.items()}

        bpx, bts, bsrc = latest_price(con, "BTCUSDT")
        epx, ets, esrc = latest_price(con, "ETHUSDT")
        d["btc"] = {"price": bpx, "ts": bts, "src": bsrc}
        d["eth"] = {"price": epx, "ts": ets, "src": esrc}
        d["btc_series"] = price_series(con, "BTCUSDT", 100)
        d["eth_series"] = price_series(con, "ETHUSDT", 100)

        for label in ["VIX", "SPX", "NASDAQ", "DXY", "US10Y", "FEAR_GREED"]:
            d["macro"][label] = latest_macro(con, label)

        for metric in ["funding_rate", "open_interest", "price_change_pct_24h", "volume_24h"]:
            d["deriv"][metric] = latest_deriv(con, "BTCUSDT", metric)

        if exists(con, "paper_micro_canary_positions_v11"):
            d["open"] = many(con, """
                SELECT id, opened_at, symbol, side, setup, status,
                       entry_price, stop_price, take_profit_price,
                       size_usd, pnl_r, net_pnl_r, mfe_r, mae_r, manager_state
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

        if exists(con, "institutional_paper_canary_adapter_health_v17_8_1"):
            d["adapter"] = one(con, """
                SELECT ts, pending_intents, opened_positions, managed_positions,
                       closed_positions, rejected_intents, errors
                FROM institutional_paper_canary_adapter_health_v17_8_1
                ORDER BY id DESC LIMIT 1
            """) or {}

        d["candidates"] = load_candidates(con)

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
    curses.init_pair(5, curses.COLOR_BLUE, -1)
    curses.init_pair(6, curses.COLOR_MAGENTA, -1)
    curses.init_pair(7, curses.COLOR_WHITE, -1)
    return {
        "green": curses.color_pair(1),
        "red": curses.color_pair(2),
        "yellow": curses.color_pair(3),
        "cyan": curses.color_pair(4),
        "blue": curses.color_pair(5),
        "magenta": curses.color_pair(6),
        "white": curses.color_pair(7),
    }

def add(stdscr, y, x, text, attr=0):
    h, w = stdscr.getmaxyx()
    if y < 0 or y >= h or x >= w:
        return
    try:
        stdscr.addnstr(y, x, str(text), max(0, w-x-1), attr)
    except Exception:
        pass

def title(stdscr, y, txt, color):
    h, w = stdscr.getmaxyx()
    add(stdscr, y, 0, "═" * max(1, w-1), color)
    add(stdscr, y, 2, f" {txt} ", color | curses.A_BOLD)

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
        graph_w = max(18, w-4)
        bar_w = max(16, w-30)

        add(stdscr, 0, 0, " " * (w-1), curses.A_REVERSE)
        add(stdscr, 0, 1, short(f"{VERSION} | {utc_now()} | q sortir | r refresh", w-3), curses.A_REVERSE | curses.A_BOLD)

        y = 2

        # BTC + ETH
        btc_px = d["btc"].get("price")
        eth_px = d["eth"].get("price")
        btc_series = d["btc_series"]
        eth_series = d["eth_series"]

        btc_chg = None
        if btc_series and btc_px:
            btc_chg = btc_px / btc_series[0] - 1 if btc_series[0] else None

        title(stdscr, y, "BTC / ETH PRICE", C["cyan"])
        y += 1
        col = C["green"] if (btc_chg or 0) >= 0 else C["red"]
        add(stdscr, y, 2, f"BTC {fmt_price(btc_px)}", col | curses.A_BOLD)
        add(stdscr, y, max(24, w-18), fmt_pct(btc_chg), col | curses.A_BOLD)
        y += 1
        add(stdscr, y, 2, spark(btc_series, graph_w), col)
        y += 1
        add(stdscr, y, 2, f"ETH {fmt_price(eth_px)}", C["magenta"] | curses.A_BOLD)
        y += 1
        add(stdscr, y, 2, spark(eth_series, graph_w), C["magenta"])
        y += 2

        # SYSTEM
        title(stdscr, y, "SYSTEM", C["white"])
        y += 1
        db_col = C["green"] if d["db"] == "ok" else C["red"]
        add(stdscr, y, 2, f"DB:{d['db']}", db_col | curses.A_BOLD)
        x = 12
        for name, cnt in d["services"].items():
            ok = cnt >= 1
            add(stdscr, y, x, f"{name}:{'OK' if ok else 'OFF'}", C["green"] if ok else C["red"])
            x += len(name) + 8
            if x > w - 12:
                y += 1
                x = 2
        y += 2

        # OPEN TRADE
        title(stdscr, y, "OPEN PAPER TRADE", C["green"])
        y += 1
        if d["open"]:
            r = d["open"][0]
            px = btc_px if r.get("symbol") == "BTCUSDT" else eth_px
            rr = fnum(r.get("net_pnl_r"), fnum(r.get("pnl_r"), 0.0)) or 0.0
            rc = C["green"] if rr >= 0 else C["red"]

            add(stdscr, y, 2, short(f"#{r['id']} {r['symbol']} {r['side']} {r['setup']}", w-4), curses.A_BOLD)
            y += 1
            add(stdscr, y, 2, f"R {fmt_r(rr)}", rc | curses.A_BOLD)
            add(stdscr, y, 14, rbar(rr, bar_w), rc)
            y += 1
            add(stdscr, y, 2, "SL→TP")
            add(stdscr, y, 14, trade_line(r.get("entry_price"), r.get("stop_price"), r.get("take_profit_price"), px, bar_w+8), C["cyan"])
            y += 1
            add(stdscr, y, 2, f"Entry {fmt_price(r.get('entry_price'))} | Now {fmt_price(px)}")
            y += 1
            add(stdscr, y, 2, f"SL {fmt_price(r.get('stop_price'))} | TP {fmt_price(r.get('take_profit_price'))} | Size {fmt_num(r.get('size_usd'),2)}$")
            y += 1
            add(stdscr, y, 2, f"MFE {fmt_r(r.get('mfe_r'))} | MAE {fmt_r(r.get('mae_r'))} | {short(r.get('manager_state'), 18)}")
            y += 1
        else:
            add(stdscr, y, 2, "No open paper trade", C["yellow"])
            y += 1
        y += 1

        # MACRO
        title(stdscr, y, "MARKET RISK", C["yellow"])
        y += 1
        vix = d["macro"].get("VIX") or {}
        fng = d["macro"].get("FEAR_GREED") or {}
        dxy = d["macro"].get("DXY") or {}
        nq = d["macro"].get("NASDAQ") or {}

        vixv = fnum(vix.get("value"))
        vix_ratio = None if vixv is None else clamp((vixv - 10) / 30)
        add(stdscr, y, 2, f"VIX {fmt_num(vixv,2):>7}")
        add(stdscr, y, 14, bar01(vix_ratio, bar_w) if vix_ratio is not None else "NO DATA", C["red"] if vixv and vixv > 22 else C["green"])
        y += 1

        fngv = fnum(fng.get("value"))
        add(stdscr, y, 2, f"Fear {fmt_num(fngv,0):>6}")
        add(stdscr, y, 14, bar01((fngv or 0)/100, bar_w) if fngv is not None else "NO DATA", C["cyan"])
        y += 1

        add(stdscr, y, 2, f"DXY {fmt_num(dxy.get('value'),2)} {fmt_pct(dxy.get('change_pct'))} | Nasdaq {fmt_pct(nq.get('change_pct'))}")
        y += 2

        # DERIVATIVES
        title(stdscr, y, "BTC DERIVATIVES", C["magenta"])
        y += 1
        funding = d["deriv"].get("funding_rate") or {}
        oi = d["deriv"].get("open_interest") or {}
        ch24 = d["deriv"].get("price_change_pct_24h") or {}
        vol24 = d["deriv"].get("volume_24h") or {}

        fval = fnum(funding.get("value"))
        add(stdscr, y, 2, f"Funding {fmt_pct(fval):>10}")
        add(stdscr, y, 24, bar01(abs(fval or 0)/0.001, max(8, w-28)), C["yellow"])
        y += 1
        add(stdscr, y, 2, f"OI {fmt_num(oi.get('value'),0)} | 24h {fmt_pct(ch24.get('value'))}")
        y += 1
        add(stdscr, y, 2, f"Vol24 {fmt_num(vol24.get('value'),0)}")
        y += 2

        # QUANT MATURITY
        title(stdscr, y, "QUANT MATURITY / POSSIBLE TRADES", C["blue"])
        y += 1
        if not d["candidates"]:
            add(stdscr, y, 2, "No quant candidates found", C["yellow"])
            y += 1
        else:
            for r in d["candidates"][:3]:
                if y >= h - 5:
                    break

                score = r.get("_maturity", 0)
                sym = r.get("symbol") or "BTCUSDT"
                side = r.get("side") or "-"
                setup = r.get("setup") or "-"
                px = btc_px if sym == "BTCUSDT" else eth_px
                series = btc_series if sym == "BTCUSDT" else eth_series
                risk = estimate_risk_pct(series)
                sl, tp1, tp2 = compute_levels(px, side, risk)
                action = candidate_action(score, r)

                color = C["green"] if score >= 70 else C["yellow"] if score >= 55 else C["red"]

                add(stdscr, y, 2, short(f"{sym} {side} {setup}", w-4), curses.A_BOLD)
                y += 1
                add(stdscr, y, 2, f"Score {score:>5.1f}")
                add(stdscr, y, 15, bar01(score/100, max(10, w-32)), color)
                add(stdscr, y, max(30, w-18), short(action, 16), color | curses.A_BOLD)
                y += 1
                add(stdscr, y, 2, f"Entry {fmt_price(px)} | SL {fmt_price(sl)}")
                y += 1
                add(stdscr, y, 2, f"TP1 {fmt_price(tp1)} | TP2 {fmt_price(tp2)}")
                y += 1

        # PERFORMANCE / ADAPTER
        if y < h - 4:
            title(stdscr, y, "PNL / ADAPTER", C["green"])
            y += 1
            p = d["pnl"] or {}
            a = d["adapter"] or {}
            add(stdscr, y, 2, f"Closed {p.get('n',0)} | PnL {fmt_num(p.get('pnl_usd'),2)}$ | R {fmt_r(p.get('pnl_r'))} | WR {fmt_pct(p.get('winrate'))}")
            y += 1
            add(stdscr, y, 2, short(f"pending={a.get('pending_intents')} managed={a.get('managed_positions')} closed={a.get('closed_positions')} errors={a.get('errors')}", w-4))
            y += 1

        # FOOTER
        add(stdscr, h-2, 0, " " * (w-1), curses.A_REVERSE)
        add(stdscr, h-2, 1, short("q sortir | r refresh | vertical visual dashboard | no execution", w-3), curses.A_REVERSE)

        if d["errors"]:
            add(stdscr, h-3, 1, short("ERR " + str(d["errors"][-1]), w-3), C["red"])

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
