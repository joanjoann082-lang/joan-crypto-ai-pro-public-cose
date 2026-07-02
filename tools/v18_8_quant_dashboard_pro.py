#!/usr/bin/env python3
from __future__ import annotations

import curses
import json
import math
import os
import sqlite3
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

DB = Path("data/joanbot_v14.sqlite")
VERSION = "V18.8.1 QUANT COMMAND DASHBOARD PRO"

SYMBOLS = ["BTCUSDT", "ETHUSDT"]

V20 = "institutional_quant_research_brain_v20_0"
V20_LEDGER = "institutional_alpha_ledger_v20_0"
V20_HEALTH = "institutional_quant_brain_health_v20_0"

V19_CONTRACT = "institutional_contract_governor_health_v19_2"
V19_INTENTS = "institutional_quant_canary_execution_intents_v17_7_2"
V19_AUDIT = "institutional_contract_audit_v19_2"

PAYOFF = "institutional_payoff_snapshot_v18_6"
POSITIONS = "paper_micro_canary_positions_v11"
MARKET = "market_snapshots"

SERVICE_PATTERNS = {
    "Brain": "run_v17_5_1_quant_brain|v20_0_institutional_quant_research_brain|quant_brain",
    "Promo": "promotion_controller",
    "Contract": "v19_2_contract|contract_governor",
    "Adapter": "paper_canary_adapter|v17_8_1",
    "Macro": "market_context|macro",
    "Payoff": "payoff",
}


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S UTC")


def parse_ts(x: Any) -> Optional[datetime]:
    if not x:
        return None
    s = str(x).replace("Z", "+00:00")
    try:
        d = datetime.fromisoformat(s)
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.astimezone(timezone.utc)
    except Exception:
        return None


def age_min(x: Any) -> Optional[float]:
    d = parse_ts(x)
    if not d:
        return None
    return max(0.0, (datetime.now(timezone.utc) - d).total_seconds() / 60.0)


def fnum(x: Any, default=None):
    try:
        if x is None or x == "":
            return default
        v = float(x)
        if math.isnan(v) or math.isinf(v):
            return default
        return v
    except Exception:
        return default


def fmt(x: Any, n: int = 2, default: str = "N/A") -> str:
    v = fnum(x)
    if v is None:
        return default
    return f"{v:,.{n}f}"


def fmt_signed(x: Any, n: int = 2) -> str:
    v = fnum(x)
    if v is None:
        return "N/A"
    return f"{v:+.{n}f}"


def clamp(x: float, lo=0.0, hi=1.0) -> float:
    return max(lo, min(hi, x))


def qid(x: str) -> str:
    return '"' + str(x).replace('"', '""') + '"'


def db_connect():
    con = sqlite3.connect(DB, timeout=3)
    con.row_factory = sqlite3.Row
    return con


def exists(con, table: str) -> bool:
    try:
        return con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone() is not None
    except Exception:
        return False


def list_tables(con) -> List[str]:
    try:
        return [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    except Exception:
        return []


def cols(con, table: str) -> List[str]:
    if not exists(con, table):
        return []
    try:
        return [r[1] for r in con.execute(f"PRAGMA table_info({qid(table)})")]
    except Exception:
        return []


def rows(con, sql: str, args=()) -> List[Dict[str, Any]]:
    try:
        return [dict(r) for r in con.execute(sql, args).fetchall()]
    except Exception:
        return []


def one(con, sql: str, args=()) -> Optional[Dict[str, Any]]:
    try:
        r = con.execute(sql, args).fetchone()
        return dict(r) if r else None
    except Exception:
        return None


def parse_payload(x: Any) -> Dict[str, Any]:
    if isinstance(x, dict):
        return x
    if not x:
        return {}
    try:
        v = json.loads(str(x))
        return v if isinstance(v, dict) else {}
    except Exception:
        return {}


def reason_list(x: Any) -> List[str]:
    if x is None:
        return []
    if isinstance(x, list):
        return [str(v) for v in x]
    if isinstance(x, dict):
        return [str(k) for k, v in x.items() if v]
    s = str(x)
    try:
        return reason_list(json.loads(s))
    except Exception:
        pass
    out = []
    for p in s.replace("[", "").replace("]", "").replace('"', "").replace("'", "").split(","):
        p = p.strip()
        if p:
            out.append(p)
    return out


def latest_ts(con, table: str) -> Optional[str]:
    c = cols(con, table)
    for tc in ["ts", "created_at", "updated_at", "opened_at", "closed_at"]:
        if tc in c:
            r = one(con, f"SELECT MAX({qid(tc)}) AS mx FROM {qid(table)}")
            if r and r.get("mx"):
                return r["mx"]
    return None


def latest_rows(con, table: str, limit: int = 40) -> List[Dict[str, Any]]:
    if not exists(con, table):
        return []
    c = cols(con, table)
    order = "id" if "id" in c else "ts" if "ts" in c else "rowid"
    return rows(con, f"SELECT * FROM {qid(table)} ORDER BY {qid(order) if order != 'rowid' else 'rowid'} DESC LIMIT ?", (limit,))


def pick(row: Dict[str, Any], payload: Dict[str, Any], *names):
    metrics = payload.get("metrics") if isinstance(payload.get("metrics"), dict) else {}
    decision = payload.get("decision") if isinstance(payload.get("decision"), dict) else {}
    candidate = payload.get("candidate") if isinstance(payload.get("candidate"), dict) else {}

    for n in names:
        for src in [row, metrics, decision, candidate, payload]:
            if isinstance(src, dict) and src.get(n) is not None:
                return src.get(n)
    return None


def normalize_candidate(r: Dict[str, Any], source: str) -> Optional[Dict[str, Any]]:
    payload = parse_payload(r.get("payload"))

    symbol = pick(r, payload, "symbol", "edge_symbol", "selected_symbol")
    side = pick(r, payload, "side", "edge_side", "selected_side")
    setup = pick(r, payload, "setup", "edge_setup", "selected_setup", "family_name")

    if not symbol or not side:
        return None

    setup = setup or "-"
    side = str(side).upper()
    symbol = str(symbol).upper()

    score = fnum(
        pick(r, payload, "quant_score", "score", "brain_score", "allocation_score", "institutional_priority", "priority"),
        0.0,
    ) or 0.0

    prob = fnum(pick(r, payload, "prob_edge", "prob_edge_pos", "prob"), None)
    qv = fnum(pick(r, payload, "q_value", "q"), None)
    lcb = fnum(pick(r, payload, "lcb95_r", "institutional_lcb_r", "lcb_r"), None)
    cvar = fnum(pick(r, payload, "cvar10_r", "cvar"), None)
    pf = fnum(pick(r, payload, "pf_cons", "profit_factor", "pf"), None)
    n_eff = fnum(pick(r, payload, "n_eff", "n"), 0.0) or 0.0
    live_eff = fnum(pick(r, payload, "live_eff", "live"), 0.0) or 0.0

    state = str(pick(r, payload, "state", "authority_state", "action") or "UNKNOWN")
    action = str(pick(r, payload, "action") or "")
    regime = str(pick(r, payload, "regime") or "")

    reasons = []
    for k in ["reasons", "reason", "hard_vetoes", "red_flags", "payload"]:
        if r.get(k) is not None:
            reasons += reason_list(r.get(k))

    sl = pick(r, payload, "sl", "stop_loss", "stop_price", "selected_sl")
    tp1 = pick(r, payload, "tp1", "take_profit_1", "selected_tp1")
    tp2 = pick(r, payload, "tp2", "take_profit_2", "selected_tp2", "take_profit_price")

    return {
        "source": source,
        "symbol": symbol,
        "side": side,
        "setup": str(setup),
        "score": score,
        "prob": prob,
        "q": qv,
        "lcb": lcb,
        "cvar": cvar,
        "pf": pf,
        "n_eff": n_eff,
        "live_eff": live_eff,
        "state": state,
        "action": action,
        "regime": regime,
        "reasons": sorted(set(reasons)),
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,
    }


def load_brain_candidates(con) -> List[Dict[str, Any]]:
    out = []

    if exists(con, V20):
        ts = latest_ts(con, V20)
        if ts:
            data = rows(con, f"SELECT * FROM {qid(V20)} WHERE ts=? ORDER BY quant_score DESC LIMIT 80", (ts,))
        else:
            data = latest_rows(con, V20, 80)
        for r in data:
            c = normalize_candidate(r, "V20")
            if c:
                out.append(c)

    fallbacks = [
        "institutional_quant_brain_v17_5_1",
        "institutional_research_governor_v19_1",
        "institutional_promotion_controller_v17_6_1",
    ]

    for t in fallbacks:
        if not exists(con, t):
            continue
        for r in latest_rows(con, t, 80):
            c = normalize_candidate(r, t)
            if c:
                out.append(c)

    best = {}
    for c in out:
        key = (c["symbol"], c["side"], c["setup"])
        old = best.get(key)
        if old is None or c["score"] > old["score"]:
            best[key] = c

    return sorted(best.values(), key=lambda x: x["score"], reverse=True)


def market_series(con, symbol: str, limit: int = 80) -> Dict[str, Any]:
    if not exists(con, MARKET):
        return {"price": None, "series": [], "age": None, "ret": None}

    c = cols(con, MARKET)
    price_col = next((x for x in ["price", "close", "last", "mark_price", "mid_price"] if x in c), None)
    ts_col = next((x for x in ["ts", "created_at", "time"] if x in c), None)
    sym_col = "symbol" if "symbol" in c else None

    if not price_col:
        return {"price": None, "series": [], "age": None, "ret": None}

    order = ts_col or "rowid"

    if sym_col:
        prefix = "BTC" if symbol.startswith("BTC") else "ETH"
        data = rows(
            con,
            f"""
            SELECT {qid(price_col)} AS p, {qid(ts_col) if ts_col else "NULL"} AS ts
            FROM {qid(MARKET)}
            WHERE UPPER(COALESCE({qid(sym_col)},''))=? OR UPPER(COALESCE({qid(sym_col)},'')) LIKE ?
            ORDER BY {qid(order) if order != "rowid" else "rowid"} DESC
            LIMIT ?
            """,
            (symbol, prefix + "%", limit),
        )
    else:
        data = rows(
            con,
            f"""
            SELECT {qid(price_col)} AS p, {qid(ts_col) if ts_col else "NULL"} AS ts
            FROM {qid(MARKET)}
            ORDER BY {qid(order) if order != "rowid" else "rowid"} DESC
            LIMIT ?
            """,
            (limit,),
        )

    vals = [fnum(r.get("p")) for r in data]
    vals = [v for v in vals if v and v > 0]

    price = vals[0] if vals else None
    ret = None
    if len(vals) >= 2 and vals[-1]:
        ret = price / vals[-1] - 1

    ts = data[0].get("ts") if data else None

    return {
        "price": price,
        "series": list(reversed(vals)),
        "age": age_min(ts),
        "ret": ret,
    }


def spark(vals: List[float], width: int) -> str:
    if not vals or width <= 0:
        return ""
    xs = vals[-width:]
    lo, hi = min(xs), max(xs)
    chars = "▁▂▃▄▅▆▇█"
    if hi <= lo:
        return chars[3] * len(xs)
    out = ""
    for v in xs:
        idx = int((v - lo) / (hi - lo) * (len(chars) - 1))
        out += chars[max(0, min(len(chars) - 1, idx))]
    return out


def best_by_side(cands: List[Dict[str, Any]], symbol: str, side: str) -> Optional[Dict[str, Any]]:
    xs = [c for c in cands if c["symbol"].startswith(symbol[:3]) and c["side"] == side]
    return xs[0] if xs else None


def load_positions(con) -> Dict[str, Any]:
    out = {"open": [], "last": []}

    if not exists(con, POSITIONS):
        return out

    c = cols(con, POSITIONS)
    order = "id" if "id" in c else "rowid"

    out["open"] = rows(
        con,
        f"""
        SELECT *
        FROM {qid(POSITIONS)}
        WHERE UPPER(COALESCE(status,'')) IN ('OPEN','ACTIVE','RUNNING','OPEN_MANAGED')
           OR (opened_at IS NOT NULL AND (closed_at IS NULL OR closed_at=''))
        ORDER BY {qid(order) if order != "rowid" else "rowid"} DESC
        LIMIT 5
        """,
    )

    if "closed_at" in c:
        out["last"] = rows(
            con,
            f"""
            SELECT *
            FROM {qid(POSITIONS)}
            WHERE closed_at IS NOT NULL AND closed_at <> ''
            ORDER BY closed_at DESC
            LIMIT 10
            """,
        )
    else:
        out["last"] = latest_rows(con, POSITIONS, 10)

    return out


def load_intents(con) -> Dict[str, Any]:
    out = {"pending": 0, "latest": [], "rejects": 0}

    if not exists(con, V19_INTENTS):
        return out

    c = cols(con, V19_INTENTS)
    order = "id" if "id" in c else "rowid"

    out["latest"] = rows(
        con,
        f"SELECT * FROM {qid(V19_INTENTS)} ORDER BY {qid(order) if order != 'rowid' else 'rowid'} DESC LIMIT 8",
    )

    if "adapter_status" in c or "intent_state" in c:
        status_expr = []
        if "adapter_status" in c:
            status_expr.append("UPPER(COALESCE(adapter_status,'')) LIKE '%PENDING%'")
        if "intent_state" in c:
            status_expr.append("UPPER(COALESCE(intent_state,'')) LIKE '%PENDING%'")
        r = one(con, f"SELECT COUNT(*) AS n FROM {qid(V19_INTENTS)} WHERE " + " OR ".join(status_expr))
        out["pending"] = int((r or {}).get("n") or 0)

        reject_expr = []
        if "adapter_status" in c:
            reject_expr.append("UPPER(COALESCE(adapter_status,'')) LIKE '%REJECT%'")
        if "intent_state" in c:
            reject_expr.append("UPPER(COALESCE(intent_state,'')) LIKE '%REJECT%'")
        if reject_expr:
            r = one(con, f"SELECT COUNT(*) AS n FROM {qid(V19_INTENTS)} WHERE " + " OR ".join(reject_expr))
            out["rejects"] = int((r or {}).get("n") or 0)

    return out


def load_payoff(con) -> Dict[str, Any]:
    if not exists(con, PAYOFF):
        return {}
    return latest_rows(con, PAYOFF, 1)[0] if latest_rows(con, PAYOFF, 1) else {}


def find_metric(con, aliases: List[str]) -> Dict[str, Any]:
    tables = list_tables(con)

    for t in tables:
        c = cols(con, t)
        low = {x.lower(): x for x in c}
        hit = None

        for a in aliases:
            for lc, real in low.items():
                if a.lower() in lc:
                    hit = real
                    break
            if hit:
                break

        if not hit:
            continue

        ts_col = next((x for x in ["ts", "created_at", "updated_at", "time"] if x in c), None)
        order = ts_col or ("id" if "id" in c else "rowid")

        r = one(
            con,
            f"""
            SELECT {qid(hit)} AS v, {qid(ts_col) if ts_col else "NULL"} AS ts
            FROM {qid(t)}
            ORDER BY {qid(order) if order != "rowid" else "rowid"} DESC
            LIMIT 1
            """,
        )

        if r and r.get("v") not in (None, ""):
            return {"value": r.get("v"), "table": t, "col": hit, "age": age_min(r.get("ts"))}

    return {"value": None, "table": None, "col": None, "age": None}


def process_alive(pattern: str) -> bool:
    try:
        txt = subprocess.check_output(["ps", "-ef"], text=True, stderr=subprocess.DEVNULL)
        lines = [ln for ln in txt.splitlines() if "grep" not in ln]
        import re
        return any(re.search(pattern, ln, re.I) for ln in lines)
    except Exception:
        return False


def collect_state() -> Dict[str, Any]:
    state = {
        "db_ok": False,
        "error": None,
        "symbols": {},
        "candidates": [],
        "positions": {"open": [], "last": []},
        "intents": {"pending": 0, "latest": [], "rejects": 0},
        "payoff": {},
        "macro": {},
        "deriv": {},
        "services": {},
        "v20_health": {},
        "contract_health": {},
    }

    if not DB.exists():
        state["error"] = "DB missing"
        return state

    try:
        con = db_connect()
        qc = con.execute("PRAGMA quick_check").fetchone()[0]
        state["db_ok"] = str(qc).lower() == "ok"

        cands = load_brain_candidates(con)
        state["candidates"] = cands

        for sym in SYMBOLS:
            ms = market_series(con, sym)
            long = best_by_side(cands, sym, "LONG")
            short = best_by_side(cands, sym, "SHORT")
            best = None
            if long and short:
                best = long if long["score"] >= short["score"] else short
            else:
                best = long or short

            state["symbols"][sym] = {
                "market": ms,
                "long": long,
                "short": short,
                "best": best,
            }

        state["positions"] = load_positions(con)
        state["intents"] = load_intents(con)
        state["payoff"] = load_payoff(con)

        state["macro"] = {
            "VIX": find_metric(con, ["vix"]),
            "Fear": find_metric(con, ["fear"]),
            "DXY": find_metric(con, ["dxy", "dollar"]),
            "Nasdaq": find_metric(con, ["nasdaq", "ndx"]),
            "US10Y": find_metric(con, ["us10y", "10y", "yield"]),
        }

        state["deriv"] = {
            "Funding": find_metric(con, ["funding"]),
            "OI": find_metric(con, ["open_interest", "oi"]),
            "Long/Short": find_metric(con, ["long_short", "longshort"]),
            "CVD": find_metric(con, ["cvd", "delta"]),
            "Liq": find_metric(con, ["liquidation", "liq"]),
        }

        if exists(con, V20_HEALTH):
            state["v20_health"] = latest_rows(con, V20_HEALTH, 1)[0] if latest_rows(con, V20_HEALTH, 1) else {}

        if exists(con, V19_CONTRACT):
            state["contract_health"] = latest_rows(con, V19_CONTRACT, 1)[0] if latest_rows(con, V19_CONTRACT, 1) else {}

        con.close()

    except Exception as e:
        state["error"] = repr(e)

    for name, pat in SERVICE_PATTERNS.items():
        state["services"][name] = process_alive(pat)

    return state


class UI:
    def __init__(self, stdscr):
        self.s = stdscr
        self.has_color = curses.has_colors()
        if self.has_color:
            curses.start_color()
            curses.use_default_colors()
            self._pair(1, curses.COLOR_GREEN)
            self._pair(2, curses.COLOR_RED)
            self._pair(3, curses.COLOR_YELLOW)
            self._pair(4, curses.COLOR_CYAN)
            self._pair(5, curses.COLOR_MAGENTA)
            self._pair(6, curses.COLOR_WHITE)
            self._pair(7, curses.COLOR_BLUE)
        self.H, self.W = self.s.getmaxyx()

    def _pair(self, n, fg):
        try:
            curses.init_pair(n, fg, -1)
        except Exception:
            pass

    def c(self, n, bold=False):
        attr = curses.color_pair(n) if self.has_color else 0
        if bold:
            attr |= curses.A_BOLD
        return attr

    def add(self, y, x, text, color=6, bold=False):
        if y < 0 or y >= self.H or x < 0 or x >= self.W:
            return
        try:
            self.s.addnstr(y, x, str(text), max(0, self.W - x - 1), self.c(color, bold))
        except Exception:
            pass

    def box(self, y, x, h, w, title="", color=6):
        if h < 2 or w < 4:
            return
        y2 = min(self.H - 1, y + h - 1)
        x2 = min(self.W - 1, x + w - 1)
        h = y2 - y + 1
        w = x2 - x + 1

        try:
            self.s.addch(y, x, curses.ACS_ULCORNER, self.c(color))
            self.s.addch(y, x2, curses.ACS_URCORNER, self.c(color))
            self.s.addch(y2, x, curses.ACS_LLCORNER, self.c(color))
            self.s.addch(y2, x2, curses.ACS_LRCORNER, self.c(color))
            for xx in range(x + 1, x2):
                self.s.addch(y, xx, curses.ACS_HLINE, self.c(color))
                self.s.addch(y2, xx, curses.ACS_HLINE, self.c(color))
            for yy in range(y + 1, y2):
                self.s.addch(yy, x, curses.ACS_VLINE, self.c(color))
                self.s.addch(yy, x2, curses.ACS_VLINE, self.c(color))
        except Exception:
            pass

        if title:
            self.add(y, x + 2, f" {title} ", color, True)

    def bar(self, y, x, w, pct, color=3, label=""):
        if w <= 0:
            return
        pct = clamp(fnum(pct, 0.0) or 0.0)
        fill = int(w * pct)
        self.add(y, x, "█" * fill, color)
        self.add(y, x + fill, "░" * max(0, w - fill), color)
        if label:
            self.add(y, x, label[:w], 6, True)


def draw_symbol(ui: UI, y, x, h, w, title, item):
    ui.box(y, x, h, w, title, 4 if title.startswith("BTC") else 5)

    m = item.get("market", {})
    price = m.get("price")
    ret = m.get("ret")
    age = m.get("age")
    series = m.get("series") or []

    color = 1 if (ret or 0) >= 0 else 2
    ui.add(y + 1, x + 2, f"Price {fmt(price, 0)}  {fmt_signed((ret or 0) * 100, 2)}%", color, True)
    ui.add(y + 2, x + 2, spark(series, max(10, w - 6)), color)

    long = item.get("long")
    short = item.get("short")
    best = item.get("best")

    ls = long["score"] if long else 0
    ss = short["score"] if short else 0

    ui.add(y + 3, x + 2, "LONG ", 1, True)
    ui.bar(y + 3, x + 8, max(8, (w - 16) // 2), ls / 100.0, 1)
    ui.add(y + 3, x + w // 2 + 1, f"{ls:5.1f}", 1)

    ui.add(y + 4, x + 2, "SHORT", 2, True)
    ui.bar(y + 4, x + 8, max(8, (w - 16) // 2), ss / 100.0, 2)
    ui.add(y + 4, x + w // 2 + 1, f"{ss:5.1f}", 2)

    if best:
        bcol = 2 if best["side"] == "SHORT" else 1
        ui.add(y + 5, x + 2, f"BEST {best['side']} {best['setup'][:28]}", bcol, True)
        ui.add(y + 6, x + 2, f"State {best['state'][:18]} | prob {fmt(best['prob'],2)} | q {fmt(best['q'],2)}", 3)
        ui.add(y + 7, x + 2, f"LCB {fmt(best['lcb'],3)} | CVaR {fmt(best['cvar'],2)} | PF {fmt(best['pf'],2)}", 6)
        ui.add(y + 8, x + 2, f"SL {fmt(best['sl'],0)} | TP1 {fmt(best['tp1'],0)} | TP2 {fmt(best['tp2'],0)}", 6)
    else:
        ui.add(y + 5, x + 2, "No candidate", 3)

    ui.add(y + h - 2, x + 2, f"data age {fmt(age,1)}m", 6)


def draw_open_pending(ui, y, x, h, w, st):
    ui.box(y, x, h, w, "OPEN / PENDING / ADAPTER", 1)

    openp = st["positions"].get("open", [])
    intents = st["intents"]

    if openp:
        p = openp[0]
        sym = p.get("symbol", "-")
        side = p.get("side", "-")
        setup = str(p.get("setup", "-"))[:28]
        r = fnum(p.get("net_pnl_r"), fnum(p.get("pnl_r"), None))
        usd = fnum(p.get("net_pnl_usd"), fnum(p.get("pnl_usd"), None))
        ui.add(y + 1, x + 2, f"OPEN {sym} {side} {setup}", 1, True)
        ui.add(y + 2, x + 2, f"R {fmt_signed(r,2)} | USD {fmt_signed(usd,2)} | state {p.get('manager_state', p.get('status','-'))}", 6)
        ui.add(y + 3, x + 2, f"Entry {fmt(p.get('entry_price'),2)} | SL {fmt(p.get('stop_price'),2)} | TP {fmt(p.get('take_profit_price'),2)}", 6)
    else:
        ui.add(y + 1, x + 2, "No open paper trade", 3)

    ui.add(y + 4, x + 2, f"Pending intents: {intents.get('pending',0)} | rejects total: {intents.get('rejects',0)}", 3 if intents.get("pending") else 1, True)

    latest = intents.get("latest", [])
    if latest:
        r = latest[0]
        ui.add(
            y + 5,
            x + 2,
            f"Last intent #{r.get('id','?')} {r.get('symbol','-')} {r.get('side','-')} {str(r.get('setup','-'))[:24]}",
            6,
        )
        ui.add(
            y + 6,
            x + 2,
            f"mode {r.get('requested_mode','-')} | perm {r.get('execution_permission','-')} | adapter {r.get('adapter_status','-')}",
            6,
        )


def draw_market(ui, y, x, h, w, st):
    half = (w - 1) // 2
    ui.box(y, x, h, half, "MACRO", 3)
    ui.box(y, x + half + 1, h, w - half - 1, "DERIVATIVES", 5)

    macro = st["macro"]
    yy = y + 1
    for name in ["VIX", "Fear", "DXY", "Nasdaq", "US10Y"]:
        m = macro.get(name, {})
        val = m.get("value")
        age = m.get("age")
        ui.add(yy, x + 2, f"{name:<7} {fmt(val,2)}  age {fmt(age,0)}m", 6)
        pct = clamp((fnum(val, 0) or 0) / (100 if name == "Fear" else 30))
        ui.bar(yy, x + 24, max(6, half - 27), pct, 1 if name in ["Nasdaq"] else 3)
        yy += 1

    deriv = st["deriv"]
    yy = y + 1
    for name in ["Funding", "OI", "Long/Short", "CVD", "Liq"]:
        d = deriv.get(name, {})
        ui.add(yy, x + half + 3, f"{name:<10} {fmt(d.get('value'),4)}  age {fmt(d.get('age'),0)}m", 6)
        yy += 1


def draw_quant_and_why(ui, y, x, h, w, st):
    half = (w - 1) // 2
    ui.box(y, x, h, half, "V20 QUANT BRAIN / SIDE BIAS", 7)
    ui.box(y, x + half + 1, h, w - half - 1, "WHY BLOCKED / WHY SHORT", 3)

    cands = st["candidates"]
    v20 = st.get("v20_health", {})
    payoff = st.get("payoff", {})

    ui.add(y + 1, x + 2, f"V20 summary: {v20.get('summary','N/A')}", 6, True)
    ui.add(y + 2, x + 2, f"Validated {v20.get('validated','N/A')} | Promising {v20.get('promising','N/A')} | Blocked {v20.get('blocked','N/A')}", 6)

    yy = y + 4
    for sym in SYMBOLS:
        item = st["symbols"].get(sym, {})
        long = item.get("long")
        short = item.get("short")
        ls = long["score"] if long else 0
        ss = short["score"] if short else 0
        bias = "SHORT" if ss > ls else "LONG" if ls > ss else "NEUTRAL"
        col = 2 if bias == "SHORT" else 1 if bias == "LONG" else 3
        ui.add(yy, x + 2, f"{sym:<7} bias {bias:<7} L {ls:5.1f} / S {ss:5.1f}", col, True)
        ui.bar(yy + 1, x + 2, max(8, half - 6), max(ls, ss) / 100.0, col)
        yy += 3

    ui.add(yy, x + 2, "Top alpha lanes:", 6, True)
    yy += 1
    for c in cands[: max(1, h - 12)]:
        col = 2 if c["side"] == "SHORT" else 1
        ui.add(
            yy,
            x + 2,
            f"{c['score']:5.1f} {c['symbol'][:3]} {c['side']:<5} {c['setup'][:22]} p={fmt(c['prob'],2)} q={fmt(c['q'],2)}",
            col,
        )
        yy += 1
        if yy >= y + h - 1:
            break

    # WHY PANEL
    xx = x + half + 3
    yy = y + 1

    gate = "BLOCK"
    if v20.get("validated", 0):
        gate = "VALIDATED"
    elif v20.get("promising", 0):
        gate = "PROMISING"

    ui.add(yy, xx, f"Gate: {gate}", 2 if gate == "BLOCK" else 3 if gate == "PROMISING" else 1, True)
    yy += 1

    pay_health = payoff.get("payoff_health", payoff.get("health", "N/A"))
    ui.add(yy, xx, f"Payoff: {pay_health} | closed_n {payoff.get('closed_n','N/A')}", 6)
    yy += 1

    if st["intents"].get("pending", 0):
        ui.add(yy, xx, f"Adapter: pending intent = {st['intents'].get('pending')}", 3, True)
        yy += 1

    reasons = []
    for c in cands[:8]:
        reasons += c.get("reasons", [])[:5]

    compact = []
    for r in reasons:
        r = str(r)
        if r and r not in compact:
            compact.append(r)

    if not compact:
        compact = ["No explicit block reason"]

    ui.add(yy, xx, "Main reasons:", 6, True)
    yy += 1
    for r in compact[: max(1, h - 7)]:
        color = 2 if any(k in r.upper() for k in ["HARD", "LCB", "CVAR", "LOW", "BLOCK"]) else 3
        ui.add(yy, xx, f"- {r[:half-8]}", color)
        yy += 1
        if yy >= y + h - 1:
            break


def trade_quality(r: Optional[float]) -> Tuple[str, int]:
    if r is None:
        return "NOISE", 6
    if r >= 1.0:
        return "STRONG", 1
    if r > 0.1:
        return "WEAK+", 3
    if r < -0.1:
        return "LOSS", 2
    return "NOISE", 6


def draw_last_trades(ui, y, x, h, w, st):
    ui.box(y, x, h, w, "LAST 10 TRADES / QUALITY", 4)
    trades = st["positions"].get("last", [])

    wins = 0
    losses = 0
    net_r = 0.0
    counted = 0

    for r in trades:
        rv = fnum(r.get("net_pnl_r"), fnum(r.get("pnl_r"), None))
        if rv is not None:
            counted += 1
            net_r += rv
            wins += 1 if rv > 0.1 else 0
            losses += 1 if rv < -0.1 else 0

    ui.add(y + 1, x + 2, f"Last {len(trades)} | W {wins} L {losses} | Net R {fmt_signed(net_r,2)}", 1 if net_r >= 0 else 2, True)

    yy = y + 3
    for i, r in enumerate(trades[: h - 4], 1):
        rv = fnum(r.get("net_pnl_r"), fnum(r.get("pnl_r"), None))
        usd = fnum(r.get("net_pnl_usd"), fnum(r.get("pnl_usd"), None))
        q, col = trade_quality(rv)
        sym = str(r.get("symbol", "-")).replace("USDT", "")
        side = str(r.get("side", "-"))[:5]
        setup = str(r.get("setup", "-"))[:22]
        exit_reason = str(r.get("manager_state", r.get("reason", r.get("status", "-"))))[:12]

        ui.add(
            yy,
            x + 2,
            f"{i:02d} {q:<6} {sym:<4} {side:<5} R {fmt_signed(rv,2):>7} $ {fmt_signed(usd,2):>8} {setup:<22} {exit_reason}",
            col,
        )
        yy += 1


def draw_system(ui, y, x, h, w, st):
    ui.box(y, x, h, w, "SYSTEM / DATA FRESHNESS", 6)
    col_db = 1 if st["db_ok"] else 2
    ui.add(y + 1, x + 2, f"DB {'OK' if st['db_ok'] else 'BAD'}", col_db, True)

    xx = x + 12
    for name, ok in st["services"].items():
        ui.add(y + 1, xx, f"{name}:{'OK' if ok else 'DEAD'}", 1 if ok else 2, True)
        xx += len(name) + 8

    ch = st.get("contract_health", {})
    ui.add(
        y + 2,
        x + 2,
        f"Contract: {ch.get('result_state','N/A')} | {ch.get('summary','N/A')} | emitted {ch.get('emitted','N/A')}",
        6,
    )

    if st.get("error"):
        ui.add(y + h - 2, x + 2, f"ERROR {st['error']}", 2, True)
    else:
        ui.add(y + h - 2, x + 2, "q sortir | r refresh | dashboard read-only | no execution", 6)


def draw(stdscr, st):
    ui = UI(stdscr)
    ui.s.erase()

    H, W = ui.H, ui.W
    ui.add(0, 0, f"{VERSION} | {now_utc()} | q sortir | r refresh", 6, True)

    y = 1
    margin = 1
    gap = 1
    full_w = W - 2

    asset_h = 10
    open_h = 8
    market_h = 7
    last_h = 10
    sys_h = 4
    mid_h = max(10, H - (1 + asset_h + open_h + market_h + last_h + sys_h + 2))

    half = (full_w - gap) // 2

    draw_symbol(ui, y, margin, asset_h, half, "BTC PANEL", st["symbols"].get("BTCUSDT", {}))
    draw_symbol(ui, y, margin + half + gap, asset_h, full_w - half - gap, "ETH PANEL", st["symbols"].get("ETHUSDT", {}))
    y += asset_h + 1

    draw_open_pending(ui, y, margin, open_h, full_w, st)
    y += open_h + 1

    draw_market(ui, y, margin, market_h, full_w, st)
    y += market_h + 1

    draw_quant_and_why(ui, y, margin, mid_h, full_w, st)
    y += mid_h + 1

    draw_last_trades(ui, y, margin, last_h, full_w, st)
    y += last_h + 1

    draw_system(ui, y, margin, min(sys_h, H - y - 1), full_w, st)

    ui.s.refresh()


def main(stdscr):
    curses.curs_set(0)
    stdscr.timeout(1000)

    last_collect = 0
    st = collect_state()

    while True:
        now = time.time()
        if now - last_collect >= 60:
            st = collect_state()
            last_collect = now

        draw(stdscr, st)

        ch = stdscr.getch()
        if ch in (ord("q"), ord("Q")):
            break
        if ch in (ord("r"), ord("R")):
            st = collect_state()
            last_collect = time.time()


if __name__ == "__main__":
    curses.wrapper(main)
