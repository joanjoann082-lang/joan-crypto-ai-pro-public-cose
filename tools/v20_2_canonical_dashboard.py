#!/usr/bin/env python3
from __future__ import annotations

import curses, json, re, sqlite3, time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

DB_PATH = Path("data/joanbot_v14.sqlite")
VERSION = "V20.2.1 CANONICAL QUANT COMMAND DASHBOARD"

MARKET = "institutional_market_data_latest_v18_9"
HEALTH = "institutional_market_data_health_v18_9"
LIQ = "institutional_liquidation_rollup_latest_v18_10"
BRAIN = "institutional_quant_brain_v17_5_1"
INTENTS = "institutional_quant_canary_execution_intents_v17_7_2"
PAPER = "paper_micro_canary_positions_v11"
POSITIONS = "positions"

C = {}

def now_utc():
    return datetime.now(timezone.utc).strftime("%H:%M:%S UTC")

def qid(x):
    return '"' + str(x).replace('"', '""') + '"'

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
        d = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.astimezone(timezone.utc)
    except Exception:
        return None

def age_min(ts):
    d = parse_ts(ts)
    if not d:
        return None
    return max(0.0, (datetime.now(timezone.utc) - d).total_seconds() / 60)

def fmt(v, nd=2, na="N/A"):
    z = fnum(v)
    if z is None:
        return na
    if abs(z) >= 1_000_000_000:
        return f"{z/1_000_000_000:.2f}B"
    if abs(z) >= 1_000_000:
        return f"{z/1_000_000:.2f}M"
    if abs(z) >= 10_000:
        return f"{z:,.0f}"
    if abs(z) >= 100:
        return f"{z:,.1f}"
    return f"{z:.{nd}f}"

def init_colors():
    global C
    C = {}
    if not curses.has_colors():
        return
    curses.start_color()
    try:
        curses.use_default_colors()
    except Exception:
        pass
    pairs = [
        ("red", curses.COLOR_RED),
        ("green", curses.COLOR_GREEN),
        ("yellow", curses.COLOR_YELLOW),
        ("cyan", curses.COLOR_CYAN),
        ("magenta", curses.COLOR_MAGENTA),
        ("blue", curses.COLOR_BLUE),
        ("white", curses.COLOR_WHITE),
    ]
    for i, (name, fg) in enumerate(pairs, 1):
        try:
            curses.init_pair(i, fg, -1)
            C[name] = curses.color_pair(i)
        except Exception:
            C[name] = 0

def col(name):
    return C.get(name, 0)

def status_col(s):
    s = str(s or "").upper()
    if s in ("OK", "LIVE", "LIVE_CONNECTED"):
        return col("green")
    if "DEGRADED" in s or "REVIEW" in s or s == "STALE":
        return col("yellow")
    if "BAD" in s or "BLOCK" in s or s in ("MISS", "INVALID", "DISCONNECTED"):
        return col("red")
    return col("white")

def pnl_col(v):
    z = fnum(v, 0.0) or 0.0
    return col("green") if z >= 0 else col("red")

def add(w, y, x, txt, c=0, bold=False):
    try:
        h, ww = w.getmaxyx()
        if y < 0 or y >= h or x < 0 or x >= ww:
            return
        s = str(txt)
        attr = c | (curses.A_BOLD if bold else 0)
        w.addstr(y, x, s[:max(0, ww-x-1)], attr)
    except Exception:
        pass

def box(w, y, x, h, ww, title, c=0):
    if h < 3 or ww < 8:
        return
    try:
        w.attron(c)
        w.addch(y, x, curses.ACS_ULCORNER)
        w.addch(y, x+ww-1, curses.ACS_URCORNER)
        w.addch(y+h-1, x, curses.ACS_LLCORNER)
        w.addch(y+h-1, x+ww-1, curses.ACS_LRCORNER)
        for xx in range(x+1, x+ww-1):
            w.addch(y, xx, curses.ACS_HLINE)
            w.addch(y+h-1, xx, curses.ACS_HLINE)
        for yy in range(y+1, y+h-1):
            w.addch(yy, x, curses.ACS_VLINE)
            w.addch(yy, x+ww-1, curses.ACS_VLINE)
        w.attroff(c)
    except Exception:
        pass
    add(w, y, x+2, f" {title} ", c, True)

def bar(w, y, x, width, value, maxv=100.0, c=0, label=""):
    v = max(0.0, min(float(fnum(value, 0.0) or 0.0), maxv))
    n = int(width * v / maxv) if maxv else 0
    add(w, y, x, "█"*n, c)
    add(w, y, x+n, "░"*max(0, width-n), c | curses.A_DIM)
    if label:
        add(w, y, x, label, col("white"), True)

def spark(vals, width):
    vals = vals[-width:]
    if not vals:
        return " " * width
    if len(vals) < width:
        vals = [vals[0]] * (width-len(vals)) + vals
    mn, mx = min(vals), max(vals)
    chars = "▁▂▃▄▅▆▇█"
    out = []
    for v in vals:
        i = 3 if mx == mn else int((v-mn)/(mx-mn)*(len(chars)-1))
        out.append(chars[max(0, min(i, len(chars)-1))])
    return "".join(out)

class ReadOnlyDB:
    def __enter__(self):
        self.err = None
        try:
            self.con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=5)
        except Exception as e:
            self.err = repr(e)
            self.con = None
            return self

        self.con.row_factory = sqlite3.Row
        try:
            self.con.execute("PRAGMA busy_timeout=5000")
        except Exception:
            pass
        self._cols = {}
        return self

    def __exit__(self, *args):
        try:
            if self.con:
                self.con.close()
        except Exception:
            pass

    def exists(self, table):
        if not self.con:
            return False
        try:
            return self.con.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (table,)
            ).fetchone() is not None
        except Exception:
            return False

    def cols(self, table):
        if table in self._cols:
            return self._cols[table]
        if not self.exists(table):
            self._cols[table] = set()
            return set()
        try:
            self._cols[table] = {r[1] for r in self.con.execute(f"PRAGMA table_info({qid(table)})")}
        except Exception:
            self._cols[table] = set()
        return self._cols[table]

    def rows(self, sql, args=()):
        if not self.con:
            return []
        try:
            return [dict(r) for r in self.con.execute(sql, args).fetchall()]
        except Exception:
            return []

    def one(self, sql, args=()):
        r = self.rows(sql, args)
        return r[0] if r else {}

def table_latest_ts(db, table):
    if not db.exists(table) or "ts" not in db.cols(table):
        return None
    return db.one(f"SELECT MAX(ts) ts FROM {qid(table)}").get("ts")

def load_market(db):
    if not db.exists(MARKET):
        return {}
    rows = db.rows(f"""
        SELECT metric, scope, ts, value, value_text, status, age_min, source, source_detail, payload
        FROM {qid(MARKET)}
    """)
    return {r["metric"]: r for r in rows}

def mval(m, key):
    return fnum((m.get(key) or {}).get("value"))

def mage(m, key):
    r = m.get(key) or {}
    a = fnum(r.get("age_min"))
    return a if a is not None else age_min(r.get("ts"))

def mst(m, key):
    return str((m.get(key) or {}).get("status") or "MISS")

def load_health(db):
    if not db.exists(HEALTH):
        return {}
    r = db.one(f"""
        SELECT * FROM {qid(HEALTH)}
        WHERE version LIKE '%CANONICAL_LIQUIDATION_BINDING%'
        ORDER BY id DESC LIMIT 1
    """)
    return r or db.one(f"SELECT * FROM {qid(HEALTH)} ORDER BY id DESC LIMIT 1")

def load_brain(db):
    if not db.exists(BRAIN):
        return []
    ts = table_latest_ts(db, BRAIN)
    if ts and "ts" in db.cols(BRAIN):
        r = db.rows(f"""
            SELECT * FROM {qid(BRAIN)}
            WHERE ts=?
            ORDER BY COALESCE(brain_score, score, 0) DESC
            LIMIT 100
        """, (ts,))
        if r:
            return r
    order = "id DESC" if "id" in db.cols(BRAIN) else "rowid DESC"
    return db.rows(f"SELECT * FROM {qid(BRAIN)} ORDER BY {order} LIMIT 100")

def score(r):
    return fnum(r.get("brain_score"), fnum(r.get("score"), 0.0)) or 0.0

def side_scores(brain, symbol):
    rows = [r for r in brain if str(r.get("symbol") or "").upper() == symbol]
    longs = [score(r) for r in rows if str(r.get("side") or "").upper() == "LONG"]
    shorts = [score(r) for r in rows if str(r.get("side") or "").upper() == "SHORT"]
    return max(longs or [0.0]), max(shorts or [0.0])

def best_lane(brain, symbol):
    rows = [r for r in brain if str(r.get("symbol") or "").upper() == symbol]
    rows.sort(key=score, reverse=True)
    return rows[0] if rows else {}

def reason_tokens(brain, n=14):
    out = {}
    for r in brain:
        txt = " ".join(str(r.get(k) or "") for k in ("reasons","reason","hard_vetoes","payload"))
        for t in re.findall(r"[A-Z][A-Z0-9_]{3,}", txt):
            if t not in ("BTCUSDT","ETHUSDT","LONG","SHORT"):
                out[t] = out.get(t,0)+1
    return sorted(out.items(), key=lambda x:x[1], reverse=True)[:n]

def price_history(db, metric, n=80):
    hist = "institutional_market_data_history_v18_9"
    if not db.exists(hist):
        return []
    rs = db.rows(f"""
        SELECT value FROM {qid(hist)}
        WHERE metric=? AND value IS NOT NULL
        ORDER BY id DESC LIMIT ?
    """, (metric, n))
    vals = [fnum(r.get("value")) for r in reversed(rs)]
    return [v for v in vals if v is not None]

def last_trades(db):
    out = []
    for table in (PAPER, POSITIONS):
        if not db.exists(table):
            continue
        cols = db.cols(table)
        order = "id DESC" if "id" in cols else "rowid DESC"
        where = ""
        if "status" in cols:
            where = "WHERE UPPER(COALESCE(status,'')) IN ('CLOSED','DONE','EXITED')"
        elif "closed_at" in cols:
            where = "WHERE closed_at IS NOT NULL"
        for r in db.rows(f"SELECT * FROM {qid(table)} {where} ORDER BY {order} LIMIT 12"):
            r["_table"] = table
            out.append(r)
    out.sort(key=lambda r: str(r.get("closed_at") or r.get("ts") or ""), reverse=True)
    return out[:10]

def open_positions(db):
    out = []
    for table in (PAPER, POSITIONS):
        if not db.exists(table):
            continue
        cols = db.cols(table)
        order = "id DESC" if "id" in cols else "rowid DESC"
        wh = []
        if "status" in cols:
            wh.append("UPPER(COALESCE(status,'')) IN ('OPEN','ACTIVE','RUNNING','OPEN_MANAGED')")
        if "closed_at" in cols:
            wh.append("closed_at IS NULL")
        sql = f"SELECT * FROM {qid(table)}"
        if wh:
            sql += " WHERE " + " OR ".join(wh)
        sql += f" ORDER BY {order} LIMIT 5"
        out += db.rows(sql)
    return out[:5]

def intents(db):
    if not db.exists(INTENTS):
        return []
    order = "id DESC" if "id" in db.cols(INTENTS) else "rowid DESC"
    return db.rows(f"SELECT * FROM {qid(INTENTS)} ORDER BY {order} LIMIT 8")

def draw_symbol(w, y, x, h, ww, coin, market, brain, db):
    c = col("cyan") if coin == "BTC" else col("magenta")
    sym = coin + "USDT"
    box(w, y, x, h, ww, f"{coin} CANONICAL", c)

    price = mval(market, f"{coin}_PRICE")
    chg = mval(market, f"{coin}_CHANGE_24H")
    add(w, y+1, x+2, f"Price {fmt(price,2)}", pnl_col(chg), True)
    add(w, y+1, x+22, f"{fmt(chg,2)}%", pnl_col(chg), True)
    add(w, y+2, x+2, spark(price_history(db, f"{coin}_PRICE", ww-6), ww-6), pnl_col(chg))

    l, s = side_scores(brain, sym)
    add(w, y+3, x+2, "LONG ", col("green"))
    bar(w, y+3, x+8, ww-14, l, 100, col("green"), f"{l:.1f}")
    add(w, y+4, x+2, "SHORT", col("red"))
    bar(w, y+4, x+8, ww-14, s, 100, col("red"), f"{s:.1f}")

    b = best_lane(brain, sym)
    state = str(b.get("authority_state") or b.get("state") or "-")
    add(w, y+5, x+2, f"BEST {b.get('side','-')} {str(b.get('setup','-'))[:26]}", status_col(state), True)
    add(w, y+6, x+2, f"State {state[:18]} | score {score(b):.1f}", status_col(state))

    lcb = fnum(b.get("institutional_lcb_r"), fnum(b.get("lcb95_r")))
    pf = fnum(b.get("profit_factor"))
    q = fnum(b.get("fdr_q"), fnum(b.get("q")))
    add(w, y+7, x+2, f"LCB {fmt(lcb,2)} | PF {fmt(pf,2)} | q {fmt(q,2)}", col("white"))

    liq = mval(market, f"{coin}_LIQUIDATIONS")
    cvd = mval(market, f"{coin}_CVD")
    add(w, y+8, x+2, f"age {fmt(mage(market,f'{coin}_PRICE'),1)}m | CVD {fmt(cvd,0)} | Liq15 {fmt(liq,0)} {mst(market,f'{coin}_LIQUIDATIONS')}",
        status_col(mst(market,f"{coin}_LIQUIDATIONS")))

def draw_open(w, y, x, h, ww, db):
    box(w, y, x, h, ww, "OPEN / PENDING / ADAPTER", col("green"))
    op = open_positions(db)
    it = intents(db)
    if op:
        p = op[0]
        add(w, y+1, x+2, f"OPEN {p.get('symbol','')} {p.get('side','')} {p.get('setup','')}", col("green"), True)
        add(w, y+2, x+2, f"entry {fmt(p.get('entry_price') or p.get('entry'),2)} | SL {fmt(p.get('stop_price') or p.get('sl'),2)} | TP {fmt(p.get('take_profit_price') or p.get('tp1'),2)} | R {fmt(p.get('net_pnl_r') or p.get('pnl_r'),2)}")
    else:
        add(w, y+1, x+2, "No open paper trade", col("yellow"))

    pending = [r for r in it if "PENDING" in str(r.get("adapter_status") or r.get("intent_state") or "").upper()]
    rejected = [r for r in it if "REJECT" in str(r.get("adapter_status") or r.get("intent_state") or "").upper()]
    add(w, y+3, x+2, f"pending {len(pending)} | recent rejects {len(rejected)}", col("cyan"))
    if it:
        r = it[0]
        add(w, y+4, x+2, f"last {r.get('symbol','')} {r.get('side','')} {str(r.get('setup',''))[:28]}")
        add(w, y+5, x+2, f"intent {r.get('intent_state','')} | adapter {r.get('adapter_status','')}", status_col(str(r.get("adapter_status",""))))

def draw_macro(w, y, x, h, ww, market):
    box(w, y, x, h, ww, "MACRO CANONICAL", col("yellow"))
    items = [("VIX",40),("FEAR_GREED",100),("DXY",120),("NASDAQ",40000),("NASDAQ_CHANGE",5),("US10Y",8)]
    for i, (m, mx) in enumerate(items[:h-2]):
        v = mval(market, m)
        st = mst(market, m)
        c = pnl_col(v) if m == "NASDAQ_CHANGE" else status_col(st)
        add(w, y+1+i, x+2, f"{m:<13} {fmt(v,2):>10} age {fmt(mage(market,m),1)}m {st}", c)
        bar(w, y+1+i, x+34, max(4, ww-37), abs(fnum(v,0) or 0), mx, c)

def draw_derivs(w, y, x, h, ww, market):
    box(w, y, x, h, ww, "DERIVATIVES / FLOW", col("magenta"))
    items = [
        ("BTC funding","BTC_FUNDING"),("BTC OI","BTC_OI"),("BTC L/S","BTC_LONG_SHORT"),("BTC CVD","BTC_CVD"),("BTC liq15","BTC_LIQUIDATIONS"),
        ("ETH funding","ETH_FUNDING"),("ETH OI","ETH_OI"),("ETH L/S","ETH_LONG_SHORT"),("ETH CVD","ETH_CVD"),("ETH liq15","ETH_LIQUIDATIONS"),
    ]
    for i, (lab, m) in enumerate(items[:h-2]):
        v = mval(market,m)
        st = mst(market,m)
        c = status_col(st)
        if "CVD" in m:
            c = pnl_col(v)
        add(w, y+1+i, x+2, f"{lab:<12} {fmt(v,4) if 'funding' in lab else fmt(v,2):>12} {st:<5} age {fmt(mage(market,m),1)}m", c)

def draw_brain(w, y, x, h, ww, brain):
    box(w, y, x, h, ww, "QUANT BRAIN / SIDE BIAS", col("blue"))
    states = {}
    for r in brain:
        s = str(r.get("authority_state") or r.get("state") or "UNKNOWN")
        states[s] = states.get(s,0)+1
    add(w, y+1, x+2, " | ".join(f"{k}:{v}" for k,v in list(states.items())[:4]), col("white"))

    yy = y+3
    for sym in ("BTCUSDT","ETHUSDT"):
        l,s = side_scores(brain, sym)
        bias = "SHORT" if s > l else "LONG" if l > s else "FLAT"
        c = col("red") if bias == "SHORT" else col("green") if bias == "LONG" else col("yellow")
        add(w, yy, x+2, f"{sym:<7} bias {bias:<5} L {l:.1f} / S {s:.1f}", c, True)
        bar(w, yy+1, x+2, ww-6, max(l,s), 100, c)
        yy += 3

    add(w, yy, x+2, "Top alpha lanes:", col("cyan"), True)
    yy += 1
    rows = sorted(brain, key=score, reverse=True)
    for r in rows[:max(0,h-(yy-y)-1)]:
        side = str(r.get("side") or "")
        c = col("red") if side.upper()=="SHORT" else col("green")
        add(w, yy, x+2, f"{score(r):5.1f} {str(r.get('symbol',''))[:4]:<4} {side[:5]:<5} {str(r.get('setup',''))[:24]:<24} {str(r.get('authority_state') or r.get('state') or '')[:10]}", c)
        yy += 1

def draw_why(w, y, x, h, ww, brain, health):
    box(w, y, x, h, ww, "WHY BLOCKED / SYSTEM", col("yellow"))
    summary = str(health.get("summary") or "UNKNOWN")
    add(w, y+1, x+2, f"Health {summary}", status_col(summary), True)
    add(w, y+2, x+2, str(health.get("version") or "")[:ww-5], col("white"))
    add(w, y+3, x+2, f"live {health.get('live_count','?')} stale {health.get('stale_count','?')} miss {health.get('miss_count','?')} invalid {health.get('invalid_count','?')}", status_col(summary))
    add(w, y+5, x+2, "Main blockers:", col("cyan"), True)
    yy = y+6
    for t,n in reason_tokens(brain, h-8):
        c = col("red") if any(k in t for k in ("LOW","NEGATIVE","BLOCK","HARD","LCB")) else col("yellow")
        add(w, yy, x+3, f"{t:<34} {n}", c)
        yy += 1
        if yy >= y+h-1:
            break

def draw_trades(w, y, x, h, ww, db):
    box(w, y, x, h, ww, "LAST 10 TRADES / QUALITY", col("cyan"))
    tr = last_trades(db)
    if not tr:
        add(w, y+1, x+2, "No closed trades found", col("yellow"))
        return
    pnls = []
    for r in tr:
        p = fnum(r.get("net_pnl_r"), fnum(r.get("pnl_r"), fnum(r.get("gross_r"), fnum(r.get("net_pnl_usd"), fnum(r.get("pnl_usd"),0)))))
        pnls.append(p or 0)
    total = sum(pnls)
    add(w, y+1, x+2, f"Last {len(tr)} | W {sum(p>0 for p in pnls)} L {sum(p<0 for p in pnls)} | Net {total:+.2f}", pnl_col(total), True)
    yy = y+2
    for i,r in enumerate(tr[:h-3],1):
        p = fnum(r.get("net_pnl_r"), fnum(r.get("pnl_r"), fnum(r.get("gross_r"), fnum(r.get("net_pnl_usd"), fnum(r.get("pnl_usd"),0))))) or 0
        tag = "STRONG" if p >= 1 else "WEAK+" if p > 0 else "LOSS" if p < 0 else "NOISE"
        add(w, yy, x+2, f"{i:02d} {tag:<6} {str(r.get('symbol',''))[:7]:<7} {str(r.get('side',''))[:5]:<5} {p:+.2f}R {str(r.get('setup',''))[:32]}", pnl_col(p))
        yy += 1

def main_loop(stdscr):
    curses.curs_set(0)
    stdscr.nodelay(True)
    stdscr.timeout(1000)
    init_colors()

    while True:
        ch = stdscr.getch()
        if ch in (ord("q"), ord("Q")):
            break

        stdscr.erase()
        h,w = stdscr.getmaxyx()
        add(stdscr, 0, 0, f"{VERSION} | {now_utc()} | q sortir | r refresh".ljust(w-1), curses.A_REVERSE)

        if h < 34 or w < 90:
            add(stdscr, 2, 2, f"Pantalla massa petita: {w}x{h}. Redueix font o amplia Termux.", col("red"), True)
            stdscr.refresh()
            time.sleep(1)
            continue

        with ReadOnlyDB() as db:
            market = load_market(db)
            health = load_health(db)
            brain = load_brain(db)

            half = (w-3)//2
            y = 1
            draw_symbol(stdscr, y, 1, 10, half, "BTC", market, brain, db)
            draw_symbol(stdscr, y, half+2, 10, w-half-3, "ETH", market, brain, db)

            y += 11
            draw_open(stdscr, y, 1, 7, w-2, db)

            y += 8
            draw_macro(stdscr, y, 1, 8, half, market)
            draw_derivs(stdscr, y, half+2, 8, w-half-3, market)

            y += 9
            mid_h = max(10, h-y-9)
            draw_brain(stdscr, y, 1, mid_h, half, brain)
            draw_why(stdscr, y, half+2, mid_h, w-half-3, brain, health)

            ty = y + mid_h
            draw_trades(stdscr, ty, 1, max(6,h-ty-1), w-2, db)

        add(stdscr, h-1, 0, " q sortir | r refresh | canonical V18.9.5 | read-only | no execution ".ljust(w-1), curses.A_REVERSE)
        stdscr.refresh()
        time.sleep(1)

def main():
    if not DB_PATH.exists():
        print("ERROR: falta data/joanbot_v14.sqlite")
        return 2
    curses.wrapper(main_loop)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
