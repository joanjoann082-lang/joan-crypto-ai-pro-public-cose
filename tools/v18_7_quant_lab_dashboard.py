#!/usr/bin/env python3
from __future__ import annotations

import curses
import importlib.util
import math
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

ROOT = Path.cwd()
BASE_FILE = ROOT / "tools" / "v18_5_mosaic_full_dashboard.py"
PAYOFF_FILE = ROOT / "tools" / "v18_6_payoff_intelligence.py"

if not BASE_FILE.exists():
    raise SystemExit("ERROR: falta tools/v18_5_mosaic_full_dashboard.py")
if not PAYOFF_FILE.exists():
    raise SystemExit("ERROR: falta tools/v18_6_payoff_intelligence.py")

def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod

base = load_module("v18_5_base", BASE_FILE)
payoff = load_module("v18_6_payoff", PAYOFF_FILE)

VERSION = "V18.7 INSTITUTIONAL QUANT LAB DASHBOARD"
REFRESH_SEC = 60


def fnum(x: Any, default=None):
    return base.fnum(x, default)


def mean(xs: List[float]):
    return sum(xs) / len(xs) if xs else None


def stdev(xs: List[float]):
    if len(xs) < 2:
        return None
    m = mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def qtile(xs: List[float], q: float):
    if not xs:
        return None
    ys = sorted(xs)
    i = int(max(0, min(len(ys) - 1, round(q * (len(ys) - 1)))))
    return ys[i]


def cumulative_curve(xs: List[float]) -> List[float]:
    out = []
    s = 0.0
    for x in xs:
        s += x
        out.append(s)
    return out


def max_drawdown(curve: List[float]) -> float:
    if not curve:
        return 0.0
    peak = curve[0]
    dd = 0.0
    for x in curve:
        peak = max(peak, x)
        dd = min(dd, x - peak)
    return dd


def profit_factor(rs: List[float]):
    gross_win = sum(x for x in rs if x > 0.10)
    gross_loss = abs(sum(x for x in rs if x < -0.10))
    if gross_loss <= 0:
        return None
    return gross_win / gross_loss


def payoff_ratio(rs: List[float]):
    wins = [x for x in rs if x > 0.10]
    losses = [abs(x) for x in rs if x < -0.10]
    if not wins or not losses:
        return None
    return mean(wins) / mean(losses)


def breakeven_wr(rs: List[float]):
    wins = [x for x in rs if x > 0.10]
    losses = [abs(x) for x in rs if x < -0.10]
    if not wins or not losses:
        return None
    aw = mean(wins)
    al = mean(losses)
    return al / (aw + al) if aw and al else None


def winrate(rs: List[float]):
    active = [x for x in rs if abs(x) > 0.10]
    if not active:
        return None
    return len([x for x in active if x > 0.10]) / len(active)


def quality_counts(trades: List[Dict[str, Any]]) -> Dict[str, int]:
    out = {
        "STRONG_WIN": 0,
        "WEAK_WIN": 0,
        "NOISE": 0,
        "LOSS": 0,
        "HARD_LOSS": 0,
        "OPEN": 0,
        "UNKNOWN": 0,
    }
    for r in trades:
        lab = payoff.classify_trade(r)
        out[lab] = out.get(lab, 0) + 1
    return out


def payoff_analytics(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    closed = [
        r for r in trades
        if payoff.classify_trade(r) not in {"OPEN", "UNKNOWN"}
    ]

    # ordre cronològic aproximat per equity curve
    closed_chrono = list(reversed(closed))
    rs = [fnum(r.get("net_r"), 0.0) or 0.0 for r in closed_chrono]
    usd = [fnum(r.get("net_usd"), 0.0) or 0.0 for r in closed_chrono]

    n = len(rs)
    avg = mean(rs)
    sd = stdev(rs)
    lcb95 = None
    if avg is not None and sd is not None and n >= 2:
        lcb95 = avg - 1.96 * sd / math.sqrt(n)

    wr = winrate(rs)
    be = breakeven_wr(rs)
    pf = profit_factor(rs)
    pr = payoff_ratio(rs)
    curve = cumulative_curve(rs)
    dd = max_drawdown(curve)

    hard_rate = None
    if n:
        hard_rate = len([x for x in rs if x <= -1.0]) / n

    q = quality_counts(trades)

    reasons = []
    gate = "BLOCK"
    if n < 10:
        gate = "INSUFFICIENT"
        reasons.append("closed_n_lt_10")
    elif lcb95 is not None and lcb95 > 0 and pf is not None and pf >= 1.25 and wr is not None and be is not None and wr > be + 0.05:
        gate = "PROMOTE_CANDIDATE"
    elif avg is not None and avg > 0 and pf is not None and pf >= 1.0 and wr is not None and be is not None and wr > be:
        gate = "CANARY_ONLY"
        reasons.append("positive_but_not_robust")
    else:
        gate = "BLOCK"
        if avg is not None and avg <= 0:
            reasons.append("mean_r_not_positive")
        if lcb95 is not None and lcb95 <= 0:
            reasons.append("lcb95_not_positive")
        if pf is not None and pf < 1.0:
            reasons.append("profit_factor_below_1")
        if wr is not None and be is not None and wr <= be:
            reasons.append("wr_below_breakeven")

    return {
        "closed_n": n,
        "mean_r": avg,
        "std_r": sd,
        "lcb95_r": lcb95,
        "total_r": sum(rs) if rs else 0.0,
        "total_usd": sum(usd) if usd else 0.0,
        "winrate": wr,
        "breakeven_wr": be,
        "payoff_ratio": pr,
        "profit_factor": pf,
        "max_dd_r": dd,
        "hard_loss_rate": hard_rate,
        "curve": curve,
        "quality": q,
        "gate": gate,
        "reasons": reasons,
        "q05": qtile(rs, 0.05),
        "q50": qtile(rs, 0.50),
        "q95": qtile(rs, 0.95),
    }


def setup_table(trades: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    groups: Dict[Tuple[str, str, str], List[Dict[str, Any]]] = {}

    for r in trades:
        lab = payoff.classify_trade(r)
        if lab in {"OPEN", "UNKNOWN"}:
            continue
        k = (
            str(r.get("symbol") or ""),
            str(r.get("side") or ""),
            str(r.get("setup") or ""),
        )
        if not all(k):
            continue
        groups.setdefault(k, []).append(r)

    rows = []
    for (sym, side, setup), gs in groups.items():
        rs = [fnum(r.get("net_r"), 0.0) or 0.0 for r in gs]
        n = len(rs)
        avg = mean(rs)
        sd = stdev(rs)
        lcb = None
        if avg is not None and sd is not None and n >= 2:
            lcb = avg - 1.65 * sd / math.sqrt(n)
        pf = profit_factor(rs)
        wr = winrate(rs)
        be = breakeven_wr(rs)
        dd = max_drawdown(cumulative_curve(rs))

        score = 0.0
        score += 20 * base.clamp((n or 0) / 20)
        score += 25 * base.clamp(((avg or -0.5) + 0.25) / 0.75)
        score += 25 * base.clamp(((lcb if lcb is not None else -0.5) + 0.35) / 0.70)
        score += 15 * base.clamp(((pf if pf is not None else 0) - 0.70) / 1.0)
        if wr is not None and be is not None:
            score += 15 * base.clamp((wr - be + 0.10) / 0.30)

        rows.append({
            "symbol": sym,
            "side": side,
            "setup": setup,
            "n": n,
            "mean_r": avg,
            "lcb_r": lcb,
            "pf": pf,
            "wr": wr,
            "be": be,
            "dd_r": dd,
            "score": round(base.clamp(score, 0, 100), 1),
        })

    rows.sort(key=lambda x: (x["score"], x["n"]), reverse=True)
    return rows[:8]


def load_v18_7():
    d = base.load()
    try:
        con = sqlite3.connect(base.DB)
        con.row_factory = sqlite3.Row
        trades = payoff.load_trades(con, 800)
        for r in trades:
            r["quality_label"] = payoff.classify_trade(r)
        d["last_trades"] = trades[:10]
        d["quant_lab"] = payoff_analytics(trades)
        d["setup_lab"] = setup_table(trades)
        con.close()
    except Exception as e:
        d.setdefault("errors", []).append(repr(e))
        d["quant_lab"] = payoff_analytics([])
        d["setup_lab"] = []
    return d


def label_visual(label: str, C):
    if label == "STRONG_WIN":
        return "✓✓ STRONG", C["green"]
    if label == "WEAK_WIN":
        return "~  WEAK", C["yellow"]
    if label == "NOISE":
        return "·  NOISE", C["white"]
    if label == "LOSS":
        return "✗  LOSS", C["red"]
    if label == "HARD_LOSS":
        return "✗✗ HARD", C["red"]
    if label == "OPEN":
        return "●  OPEN", C["yellow"]
    return "?  UNK", C["white"]


def fmt_ratio(x):
    if x is None:
        return "N/A"
    return f"{x:.2f}"


def gauge_signed(x, width, lo=-0.50, hi=0.50):
    if x is None:
        return "NO DATA".ljust(width)
    ratio = (x - lo) / (hi - lo)
    return base.bar01(base.clamp(ratio, 0, 1), width)


def draw_quant_lab(stdscr, y, x, h, w, d, C):
    base.box(stdscr, y, x, h, w, "QUANT PAYOFF LAB", C["blue"])
    q = d.get("quant_lab") or {}

    gate = q.get("gate", "N/A")
    gate_col = C["green"] if gate == "PROMOTE_CANDIDATE" else C["yellow"] if gate in {"CANARY_ONLY", "INSUFFICIENT"} else C["red"]

    line = y + 1
    base.add(stdscr, line, x+2, base.short(f"GATE {gate}", w-4), gate_col | curses.A_BOLD)
    line += 1

    curve = q.get("curve") or []
    base.add(stdscr, line, x+2, "Equity R", curses.A_BOLD)
    base.add(stdscr, line, x+12, base.spark(curve, max(8, w-14)), C["cyan"])
    line += 1

    exp_r = q.get("mean_r")
    lcb = q.get("lcb95_r")
    dd = q.get("max_dd_r")

    base.add(stdscr, line, x+2, f"Exp {base.fmt_r(exp_r)}")
    base.add(stdscr, line, x+14, gauge_signed(exp_r, max(8, w-42), -0.50, 0.50), C["green"] if exp_r and exp_r > 0 else C["red"])
    base.add(stdscr, line, x+w-22, f"LCB {base.fmt_r(lcb)}", C["green"] if lcb and lcb > 0 else C["red"])
    line += 1

    base.add(stdscr, line, x+2, f"PF {fmt_ratio(q.get('profit_factor'))}")
    base.add(stdscr, line, x+13, f"Payoff {fmt_ratio(q.get('payoff_ratio'))}")
    base.add(stdscr, line, x+28, f"WR {base.fmt_pct(q.get('winrate'))}")
    base.add(stdscr, line, x+44, f"BE {base.fmt_pct(q.get('breakeven_wr'))}")
    base.add(stdscr, line, x+w-16, f"DD {base.fmt_r(dd)}", C["red"] if dd and dd < -1 else C["yellow"])
    line += 1

    qual = q.get("quality") or {}
    base.add(
        stdscr,
        line,
        x+2,
        base.short(
            f"N {q.get('closed_n')} | STR {qual.get('STRONG_WIN',0)} WEAK {qual.get('WEAK_WIN',0)} NOISE {qual.get('NOISE',0)} LOSS {qual.get('LOSS',0)} HARD {qual.get('HARD_LOSS',0)}",
            w-4,
        ),
    )
    line += 1

    # Distribució visual per bins
    if line < y + h - 4:
        base.add(stdscr, line, x+2, "Distribution R", curses.A_BOLD)
        line += 1

        trades = d.get("last_trades") or []
        all_trades = payoff.load_trades(sqlite3.connect(base.DB), 800) if base.DB.exists() else []
        rs = []
        for r in all_trades:
            lab = payoff.classify_trade(r)
            if lab not in {"OPEN", "UNKNOWN"}:
                rs.append(fnum(r.get("net_r"), 0.0) or 0.0)

        bins = [
            ("<-1R", lambda v: v <= -1.0, C["red"]),
            ("-1..-.1", lambda v: -1.0 < v < -0.10, C["red"]),
            ("noise", lambda v: -0.10 <= v <= 0.10, C["white"]),
            ("weak", lambda v: 0.10 < v < 0.75, C["yellow"]),
            ("strong", lambda v: v >= 0.75, C["green"]),
        ]
        max_count = 1
        counts = []
        for name, fn, col in bins:
            c = len([v for v in rs if fn(v)])
            counts.append((name, c, col))
            max_count = max(max_count, c)

        for name, c, col in counts:
            if line >= y + h - 1:
                break
            bw = max(4, w - 18)
            base.add(stdscr, line, x+2, f"{name:<8} {c:>2}")
            base.add(stdscr, line, x+14, base.bar01(c / max_count if max_count else 0, bw), col)
            line += 1

    # Setup leaders
    if line < y + h - 2:
        base.add(stdscr, line, x+2, "Setup leaders", curses.A_BOLD)
        line += 1

    for s in (d.get("setup_lab") or [])[:max(1, y+h-line-1)]:
        if line >= y + h - 1:
            break
        col = C["green"] if s["score"] >= 70 else C["yellow"] if s["score"] >= 50 else C["red"]
        base.add(
            stdscr,
            line,
            x+2,
            base.short(
                f"{s['score']:>5.1f} {s['symbol']} {s['side']} {s['setup']} n={s['n']} exp={base.fmt_r(s['mean_r'])} lcb={base.fmt_r(s['lcb_r'])}",
                w-4,
            ),
            col,
        )
        line += 1


def draw_last_trades(stdscr, y, x, h, w, d, C):
    base.box(stdscr, y, x, h, w, "LAST 10 TRADES / QUALITY", C["cyan"])
    trades = d.get("last_trades") or []

    if not trades:
        base.add(stdscr, y+2, x+2, "No trades yet", C["yellow"])
        return

    row = y + 1
    for r in trades[:max(1, h-2)]:
        if row >= y + h - 1:
            break
        label = r.get("quality_label") or payoff.classify_trade(r)
        txt, col = label_visual(label, C)
        net_r = fnum(r.get("net_r"), 0.0) or 0.0
        net_usd = fnum(r.get("net_usd"), None)
        symbol = r.get("symbol", "-")
        side = r.get("side", "-")
        setup = r.get("setup", "-")
        reason = r.get("reason") or r.get("manager_state") or ""

        if w >= 56:
            base.add(stdscr, row, x+2, txt, col | curses.A_BOLD)
            base.add(stdscr, row, x+13, base.short(f"{symbol} {side} {setup}", 26), curses.A_BOLD)
            base.add(stdscr, row, x+41, base.fmt_r(net_r), col | curses.A_BOLD)
            base.add(stdscr, row, x+50, base.result_bar(net_r, max(8, w-67)), col)
            base.add(stdscr, row, x+w-16, base.short(base.fmt_usd(net_usd), 8), col)
            base.add(stdscr, row, x+w-7, base.short(reason, 5))
        else:
            base.add(stdscr, row, x+2, base.short(f"{txt} {symbol} {side} {base.fmt_r(net_r)}", w-4), col | curses.A_BOLD)
        row += 1


def draw_bottom(stdscr, y, x, h, w, d, C):
    base.box(stdscr, y, x, h, w, "SYSTEM / RISK VERDICT", C["white"])

    q = d.get("quant_lab") or {}
    db_col = C["green"] if d.get("db") == "ok" else C["red"]

    base.add(stdscr, y+1, x+2, f"DB {d.get('db')}", db_col | curses.A_BOLD)

    sx = x + 12
    for name, cnt in (d.get("services") or {}).items():
        ok = cnt >= 1
        base.add(stdscr, y+1, sx, f"{name}:{'OK' if ok else 'OFF'}", C["green"] if ok else C["red"])
        sx += len(name) + 8
        if sx > x + w - 14:
            break

    a = d.get("adapter") or {}
    base.add(
        stdscr,
        y+2,
        x+2,
        base.short(
            f"Gate={q.get('gate')} | Exp={base.fmt_r(q.get('mean_r'))} | LCB95={base.fmt_r(q.get('lcb95_r'))} | Total={base.fmt_r(q.get('total_r'))} | USD={base.fmt_usd(q.get('total_usd'))}",
            w-4,
        ),
    )
    base.add(
        stdscr,
        y+3,
        x+2,
        base.short(
            f"Adapter pending={a.get('pending_intents')} managed={a.get('managed_positions')} closed={a.get('closed_positions')} errors={a.get('errors')} | reasons={','.join(q.get('reasons') or []) or 'none'}",
            w-4,
        ),
    )


def draw(stdscr):
    curses.curs_set(0)
    stdscr.nodelay(True)
    C = base.init_colors()

    data = load_v18_7()
    last = time.time()

    while True:
        if time.time() - last >= REFRESH_SEC:
            data = load_v18_7()
            last = time.time()

        d = data
        stdscr.erase()
        h, w = stdscr.getmaxyx()

        base.add(stdscr, 0, 0, " " * max(1, w-1), curses.A_REVERSE)
        base.add(stdscr, 0, 1, base.short(f"{VERSION} | {base.utc_now()} | q sortir | r refresh", w-3), curses.A_REVERSE | curses.A_BOLD)

        margin = 1
        gap = 1
        usable_w = max(20, w - 2)
        left_w = (usable_w - gap) // 2
        right_w = usable_w - gap - left_w
        left_x = margin
        right_x = margin + left_w + gap

        cands = d.get("candidates") or []
        btc_cand = base.candidate_for_symbol(cands, "BTCUSDT")
        eth_cand = base.candidate_for_symbol(cands, "ETHUSDT")

        top_h = 10
        trade_h = 8
        mid_h = 7
        bottom_h = 5
        footer_h = 1

        y = 2

        if w >= 62:
            base.draw_asset_card(stdscr, y, left_x, top_h, left_w, "BTC", "BTCUSDT", d["btc"].get("price"), d["btc_series"], btc_cand, C)
            base.draw_asset_card(stdscr, y, right_x, top_h, right_w, "ETH", "ETHUSDT", d["eth"].get("price"), d["eth_series"], eth_cand, C)
            y += top_h + 1
        else:
            base.draw_asset_card(stdscr, y, margin, top_h, usable_w, "BTC", "BTCUSDT", d["btc"].get("price"), d["btc_series"], btc_cand, C)
            y += top_h + 1
            base.draw_asset_card(stdscr, y, margin, top_h, usable_w, "ETH", "ETHUSDT", d["eth"].get("price"), d["eth_series"], eth_cand, C)
            y += top_h + 1

        base.draw_open_trade(stdscr, y, margin, trade_h, usable_w, d, C)
        y += trade_h + 1

        if w >= 62:
            base.draw_macro_card(stdscr, y, left_x, mid_h, left_w, d, C)
            base.draw_deriv_card(stdscr, y, right_x, mid_h, right_w, d, C)
            y += mid_h + 1
        else:
            base.draw_macro_card(stdscr, y, margin, mid_h, usable_w, d, C)
            y += mid_h + 1

        available = h - y - bottom_h - footer_h - 2
        available = max(10, available)

        if w >= 86:
            lab_w = int(usable_w * 0.58)
            trades_w = usable_w - lab_w - gap
            lab_x = margin
            trades_x = margin + lab_w + gap
            draw_quant_lab(stdscr, y, lab_x, available, lab_w, d, C)
            draw_last_trades(stdscr, y, trades_x, available, trades_w, d, C)
            y += available + 1
        else:
            lab_h = max(8, available // 2)
            trades_h = max(8, available - lab_h - 1)
            draw_quant_lab(stdscr, y, margin, lab_h, usable_w, d, C)
            y += lab_h + 1
            draw_last_trades(stdscr, y, margin, trades_h, usable_w, d, C)
            y += trades_h + 1

        if y + bottom_h <= h - 1:
            draw_bottom(stdscr, y, margin, bottom_h, usable_w, d, C)

        base.add(stdscr, h-1, 0, " " * max(1, w-1), curses.A_REVERSE)
        base.add(stdscr, h-1, 1, base.short("q sortir | r refresh | quant lab | payoff corrected | no execution", w-3), curses.A_REVERSE)

        if d.get("errors") and h > 4:
            base.add(stdscr, h-2, 1, base.short("ERR " + str(d["errors"][-1]), w-3), C["red"])

        stdscr.refresh()

        for _ in range(REFRESH_SEC * 10):
            c = stdscr.getch()
            if c in (ord("q"), ord("Q")):
                return
            if c in (ord("r"), ord("R")):
                data = load_v18_7()
                last = time.time()
                break
            time.sleep(0.1)


def main():
    curses.wrapper(draw)


if __name__ == "__main__":
    main()
