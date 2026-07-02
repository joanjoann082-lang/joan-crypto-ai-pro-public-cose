#!/usr/bin/env python3
from __future__ import annotations

import curses, json, math, sqlite3, subprocess, time
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

DB = Path("data/joanbot_v14.sqlite")
VERSION = "V18.4 MOSAIC QUANT DASHBOARD"
REFRESH_SEC = 60
BLOCKS = "▁▂▃▄▅▆▇█"

SERVICES = {
    "Brain": "run_v17_5_1_quant_brain_forever|quant_brain_v17_5_1",
    "Promo": "run_v17_6_1_promotion_controller_forever|promotion_controller_v17_6_1",
    "Adapter": "run_v17_8_1_paper_canary_adapter_forever|institutional_paper_canary_adapter_v17_8_1",
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

def safe_json(x, fallback):
    if x is None:
        return fallback
    if isinstance(x, (dict, list)):
        return x
    try:
        return json.loads(str(x))
    except Exception:
        return fallback

def fmt_price(x):
    v = fnum(x)
    if v is None:
        return "NO DATA"
    if v >= 1000:
        return f"{v:,.0f}"
    if v >= 10:
        return f"{v:,.2f}"
    return f"{v:.5f}"

def fmt_num(x, nd=2):
    v = fnum(x)
    if v is None:
        return "NO DATA"
    return f"{v:,.{nd}f}"

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

def short(s, n):
    s = "" if s is None else str(s)
    return s if len(s) <= n else s[:max(0, n-1)] + "…"

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
            SELECT value, ts
            FROM derivatives_context_v18_2
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

def price_series(con, symbol, limit=100):
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
            SELECT value AS p
            FROM derivatives_context_v18_2
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
    if v is None:
        return "NO DATA".ljust(width)
    v = clamp(fnum(v, 0.0) or 0.0)
    n = int(v * width)
    return "█" * n + "░" * (width - n)

def rbar(r, width):
    v = clamp(fnum(r, 0.0) or 0.0, -1.5, 1.5)
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

def sltp_line(entry, sl, tp, price, width):
    e, s, t, p = map(fnum, [entry, sl, tp, price])
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

def levels(price, side, risk_pct):
    p = fnum(price)
    if p is None:
        return None, None, None
    r = p * risk_pct
    if str(side).upper() == "SHORT":
        return p + r, p - 1.0*r, p - 1.8*r
    return p - r, p + 1.0*r, p + 1.8*r

def flatten_candidate(row):
    out = dict(row)
    payload = safe_json(out.get("payload"), {})
    if isinstance(payload, dict):
        metrics = payload.get("metrics") or {}
        if isinstance(metrics, dict):
            for k, v in metrics.items():
                out.setdefault(k, v)
        for k in ["symbol", "side", "setup", "quant_score", "brain_score"]:
            if k in payload and out.get(k) is None:
                out[k] = payload[k]
    return out

def maturity_score(row):
    r = flatten_candidate(row)

    clean = fnum(r.get("clean_n"), 0) or 0
    live = fnum(r.get("live_n"), 0) or 0
    shadow = fnum(r.get("shadow_n"), 0) or 0
    mean = fnum(r.get("robust_mean_r"), fnum(r.get("mean_r"), 0)) or 0
    lcb = fnum(r.get("institutional_lcb_r"), -0.6) or -0.6
    pf = fnum(r.get("profit_factor"), 0) or 0
    prob = fnum(r.get("prob_edge_gt_zero"), 0.5) or 0.5
    brain = fnum(r.get("brain_score"), fnum(r.get("quant_score"), 0)) or 0

    score = 0
    score += 16 * clamp(clean / 250)
    score += 12 * clamp(live / 20)
    score += 10 * clamp(shadow / 250)
    score += 18 * clamp((mean + 0.005) / 0.065)
    score += 18 * clamp((lcb + 0.55) / 0.55)
    score += 14 * clamp((pf - 0.70) / 1.0)
    score += 8 * clamp((prob - 0.50) / 0.35)
    score += 4 * clamp(brain / 70)

    return round(clamp(score, 0, 100), 1)

def action_label(score, row):
    r = flatten_candidate(row)
    lcb = fnum(r.get("institutional_lcb_r"), -1) or -1
    pf = fnum(r.get("profit_factor"), 0) or 0
    live = fnum(r.get("live_n"), 0) or 0

    if score >= 78 and lcb > -0.10 and pf >= 1.35 and live >= 10:
        return "TRADE POSSIBLE"
    if score >= 58:
        return "CANARY / REVIEW"
    return "NO TRADE"

def load_candidates(con):
    raw = []

    if exists(con, "institutional_quant_canary_governance_v17_7_2"):
        ts = one(con, "SELECT MAX(ts) AS ts FROM institutional_quant_canary_governance_v17_7_2")
        latest = ts.get("ts") if ts else None
        rows = many(con, """
            SELECT *
            FROM institutional_quant_canary_governance_v17_7_2
            WHERE ts=?
            ORDER BY quant_score DESC
            LIMIT 8
        """, (latest,))
        raw.extend(rows)

    if exists(con, "institutional_quant_brain_v17_5_1"):
        c = cols(con, "institutional_quant_brain_v17_5_1")
        ts = one(con, "SELECT MAX(ts) AS ts FROM institutional_quant_brain_v17_5_1")
        latest = ts.get("ts") if ts else None
        order_col = "brain_score" if "brain_score" in c else "id"
        rows = many(con, f"""
            SELECT *
            FROM institutional_quant_brain_v17_5_1
            WHERE ts=?
            ORDER BY {qid(order_col)} DESC
            LIMIT 12
        """, (latest,))
        raw.extend(rows)

    seen = set()
    out = []
    for r in raw:
        rr = flatten_candidate(r)
        sym = rr.get("symbol")
        side = rr.get("side")
        setup = rr.get("setup")
        if not sym or not side or not setup:
            continue
        k = (sym, side, setup)
        if k in seen:
            continue
        seen.add(k)
        rr["_maturity"] = maturity_score(rr)
        rr["_action"] = action_label(rr["_maturity"], rr)
        out.append(rr)

    out.sort(key=lambda x: x.get("_maturity", 0), reverse=True)
    return out[:6]

def candidate_for_symbol(cands, symbol):
    for c in cands:
        if str(c.get("symbol")).upper() == symbol.upper():
            return c
    return None

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

        d["btc_series"] = price_series(con, "BTCUSDT")
        d["eth_series"] = price_series(con, "ETHUSDT")

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
        stdscr.addnstr(y, x, str(text), max(0, w - x - 1), attr)
    except Exception:
        pass

def box(stdscr, y, x, h, w, title, color):
    if h < 3 or w < 8:
        return
    add(stdscr, y, x, "╭" + "─" * (w - 2) + "╮", color)
    add(stdscr, y, x + 2, f" {short(title, w-6)} ", color | curses.A_BOLD)
    for yy in range(y + 1, y + h - 1):
        add(stdscr, yy, x, "│", color)
        add(stdscr, yy, x + w - 1, "│", color)
        add(stdscr, yy, x + 1, " " * (w - 2))
    add(stdscr, y + h - 1, x, "╰" + "─" * (w - 2) + "╯", color)

def draw_asset_card(stdscr, y, x, h, w, name, symbol, price, series, cand, C):
    chg = None
    if series and price and series[0]:
        chg = price / series[0] - 1.0
    col = C["green"] if (chg or 0) >= 0 else C["red"]

    box(stdscr, y, x, h, w, name, C["cyan"] if symbol == "BTCUSDT" else C["magenta"])

    add(stdscr, y+1, x+2, fmt_price(price), col | curses.A_BOLD)
    add(stdscr, y+1, x+w-12, fmt_pct(chg), col | curses.A_BOLD)
    add(stdscr, y+2, x+2, spark(series, max(8, w-4)), col)

    if not cand:
        add(stdscr, y+4, x+2, "No setup")
        add(stdscr, y+5, x+2, "Score: N/A")
        return

    score = cand.get("_maturity", 0)
    action = cand.get("_action", "NO TRADE")
    side = cand.get("side", "-")
    setup = cand.get("setup", "-")
    score_col = C["green"] if score >= 70 else C["yellow"] if score >= 55 else C["red"]

    risk = estimate_risk_pct(series)
    sl, tp1, tp2 = levels(price, side, risk)

    add(stdscr, y+4, x+2, short(f"{side} {setup}", w-4), curses.A_BOLD)
    add(stdscr, y+5, x+2, f"Score {score:>5.1f}")
    add(stdscr, y+5, x+14, bar01(score/100, max(6, w-22)), score_col)
    add(stdscr, y+6, x+2, short(action, w-4), score_col | curses.A_BOLD)
    add(stdscr, y+7, x+2, f"SL {fmt_price(sl)}")
    add(stdscr, y+8, x+2, f"TP1 {fmt_price(tp1)}")
    add(stdscr, y+8, x+w//2, f"TP2 {fmt_price(tp2)}")

def draw_open_trade(stdscr, y, x, h, w, d, C):
    box(stdscr, y, x, h, w, "OPEN PAPER TRADE", C["green"])

    if not d["open"]:
        add(stdscr, y+2, x+2, "No open paper trade", C["yellow"])
        return

    r = d["open"][0]
    px = d["btc"]["price"] if r.get("symbol") == "BTCUSDT" else d["eth"]["price"]
    rr = fnum(r.get("net_pnl_r"), fnum(r.get("pnl_r"), 0.0)) or 0.0
    rc = C["green"] if rr >= 0 else C["red"]

    add(stdscr, y+1, x+2, short(f"#{r['id']} {r['symbol']} {r['side']} {r['setup']}", w-4), curses.A_BOLD)
    add(stdscr, y+2, x+2, f"R {fmt_r(rr)}", rc | curses.A_BOLD)
    add(stdscr, y+2, x+14, rbar(rr, max(12, w-18)), rc)
    add(stdscr, y+3, x+2, "SL→TP")
    add(stdscr, y+3, x+14, sltp_line(r.get("entry_price"), r.get("stop_price"), r.get("take_profit_price"), px, max(12, w-18)), C["cyan"])
    add(stdscr, y+4, x+2, f"Entry {fmt_price(r.get('entry_price'))} | Now {fmt_price(px)}")
    add(stdscr, y+5, x+2, f"SL {fmt_price(r.get('stop_price'))} | TP {fmt_price(r.get('take_profit_price'))}")
    add(stdscr, y+6, x+2, f"Size {fmt_num(r.get('size_usd'),2)}$ | MFE {fmt_r(r.get('mfe_r'))} | MAE {fmt_r(r.get('mae_r'))}")
    add(stdscr, y+7, x+2, short(str(r.get("manager_state")), w-4), C["cyan"])

def draw_macro_card(stdscr, y, x, h, w, d, C):
    box(stdscr, y, x, h, w, "MACRO RISK", C["yellow"])

    vix = d["macro"].get("VIX") or {}
    fng = d["macro"].get("FEAR_GREED") or {}
    dxy = d["macro"].get("DXY") or {}
    nq = d["macro"].get("NASDAQ") or {}

    vixv = fnum(vix.get("value"))
    fngv = fnum(fng.get("value"))
    vix_ratio = None if vixv is None else clamp((vixv - 10) / 30)

    add(stdscr, y+1, x+2, f"VIX  {fmt_num(vixv,2):>8}")
    add(stdscr, y+2, x+2, bar01(vix_ratio, max(8, w-4)), C["red"] if vixv and vixv > 22 else C["green"])

    add(stdscr, y+3, x+2, f"Fear {fmt_num(fngv,0):>8}")
    add(stdscr, y+4, x+2, bar01((fngv or 0)/100 if fngv is not None else None, max(8, w-4)), C["cyan"])

    add(stdscr, y+5, x+2, short(f"DXY {fmt_num(dxy.get('value'),2)} {fmt_pct(dxy.get('change_pct'))}", w-4))
    add(stdscr, y+6, x+2, short(f"Nasdaq {fmt_pct(nq.get('change_pct'))}", w-4))

def draw_deriv_card(stdscr, y, x, h, w, d, C):
    box(stdscr, y, x, h, w, "BTC DERIVATIVES", C["magenta"])

    funding = d["deriv"].get("funding_rate") or {}
    oi = d["deriv"].get("open_interest") or {}
    ch24 = d["deriv"].get("price_change_pct_24h") or {}
    vol24 = d["deriv"].get("volume_24h") or {}

    fval = fnum(funding.get("value"))
    add(stdscr, y+1, x+2, f"Funding {fmt_pct(fval)}")
    add(stdscr, y+2, x+2, bar01(abs(fval or 0) / 0.001 if fval is not None else None, max(8, w-4)), C["yellow"])
    add(stdscr, y+3, x+2, short(f"OI {fmt_num(oi.get('value'),0)}", w-4))
    add(stdscr, y+4, x+2, short(f"24h {fmt_pct(ch24.get('value'))}", w-4))
    add(stdscr, y+5, x+2, short(f"Vol {fmt_num(vol24.get('value'),0)}", w-4))

def draw_quant_card(stdscr, y, x, h, w, d, C):
    box(stdscr, y, x, h, w, "QUANT MATURITY / SETUPS", C["blue"])

    cands = d["candidates"]
    if not cands:
        add(stdscr, y+2, x+2, "No quant candidates", C["yellow"])
        return

    row_y = y + 1
    usable = h - 2
    for c in cands[:max(1, usable // 3)]:
        if row_y >= y + h - 1:
            break

        sym = c.get("symbol", "-")
        side = c.get("side", "-")
        setup = c.get("setup", "-")
        score = c.get("_maturity", 0)
        action = c.get("_action", "-")
        price = d["btc"]["price"] if sym == "BTCUSDT" else d["eth"]["price"]
        series = d["btc_series"] if sym == "BTCUSDT" else d["eth_series"]
        risk = estimate_risk_pct(series)
        sl, tp1, tp2 = levels(price, side, risk)

        col = C["green"] if score >= 70 else C["yellow"] if score >= 55 else C["red"]

        add(stdscr, row_y, x+2, short(f"{sym} {side} {setup}", w-4), curses.A_BOLD)
        row_y += 1
        add(stdscr, row_y, x+2, f"{score:>5.1f}")
        add(stdscr, row_y, x+9, bar01(score/100, max(8, w-30)), col)
        add(stdscr, row_y, x+w-18, short(action, 16), col | curses.A_BOLD)
        row_y += 1
        add(stdscr, row_y, x+2, short(f"SL {fmt_price(sl)} | TP1 {fmt_price(tp1)} | TP2 {fmt_price(tp2)}", w-4))
        row_y += 1

def draw_bottom(stdscr, y, x, h, w, d, C):
    box(stdscr, y, x, h, w, "SYSTEM / PNL", C["white"])

    db_col = C["green"] if d["db"] == "ok" else C["red"]
    add(stdscr, y+1, x+2, f"DB {d['db']}", db_col | curses.A_BOLD)

    sx = x + 12
    for name, cnt in d["services"].items():
        ok = cnt >= 1
        add(stdscr, y+1, sx, f"{name}:{'OK' if ok else 'OFF'}", C["green"] if ok else C["red"])
        sx += len(name) + 8
        if sx > x + w - 12:
            break

    p = d["pnl"] or {}
    a = d["adapter"] or {}
    add(stdscr, y+2, x+2, short(f"Closed {p.get('n',0)} | PnL {fmt_num(p.get('pnl_usd'),2)}$ | R {fmt_r(p.get('pnl_r'))} | WR {fmt_pct(p.get('winrate'))}", w-4))
    add(stdscr, y+3, x+2, short(f"Adapter pending={a.get('pending_intents')} managed={a.get('managed_positions')} closed={a.get('closed_positions')} errors={a.get('errors')}", w-4))

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

        add(stdscr, 0, 0, " " * max(1, w-1), curses.A_REVERSE)
        add(stdscr, 0, 1, short(f"{VERSION} | {utc_now()} | q sortir | r refresh", w-3), curses.A_REVERSE | curses.A_BOLD)

        margin = 1
        gap = 1
        usable_w = max(20, w - 2)
        left_w = (usable_w - gap) // 2
        right_w = usable_w - gap - left_w
        left_x = margin
        right_x = margin + left_w + gap

        cands = d["candidates"]
        btc_cand = candidate_for_symbol(cands, "BTCUSDT")
        eth_cand = candidate_for_symbol(cands, "ETHUSDT")

        # Dynamic heights to fill portrait screen
        top_h = 10
        trade_h = 9
        mid_h = 8
        bottom_h = 5
        header_h = 2
        used = header_h + top_h + trade_h + mid_h + bottom_h + 5
        quant_h = max(7, h - used)

        y = 2

        if w >= 62:
            draw_asset_card(stdscr, y, left_x, top_h, left_w, "BTC", "BTCUSDT", d["btc"].get("price"), d["btc_series"], btc_cand, C)
            draw_asset_card(stdscr, y, right_x, top_h, right_w, "ETH", "ETHUSDT", d["eth"].get("price"), d["eth_series"], eth_cand, C)
            y += top_h + 1
        else:
            draw_asset_card(stdscr, y, margin, top_h, usable_w, "BTC", "BTCUSDT", d["btc"].get("price"), d["btc_series"], btc_cand, C)
            y += top_h + 1
            draw_asset_card(stdscr, y, margin, top_h, usable_w, "ETH", "ETHUSDT", d["eth"].get("price"), d["eth_series"], eth_cand, C)
            y += top_h + 1

        draw_open_trade(stdscr, y, margin, trade_h, usable_w, d, C)
        y += trade_h + 1

        if w >= 62:
            draw_macro_card(stdscr, y, left_x, mid_h, left_w, d, C)
            draw_deriv_card(stdscr, y, right_x, mid_h, right_w, d, C)
            y += mid_h + 1
        else:
            draw_macro_card(stdscr, y, margin, mid_h, usable_w, d, C)
            y += mid_h + 1

        remaining_for_bottom = bottom_h + 2
        qh = max(5, min(quant_h, h - y - remaining_for_bottom))
        draw_quant_card(stdscr, y, margin, qh, usable_w, d, C)
        y += qh + 1

        if y + bottom_h < h - 1:
            draw_bottom(stdscr, y, margin, bottom_h, usable_w, d, C)

        add(stdscr, h-1, 0, " " * max(1, w-1), curses.A_REVERSE)
        add(stdscr, h-1, 1, short("q sortir | r refresh | mosaic full-screen | no execution", w-3), curses.A_REVERSE)

        if d["errors"] and h > 4:
            add(stdscr, h-2, 1, short("ERR " + str(d["errors"][-1]), w-3), C["red"])

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
