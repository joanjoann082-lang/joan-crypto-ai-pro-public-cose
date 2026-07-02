from __future__ import annotations

import json
import os
import socket
import subprocess
import traceback
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from ..config import CFG, LOG_DIR, STATE_PATH
from ..utils import read_json, tail_lines, fnum
from ..storage import get_db

SERVER_NAME = "JoanBotDashboard/18.0-MobileCommandCenter"
REFRESH_MS = int(float(os.getenv("DASHBOARD_REFRESH_MS", "3000")))
SYMBOLS = ("BTCUSDT", "ETHUSDT")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def jloads(x: Any, default: Any = None) -> Any:
    if default is None:
        default = {}
    if isinstance(x, (dict, list)):
        return x
    if x in (None, ""):
        return default
    try:
        return json.loads(str(x))
    except Exception:
        return default


def clamp(x: Any, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, fnum(x, 0.0)))


def pick(d: Any, paths: List[str], default: Any = None) -> Any:
    for path in paths:
        cur = d
        ok = True
        for part in path.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                ok = False
                break
        if ok and cur not in (None, "", [], {}):
            return cur
    return default


def nonzero(*vals: Any) -> Any:
    for v in vals:
        if v in (None, "", [], {}):
            continue
        try:
            fv = float(v)
            if abs(fv) > 1e-12:
                return v
        except Exception:
            return v
    return None


def table_exists(db, table: str) -> bool:
    try:
        return bool(db.query("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)))
    except Exception:
        return False


def safe_query(db, sql: str, params: Tuple[Any, ...] = ()) -> List[Dict[str, Any]]:
    try:
        return db.query(sql, params)
    except Exception:
        return []


def latest_row(db, table: str, where: str = "", params: Tuple[Any, ...] = ()) -> Optional[Dict[str, Any]]:
    if not table_exists(db, table):
        return None
    sql = f"SELECT * FROM {table}"
    if where:
        sql += " WHERE " + where
    sql += " ORDER BY id DESC LIMIT 1"
    rows = safe_query(db, sql, params)
    return rows[0] if rows else None


def count_table(db, table: str) -> int:
    if not table_exists(db, table):
        return 0
    try:
        return int(db.query(f"SELECT COUNT(*) c FROM {table}")[0]["c"])
    except Exception:
        return 0


def freshness(ts: Any) -> Dict[str, Any]:
    if not ts:
        return {"label": "—", "age_sec": None, "class": "bad"}
    try:
        txt = str(ts).replace("Z", "+00:00")
        dt = datetime.fromisoformat(txt)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        age = max(0.0, (datetime.now(timezone.utc) - dt).total_seconds())
        cls = "good" if age < 90 else "warn" if age < 600 else "bad"
        if age < 60:
            label = f"{int(age)}s"
        elif age < 3600:
            label = f"{int(age//60)}m"
        else:
            label = f"{age/3600:.1f}h"
        return {"label": label, "age_sec": age, "class": cls}
    except Exception:
        return {"label": str(ts)[:19], "age_sec": None, "class": "warn"}


def get_ips() -> Dict[str, List[str]]:
    lan: List[str] = []
    tail: List[str] = []

    def add(ip: str) -> None:
        if not ip or ":" in ip or ip.startswith("127."):
            return
        if ip.startswith("100."):
            if ip not in tail:
                tail.append(ip)
        elif ip not in lan:
            lan.append(ip)

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        add(s.getsockname()[0])
        s.close()
    except Exception:
        pass
    try:
        out = subprocess.check_output(["ip", "-4", "addr", "show"], text=True, timeout=1.2, stderr=subprocess.DEVNULL)
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("inet "):
                add(line.split()[1].split("/")[0])
    except Exception:
        pass
    try:
        out = subprocess.check_output(["tailscale", "ip", "-4"], text=True, timeout=1.2, stderr=subprocess.DEVNULL)
        for line in out.splitlines():
            add(line.strip())
    except Exception:
        pass
    return {"lan": lan[:4], "tailscale": tail[:4]}


def latest_payload_table(db, table: str, symbol: str) -> Dict[str, Any]:
    r = latest_row(db, table, "symbol=?", (symbol,))
    if not r:
        return {}
    p = jloads(r.get("payload"), {})
    for k, v in r.items():
        if k != "payload" and v not in (None, ""):
            p.setdefault(k, v)
    p["_row"] = r
    return p


def latest_decisions(db, limit: int = 24) -> List[Dict[str, Any]]:
    if not table_exists(db, "decisions"):
        return []
    rows = safe_query(db, "SELECT * FROM decisions ORDER BY id DESC LIMIT ?", (limit,))
    out = []
    for r in rows:
        p = jloads(r.get("payload"), {})
        d = dict(p)
        for k in ["id", "ts", "mode", "symbol", "action", "side", "setup", "final_score", "confidence", "size_usd"]:
            if r.get(k) not in (None, ""):
                d[k] = r.get(k)
        d["freshness"] = freshness(d.get("ts"))
        out.append(d)
    return out


def latest_decision_for(decisions: List[Dict[str, Any]], symbol: str) -> Dict[str, Any]:
    for d in decisions:
        if str(d.get("symbol", "")).upper() == symbol:
            return d
    return {"symbol": symbol, "action": "WAIT", "side": "LONG", "setup": "NO_RECENT_DECISION", "final_score": 0, "confidence": 0}


def atr_fallback(symbol: str, price: float) -> float:
    return max(price * (0.008 if symbol == "BTCUSDT" else 0.011), 1.0)


def level_map(decision: Dict[str, Any], feature: Dict[str, Any], market: Dict[str, Any], symbol: str) -> Dict[str, Any]:
    side = str(decision.get("side") or "LONG").upper()
    entry = fnum(nonzero(
        pick(decision, ["entry", "entry_price", "execution.entry", "levels.entry", "signal.entry"]),
        market.get("price"),
        pick(feature, ["price", "technical.price", "technical.timeframes.15m.close", "technical.timeframes.1h.close"]),
    ), 0)
    sl = fnum(nonzero(pick(decision, ["sl", "stop", "stop_loss", "levels.sl", "levels.stop", "levels.stop_loss", "risk.stop_loss"])), 0)
    tp1 = fnum(nonzero(pick(decision, ["tp1", "take_profit_1", "levels.tp1", "targets.tp1"])), 0)
    tp2 = fnum(nonzero(pick(decision, ["tp2", "take_profit_2", "levels.tp2", "targets.tp2"])), 0)
    tp3 = fnum(nonzero(pick(decision, ["tp3", "take_profit_3", "levels.tp3", "targets.tp3"])), 0)
    source = "decision"
    if entry > 0 and (sl <= 0 or tp1 <= 0 or tp2 <= 0 or tp3 <= 0):
        atr = fnum(nonzero(pick(feature, ["technical.timeframes.15m.atr", "technical.timeframes.1h.atr", "technical.atr", "atr"])), 0)
        if atr <= 0:
            atr = atr_fallback(symbol, entry)
            source = "ATR fallback"
        else:
            source = "ATR"
        if side == "SHORT":
            sl = sl or entry + atr * 1.25
            tp1 = tp1 or entry - atr * 1.10
            tp2 = tp2 or entry - atr * 1.85
            tp3 = tp3 or entry - atr * 2.65
        else:
            sl = sl or entry - atr * 1.25
            tp1 = tp1 or entry + atr * 1.10
            tp2 = tp2 or entry + atr * 1.85
            tp3 = tp3 or entry + atr * 2.65
    risk = abs(entry - sl) if entry > 0 and sl > 0 else 0
    reward = abs(tp2 - entry) if entry > 0 and tp2 > 0 else 0
    return {"entry": entry, "sl": sl, "tp1": tp1, "tp2": tp2, "tp3": tp3, "rr": reward / risk if risk else 0, "source": source}


def reasons(decision: Dict[str, Any], feature: Dict[str, Any]) -> List[str]:
    raw: List[str] = []
    for path in ["reasons", "final_reasons", "blockers", "warnings", "why", "explain", "metadata.reasons", "reasoning.notes"]:
        v = pick(decision, [path])
        if isinstance(v, list):
            raw += [str(x) for x in v]
        elif isinstance(v, str):
            raw += [x.strip() for x in v.replace(";", ",").split(",") if x.strip()]
    flags = pick(feature, ["flags"], {}) or {}
    if isinstance(flags, dict):
        raw += [k for k, v in flags.items() if v is True]
    if not raw:
        raw = ["Sense edge suficient / falta confirmació"]
    out = []
    seen = set()
    for x in raw:
        x = str(x).replace("_", " ").strip()
        if x and x not in seen:
            seen.add(x)
            out.append(x[:76])
    return out[:5]


def tf_view(feature: Dict[str, Any]) -> List[Dict[str, Any]]:
    tfs = pick(feature, ["technical.timeframes"], {}) or {}
    out = []
    for tf in ["15m", "1h", "4h", "1d"]:
        x = tfs.get(tf, {}) if isinstance(tfs, dict) else {}
        out.append({"tf": tf, "state": str(x.get("state") or "—"), "score": fnum(x.get("score"), 0), "rsi": fnum(x.get("rsi"), 0)})
    return out


def symbol_state(db, symbol: str, decisions: List[Dict[str, Any]]) -> Dict[str, Any]:
    market = latest_payload_table(db, "market_snapshots", symbol)
    feature = latest_payload_table(db, "features", symbol)
    der = latest_payload_table(db, "derivatives_snapshots", symbol)
    of = latest_payload_table(db, "orderflow_snapshots", symbol)
    dec = latest_decision_for(decisions, symbol)
    lv = level_map(dec, feature, market, symbol)
    score = clamp(nonzero(dec.get("final_score"), dec.get("score")))
    action = str(dec.get("action") or "WAIT").upper()
    if action == "OPEN":
        readiness = max(score, 78)
    elif action == "PROBE":
        readiness = max(score, 58)
    else:
        readiness = score
    return {
        "symbol": symbol,
        "label": "BTC" if symbol.startswith("BTC") else "ETH",
        "price": fnum(nonzero(market.get("price"), lv.get("entry")), 0),
        "action": action,
        "side": str(dec.get("side") or "LONG").upper(),
        "setup": str(dec.get("setup") or "NO_SETUP"),
        "score": score,
        "confidence": clamp(dec.get("confidence")),
        "readiness": readiness,
        "regime": pick(feature, ["regime", "technical.regime", "_row.regime"], "—"),
        "session": pick(feature, ["session", "_row.session"], "—"),
        "dq": fnum(nonzero(pick(feature, ["data_quality.score", "data_quality", "_row.data_quality"])), 0),
        "freshness": freshness(nonzero(pick(feature, ["ts", "_row.ts"]), market.get("ts"))),
        "levels": lv,
        "reasons": reasons(dec, feature),
        "tf": tf_view(feature),
        "derivatives": {
            "funding": der.get("funding"), "open_interest": der.get("open_interest"), "oi_1h": der.get("oi_chg_1h"),
            "long_short": der.get("long_short"), "top_long_short": der.get("top_long_short"), "taker_buy": der.get("taker_buy_ratio"), "basis_bps": der.get("basis_bps"), "freshness": freshness(der.get("ts")),
        },
        "orderflow": {
            "spread_bps": of.get("spread_bps"), "depth_25bps": of.get("depth_25bps"), "imbalance": of.get("imbalance_25bps"), "wall_pressure": of.get("wall_pressure"), "cvd_proxy": of.get("cvd_proxy"), "freshness": freshness(of.get("ts")),
        },
    }


def latest_macro(db, runtime_state: Dict[str, Any]) -> Dict[str, Any]:
    r = latest_row(db, "macro_snapshots")
    p = jloads(r.get("payload"), {}) if r else {}
    if r:
        p.setdefault("risk_score", r.get("risk_score"))
        p.setdefault("mode", r.get("mode"))
        p.setdefault("ts", r.get("ts"))
    if not p:
        p = pick(runtime_state, ["global.macro"], {}) or {}
    inner = p.get("macro") if isinstance(p.get("macro"), dict) else {}
    items = p.get("items") or inner.get("items") or {}

    def first(*vals: Any) -> Any:
        for v in vals:
            if v not in (None, "", [], {}):
                return v
        return None

    def item(sym: str, field: str) -> Any:
        x = items.get(sym, {}) if isinstance(items, dict) else {}
        return x.get(field)

    out = dict(p)
    if isinstance(inner, dict):
        for k, v in inner.items():
            out.setdefault(k, v)
    out["vix"] = first(p.get("vix"), inner.get("vix"), item("^VIX", "price"), item("VIX", "price"))
    out["qqq_chg"] = first(p.get("qqq_chg"), inner.get("qqq_chg"), item("QQQ", "chg"))
    out["spy_chg"] = first(p.get("spy_chg"), inner.get("spy_chg"), item("SPY", "chg"))
    out["dia_chg"] = first(p.get("dia_chg"), inner.get("dia_chg"), item("DIA", "chg"))
    out["dxy_chg"] = first(p.get("dxy_chg"), inner.get("dxy_chg"), item("DX-Y.NYB", "chg"), item("DX=F", "chg"))
    out["us10y_chg"] = first(p.get("us10y_chg"), inner.get("us10y_chg"), item("^TNX", "chg"))
    out["oil_chg"] = first(p.get("oil_chg"), inner.get("oil_chg"), item("CL=F", "chg"))
    out["gold_chg"] = first(p.get("gold_chg"), inner.get("gold_chg"), item("GC=F", "chg"))
    out["fear_greed"] = first(p.get("fear_greed"), inner.get("fear_greed"))
    out["risk_score"] = first(p.get("risk_score"), inner.get("risk_score"), 50)
    out["mode"] = first(p.get("mode"), inner.get("mode"), "UNKNOWN")
    out["freshness"] = freshness(out.get("ts"))
    core = ["vix", "qqq_chg", "spy_chg", "us10y_chg"]
    alt = ["dia_chg", "dxy_chg", "gold_chg", "fear_greed", "oil_chg"]
    has_core = any(out.get(k) not in (None, "") for k in core)
    has_alt = any(out.get(k) not in (None, "") for k in alt)
    out["quality"] = "OK" if has_core else "PARTIAL" if has_alt or bool(p.get("data_ok") or inner.get("data_ok")) else "NO"
    out["missing_core"] = [k for k in core if out.get(k) in (None, "")]
    return out


def latest_news(db, runtime_state: Dict[str, Any]) -> Dict[str, Any]:
    rows = safe_query(db, "SELECT * FROM news_events ORDER BY id DESC LIMIT 8") if table_exists(db, "news_events") else []
    events = []
    for r in rows:
        events.append({"ts": r.get("ts"), "source": r.get("source"), "category": r.get("category"), "severity": fnum(r.get("severity"), 0), "title": r.get("title"), "url": r.get("url")})
    agg = pick(runtime_state, ["global.news"], {}) or {}
    sev = max([fnum(e.get("severity"), 0) for e in events] + [fnum(agg.get("severity"), 0)])
    return {"severity": sev, "bucket": "HIGH" if sev >= 70 else "MEDIUM" if sev >= 35 else "LOW", "events": events, "data_ok": bool(events) or bool(agg)}


def account_state(db, runtime_state: Dict[str, Any]) -> Dict[str, Any]:
    wallet = runtime_state.get("wallet", {}) if isinstance(runtime_state, dict) else {}
    eq = fnum(wallet.get("equity"), CFG.initial_equity)
    open_rows = wallet.get("open", []) if isinstance(wallet.get("open"), list) else []
    if not open_rows and table_exists(db, "positions"):
        open_rows = safe_query(db, "SELECT * FROM positions WHERE status IN ('OPEN','PARTIAL') ORDER BY opened_at DESC LIMIT 20")
    exposure = sum(abs(fnum(nonzero(p.get("size_usd"), pick(jloads(p.get("payload"), {}), ["size_usd", "notional"])), 0)) for p in open_rows if isinstance(p, dict))
    trades = safe_query(db, "SELECT * FROM trades ORDER BY id DESC LIMIT 250") if table_exists(db, "trades") else []
    closed = sum(fnum(t.get("pnl_usd"), 0) for t in trades)
    wins = sum(1 for t in trades if fnum(t.get("pnl_usd"), 0) > 0)
    gross_pos = sum(max(fnum(t.get("pnl_usd"), 0), 0) for t in trades)
    gross_neg = abs(sum(min(fnum(t.get("pnl_usd"), 0), 0) for t in trades))
    return {"equity": eq, "pnl_total": eq - CFG.initial_equity, "pnl_pct": ((eq / CFG.initial_equity) - 1) * 100 if CFG.initial_equity else 0, "open_count": len(open_rows), "exposure": exposure, "exposure_pct": exposure / eq * 100 if eq else 0, "winrate": wins / len(trades) * 100 if trades else 0, "profit_factor": gross_pos / gross_neg if gross_neg else 0, "closed_pnl_sample": closed}


def positions_state(db, runtime_state: Dict[str, Any]) -> List[Dict[str, Any]]:
    wallet = runtime_state.get("wallet", {}) if isinstance(runtime_state, dict) else {}
    rows = wallet.get("open", []) if isinstance(wallet.get("open"), list) else []
    if not rows and table_exists(db, "positions"):
        rows = safe_query(db, "SELECT * FROM positions WHERE status IN ('OPEN','PARTIAL') ORDER BY opened_at DESC LIMIT 20")
    out = []
    for r in rows[:20]:
        if not isinstance(r, dict):
            continue
        p = jloads(r.get("payload"), {})
        out.append({
            "symbol": nonzero(pick(p, ["symbol"]), r.get("symbol")),
            "side": str(nonzero(pick(p, ["side"]), r.get("side")) or "").upper(),
            "setup": nonzero(pick(p, ["setup"]), r.get("setup")),
            "entry": nonzero(pick(p, ["entry", "entry_price", "execution.entry", "levels.entry", "open.entry"]), r.get("entry")),
            "stop": nonzero(pick(p, ["stop", "sl", "stop_loss", "levels.sl", "levels.stop", "risk.stop_loss"]), r.get("stop") if "stop" in r else None),
            "tp": nonzero(pick(p, ["tp", "tp1", "take_profit", "take_profit_1", "levels.tp1", "targets.tp1"]), r.get("tp") if "tp" in r else None),
            "size_usd": nonzero(pick(p, ["size_usd", "notional", "execution.size_usd"]), r.get("size_usd")),
            "pnl_usd": nonzero(pick(p, ["pnl_usd", "unrealized_pnl", "pnl"]), r.get("pnl_usd")),
            "status": nonzero(pick(p, ["status"]), r.get("status")),
        })
    return out


def trades_state(db) -> List[Dict[str, Any]]:
    return safe_query(db, "SELECT * FROM trades ORDER BY id DESC LIMIT 20") if table_exists(db, "trades") else []


def edge_state(db) -> List[Dict[str, Any]]:
    if not table_exists(db, "edge_memory"):
        return []
    rows = safe_query(db, "SELECT * FROM edge_memory ORDER BY n DESC, updated_at DESC LIMIT 12")
    out = []
    for r in rows:
        n = fnum(r.get("n"), 0); wins = fnum(r.get("wins"), 0); pos = fnum(r.get("sum_pos_r"), 0); neg = abs(fnum(r.get("sum_neg_r"), 0)); sr = fnum(r.get("sum_r"), 0)
        out.append({"key": r.get("key"), "n": n, "wr": wins / n * 100 if n else 0, "avg_r": sr / n if n else 0, "pf": pos / neg if neg else 0})
    return out


def forward_state(db) -> Dict[str, Any]:
    recent = safe_query(db, "SELECT * FROM forward_results ORDER BY id DESC LIMIT 30") if table_exists(db, "forward_results") else []
    return {"cases": count_table(db, "forward_cases"), "results": count_table(db, "forward_results"), "avg_r": sum(fnum(r.get("result_r"), 0) for r in recent) / len(recent) if recent else 0}


def errors_state(db, runtime_state: Dict[str, Any]) -> Dict[str, Any]:
    ignore = ("BrokenPipeError", "ConnectionResetError", "GET /state", "GET /favicon", "favicon", "nohup: ignoring input")
    categories: Dict[str, int] = {}
    examples: List[str] = []

    def add(cat: str, ex: str) -> None:
        categories[cat] = categories.get(cat, 0) + 1
        if len(examples) < 5 and ex not in examples:
            examples.append(ex[:220])

    for name in ["runner_errors.log", "dashboard_errors.log", "runner.log"]:
        for line in tail_lines(LOG_DIR / name, 160):
            if any(x in line for x in ignore):
                continue
            low = line.lower()
            if "timeout" in low or "timed out" in low:
                add("NETWORK_TIMEOUT", "Timeout API/xarxa. El bot continua si les taules segueixen creixent.")
            elif "traceback" in low:
                add("TRACEBACK", line)
            elif "error" in low or "exception" in low:
                add("LOG_ERROR", line)
    if table_exists(db, "runtime_events"):
        for e in safe_query(db, "SELECT * FROM runtime_events WHERE level IN ('ERROR','CRITICAL','WARN') ORDER BY id DESC LIMIT 20"):
            msg = str(e.get("message") or "runtime event")
            if "Timeout" in msg or "timed out" in msg:
                add("NETWORK_TIMEOUT", "Timeout registrat a runtime_events.")
            else:
                add(f"RUNTIME_{e.get('level')}", msg)
    return {"count": sum(categories.values()), "summary": categories, "examples": examples}


def build_state() -> Dict[str, Any]:
    db = get_db()
    runtime_state = read_json(STATE_PATH, {})
    decisions = latest_decisions(db)
    symbols = [symbol_state(db, s, decisions) for s in SYMBOLS]
    best = sorted(symbols, key=lambda x: (1 if x["action"] == "OPEN" else 0.65 if x["action"] == "PROBE" else 0, x["score"]), reverse=True)[0]
    macro = latest_macro(db, runtime_state)
    news = latest_news(db, runtime_state)
    counts = {t: count_table(db, t) for t in ["market_snapshots", "derivatives_snapshots", "orderflow_snapshots", "macro_snapshots", "news_events", "features", "decisions", "positions", "trades", "edge_memory", "forward_cases", "forward_results", "runtime_events"]}
    ips = get_ips()
    health = {
        "market": "OK" if counts["market_snapshots"] and symbols[0]["price"] else "BAD",
        "derivatives": "OK" if counts["derivatives_snapshots"] else "BAD",
        "orderflow": "OK" if counts["orderflow_snapshots"] else "BAD",
        "macro": macro["quality"],
        "news": "OK" if counts["news_events"] else "OFF",
    }
    warnings: List[str] = []
    if macro["quality"] == "PARTIAL":
        warnings.append("MACRO parcial: hi ha dades reals, però falten VIX/QQQ/SPY/US10Y.")
    if macro["quality"] == "NO":
        warnings.append("MACRO sense dades útils.")
    if counts["news_events"] == 0:
        warnings.append("NEWS OFF: no hi ha feed de notícies actiu.")
    if errors_state(db, runtime_state)["summary"].get("NETWORK_TIMEOUT"):
        warnings.append("Timeouts de xarxa puntuals detectats.")
    return {
        "meta": {"ts": now_iso(), "server": SERVER_NAME, "refresh_ms": REFRESH_MS, "port": CFG.dashboard_port, "db_path": str(CFG.db_path), "ips": ips, "lan_urls": [f"http://{ip}:{CFG.dashboard_port}" for ip in ips["lan"]], "tail_urls": [f"http://{ip}:{CFG.dashboard_port}" for ip in ips["tailscale"]]},
        "account": account_state(db, runtime_state),
        "symbols": symbols,
        "best": best,
        "macro": macro,
        "news": news,
        "positions": positions_state(db, runtime_state),
        "decisions": decisions[:14],
        "trades": trades_state(db),
        "edge": edge_state(db),
        "forward": forward_state(db),
        "errors": errors_state(db, runtime_state),
        "counts": counts,
        "health": health,
        "warnings": warnings,
    }


HTML = r'''<!doctype html><html lang="ca"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover"><meta name="theme-color" content="#050914"><title>JoanBot V18 Command Center</title><style>
:root{--bg:#050914;--card:#0d1728;--card2:#111e33;--line:#243858;--text:#edf6ff;--muted:#8191aa;--green:#2ee98b;--red:#ff4d6d;--yellow:#ffd166;--blue:#38bdf8;--violet:#b88cff}*{box-sizing:border-box}html,body{margin:0;background:radial-gradient(circle at 50% -10%,#132340 0,#050914 45%,#03060d 100%);color:var(--text);font-family:Inter,Roboto,Arial,sans-serif}body{max-width:820px;margin:0 auto;padding:10px 10px 16px}.top{position:sticky;top:0;z-index:20;background:rgba(5,9,20,.94);backdrop-filter:blur(12px);border-bottom:1px solid #17243a;padding:8px 0 10px}.title{display:flex;justify-content:space-between;align-items:flex-start;gap:8px}.title h1{font-size:16px;margin:0;font-weight:950;letter-spacing:.2px}.sub{font-size:10px;color:var(--muted);margin-top:3px}.pills{display:flex;gap:5px;flex-wrap:wrap;justify-content:flex-end}.pill{font-size:9px;font-weight:900;padding:4px 7px;border-radius:99px;background:#10192b;border:1px solid #2b3d5d;color:#c4d7f0}.good{color:var(--green)!important}.warn{color:var(--yellow)!important}.bad{color:var(--red)!important}.blue{color:var(--blue)!important}.pill.good{border-color:#1d704e}.pill.warn{border-color:#7a6222}.pill.bad{border-color:#7a2b44}.nav{display:grid;grid-template-columns:repeat(4,1fr);gap:7px;margin-top:9px}.nav button{border:1px solid #243858;background:#0c1424;color:#b7c5d8;border-radius:13px;padding:9px 4px;font-weight:950;font-size:11px}.nav button.active{background:#173b68;color:#fff;border-color:#3b86d6}.screen{display:none}.screen.active{display:grid;gap:10px}.card{background:linear-gradient(180deg,rgba(16,29,50,.98),rgba(8,16,30,.98));border:1px solid var(--line);border-radius:17px;padding:12px;box-shadow:0 14px 32px rgba(0,0,0,.28);overflow:hidden}.card h2{font-size:12px;text-transform:uppercase;color:#cfe3ff;margin:0 0 10px}.row{display:flex;justify-content:space-between;gap:8px;align-items:center}.grid2{display:grid;grid-template-columns:1fr 1fr;gap:8px}.metric{font-size:32px;line-height:1;font-weight:950;letter-spacing:-1px}.small{font-size:11px}.tiny{font-size:10px}.muted{color:var(--muted)}.kv{display:grid;grid-template-columns:1fr auto;gap:8px;padding:6px 0;border-bottom:1px solid rgba(255,255,255,.055);font-size:12px}.kv:last-child{border-bottom:0}.badge{display:inline-flex;border-radius:9px;padding:4px 8px;background:#13233a;border:1px solid #304767;font-size:10px;font-weight:950}.badge.wait{background:#2c1e3a;color:#e4c0ff;border-color:#63447f}.badge.open{background:#0b3a29;color:#49ffa3;border-color:#188757}.badge.probe{background:#3d2a08;color:#ffd166;border-color:#8c651e}.badge.short{background:#3c1624;color:#ff7891;border-color:#83304c}.badge.long{background:#0d3a29;color:#48ffa2;border-color:#198857}.gauge{width:96px;height:96px;border-radius:50%;background:conic-gradient(var(--green) 0deg,var(--yellow) 0deg,var(--red) 0deg,#1d2b43 0deg);display:grid;place-items:center;position:relative;flex:0 0 auto}.gauge:after{content:"";position:absolute;inset:12px;border-radius:50%;background:#0b1526}.gauge b{position:relative;z-index:1;font-size:26px}.gauge span{position:relative;z-index:1;font-size:9px;color:var(--muted);display:block;text-align:center}.score{height:9px;border-radius:99px;background:#17263e;overflow:hidden}.score i{display:block;height:100%;background:linear-gradient(90deg,var(--red),var(--yellow),var(--green));width:0}.levels{display:grid;grid-template-columns:repeat(2,1fr);gap:7px;margin-top:8px}.box{background:#0a1323;border:1px solid #213555;border-radius:12px;padding:8px;min-width:0}.box .lab{font-size:9px;color:#8191aa;font-weight:900;text-transform:uppercase}.box .val{font-size:16px;font-weight:950;margin-top:2px}.reasons{display:grid;gap:5px;margin-top:8px}.reason{background:#0b1425;border:1px solid #1f314d;border-radius:10px;padding:7px;font-size:11px}.tf{display:grid;grid-template-columns:repeat(4,1fr);gap:6px;margin-top:8px}.tf .box{text-align:center}.macrogrid{display:grid;grid-template-columns:1fr 1fr;gap:7px}.okbox,.warnbox,.badbox{border-radius:12px;padding:9px;border:1px solid;font-size:12px}.okbox{background:#0d3325;border-color:#1e6e4d}.warnbox{background:#35280b;border-color:#7a601d}.badbox{background:#361622;border-color:#7a2d45}.table{width:100%;border-collapse:collapse;font-size:12px}.table th{text-align:left;color:#8191aa;font-size:10px;text-transform:uppercase;padding:7px 4px}.table td{padding:8px 4px;border-top:1px solid rgba(255,255,255,.055);vertical-align:top}.nowrap{white-space:nowrap}.symbols{display:grid;grid-template-columns:1fr;gap:10px}@media(min-width:1000px){body{max-width:1100px}.symbols{grid-template-columns:1fr 1fr}.wide2{display:grid;grid-template-columns:1fr 1fr;gap:10px}}@media(max-width:520px){body{padding:8px}.metric{font-size:28px}.title h1{font-size:14px}.levels{grid-template-columns:1fr 1fr}.tf{grid-template-columns:repeat(2,1fr)}}
</style></head><body><div class="top"><div class="title"><div><h1>⚡ JOANBOT V18 — MOBILE COMMAND CENTER</h1><div class="sub" id="sub">carregant...</div></div><div class="pills"><span class="pill" id="pData">DATA</span><span class="pill" id="pMacro">MACRO</span><span class="pill" id="pErr">ERR</span></div></div><div class="nav"><button data-tab="desk" class="active">DESK</button><button data-tab="market">MERCAT</button><button data-tab="trades">TRADES</button><button data-tab="access">ACCÉS</button></div></div><main><section id="desk" class="screen active"></section><section id="market" class="screen"></section><section id="trades" class="screen"></section><section id="access" class="screen"></section></main><script>
const $=id=>document.getElementById(id); let STATE=null; const R=3000;
function esc(x){return String(x??'—').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[m]))} function n(x,d=2){let v=Number(x);return Number.isFinite(v)?v.toLocaleString('en-US',{minimumFractionDigits:d,maximumFractionDigits:d}):'—'} function usd(x,d=2){let v=Number(x);return Number.isFinite(v)&&Math.abs(v)>1e-12?'$'+n(v,d):'—'} function pct(x,d=2){let v=Number(x);return Number.isFinite(v)?(v>0?'+':'')+n(v,d)+'%':'—'} function cls(v){let x=Number(v);return !Number.isFinite(x)?'':x>0?'good':x<0?'bad':''} function badge(t,c){return `<span class="badge ${c||''}">${esc(t)}</span>`} function bAction(a){a=String(a||'').toLowerCase();return a.includes('open')?'open':a.includes('probe')?'probe':'wait'} function bSide(s){s=String(s||'').toLowerCase();return s.includes('short')?'short':'long'} function kv(k,v,c=''){return `<div class="kv"><span class="muted">${esc(k)}</span><b class="${c}">${v}</b></div>`} function score(v){v=Math.max(0,Math.min(100,Number(v)||0));return `<div class="score"><i style="width:${v}%"></i></div>`} function gauge(v){v=Math.max(0,Math.min(100,Number(v)||0));let deg=v*3.6;return `<div class="gauge" style="background:conic-gradient(var(--green) 0deg ${deg*.55}deg,var(--yellow) ${deg*.55}deg ${deg*.82}deg,var(--red) ${deg*.82}deg ${deg}deg,#1d2b43 ${deg}deg)"><div><b>${n(v,0)}</b><span>READINESS</span></div></div>`}
function levelBoxes(l){return `<div class="levels"><div class="box"><div class="lab">Entry</div><div class="val">${usd(l.entry,2)}</div></div><div class="box"><div class="lab">Stop</div><div class="val bad">${usd(l.sl,2)}</div></div><div class="box"><div class="lab">TP1</div><div class="val good">${usd(l.tp1,2)}</div></div><div class="box"><div class="lab">TP2</div><div class="val good">${usd(l.tp2,2)}</div></div><div class="box"><div class="lab">TP3</div><div class="val good">${usd(l.tp3,2)}</div></div><div class="box"><div class="lab">R:R</div><div class="val good">${n(l.rr,2)}</div></div></div><div class="tiny muted" style="margin-top:5px">Nivells: ${esc(l.source)}</div>`}
function symbolCard(s){let l=s.levels||{};return `<div class="card"><div class="row"><div><div class="metric">${esc(s.label)}</div><div class="tiny muted">${esc(s.regime)} · ${esc(s.session)} · DQ ${n(s.dq,0)} · ${esc(s.freshness?.label)}</div></div><div>${badge(s.action,bAction(s.action))} ${badge(s.side,bSide(s.side))}</div></div><div class="row" style="margin-top:10px;align-items:center"><div>${gauge(s.readiness)}</div><div style="flex:1">${kv('Preu',usd(s.price,2))}${kv('Setup',esc(s.setup))}${kv('Score',`${n(s.score,0)}/100`)}${score(s.score)}</div></div>${levelBoxes(l)}<h2 style="margin-top:10px">Per què?</h2><div class="reasons">${(s.reasons||[]).map(x=>`<div class="reason">• ${esc(x)}</div>`).join('')}</div><h2 style="margin-top:10px">Multi-timeframe</h2><div class="tf">${(s.tf||[]).map(t=>`<div class="box"><b>${esc(t.tf)}</b><div class="tiny muted">${esc(t.state)}</div><div class="tiny">RSI ${n(t.rsi,0)}</div></div>`).join('')}</div></div>`}
function bestCard(b){let verdict=String(b.action).toUpperCase()==='OPEN'?'OPEN POSSIBLE':String(b.action).toUpperCase()==='PROBE'?'PROBE PETIT':'WAIT — NO TOCAR TRADE';return `<div class="card"><h2>🎯 MILLOR IDEA ARA</h2><div class="row"><div>${gauge(b.readiness)}</div><div style="flex:1"><div class="metric ${String(b.side).toUpperCase()==='SHORT'?'bad':'good'}">${esc(b.symbol)} ${esc(b.side)}</div><div>${badge(b.action,bAction(b.action))} ${badge(b.setup)}</div><div style="margin-top:8px">${score(b.score)}</div><div class="small" style="margin-top:6px"><b>Veredicte:</b> <span class="${b.action==='WAIT'?'bad':'good'}">${esc(verdict)}</span></div></div></div>${levelBoxes(b.levels||{})}<div class="reasons">${(b.reasons||[]).map(x=>`<div class="reason">• ${esc(x)}</div>`).join('')}</div></div>`}
function macroCard(m,nw){let q=m.quality;let warn=q==='OK'?'okbox':q==='PARTIAL'?'warnbox':'badbox';return `<div class="card"><h2>🌍 MACRO / RISC EXTERN</h2><div class="${warn}">${q==='OK'?'Macro OK':q==='PARTIAL'?'Macro parcial: dades reals disponibles, però falten VIX/QQQ/SPY/US10Y.':'Macro sense dades útils.'}</div><div class="macrogrid" style="margin-top:8px"><div class="box"><div class="lab">VIX</div><div class="val">${m.vix==null?'—':n(m.vix,2)}</div></div><div class="box"><div class="lab">QQQ</div><div class="val ${cls(m.qqq_chg)}">${pct(m.qqq_chg)}</div></div><div class="box"><div class="lab">SPY</div><div class="val ${cls(m.spy_chg)}">${pct(m.spy_chg)}</div></div><div class="box"><div class="lab">DXY</div><div class="val ${cls(m.dxy_chg)}">${pct(m.dxy_chg)}</div></div><div class="box"><div class="lab">Gold</div><div class="val ${cls(m.gold_chg)}">${pct(m.gold_chg)}</div></div><div class="box"><div class="lab">Fear&Greed</div><div class="val ${Number(m.fear_greed)<=25?'bad':Number(m.fear_greed)>=75?'warn':'good'}">${m.fear_greed==null?'—':n(m.fear_greed,0)}</div></div></div>${kv('Macro risk',n(m.risk_score,0)+'/100')}${kv('Mode',esc(m.mode))}${kv('Freshness',esc(m.freshness?.label))}</div><div class="card"><h2>📰 NEWS / EVENT RISK</h2><div class="metric ${Number(nw.severity)>=70?'bad':Number(nw.severity)>=35?'warn':'good'}">${n(nw.severity,0)}<span class="small">/100</span></div>${score(nw.severity)}<div class="small muted" style="margin-top:8px">${nw.data_ok?'Feed actiu':'News feed OFF / sense events registrats'}</div>${(nw.events||[]).slice(0,5).map(e=>`<div class="reason">${esc(e.category||e.source)} · ${n(e.severity,0)} · ${esc(e.title||'')}</div>`).join('')||'<div class="okbox">Sense notícia crítica registrada.</div>'}</div>`}
function derivCard(s){let d=s.derivatives||{},o=s.orderflow||{};return `<div class="card"><h2>${esc(s.label)} · DERIVATS / ORDERFLOW</h2>${kv('Funding',pct((Number(d.funding)||0)*100,4),cls(d.funding))}${kv('Open Interest',n(d.open_interest,0))}${kv('OI 1h',pct(d.oi_1h),cls(d.oi_1h))}${kv('Long/Short',n(d.long_short,2))}${kv('Top L/S',n(d.top_long_short,2))}${kv('Taker buy',n(d.taker_buy,2))}${kv('Spread bps',n(o.spread_bps,2))}${kv('Depth 25bps',n(o.depth_25bps,0))}${kv('Imbalance',n(o.imbalance,2),cls(o.imbalance))}<div class="tiny muted">Derivats ${esc(d.freshness?.label)} · Orderflow ${esc(o.freshness?.label)}</div></div>`}
function table(rows,cols){if(!rows||!rows.length)return '<div class="warnbox">Sense dades.</div>';return `<table class="table"><thead><tr>${cols.map(c=>`<th>${esc(c[0])}</th>`).join('')}</tr></thead><tbody>${rows.map(r=>`<tr>${cols.map(c=>`<td class="${c[2]||''}">${typeof c[1]==='function'?c[1](r):esc(r[c[1]])}</td>`).join('')}</tr>`).join('')}</tbody></table>`}
function render(s){STATE=s;$('sub').innerText=`${new Date(s.meta.ts).toLocaleTimeString()} · auto ${Math.round(s.meta.refresh_ms/1000)}s · ${s.meta.db_path}`;let h=s.health||{};$('pData').className='pill '+(h.market==='OK'&&h.derivatives==='OK'?'good':'bad');$('pData').innerText=`DATA ${h.market}/${h.derivatives}`;$('pMacro').className='pill '+(h.macro==='OK'?'good':h.macro==='PARTIAL'?'warn':'bad');$('pMacro').innerText='MACRO '+h.macro;$('pErr').className='pill '+(s.errors.count?'bad':'good');$('pErr').innerText='ERR '+s.errors.count;let a=s.account||{},sy=s.symbols||[],best=s.best||sy[0]||{};$('desk').innerHTML=`<div class="card"><h2>📌 RESUM EXECUTIU</h2><div class="grid2"><div><div class="metric">${usd(a.equity,0)}</div><div class="small ${cls(a.pnl_total)}">PnL ${usd(a.pnl_total,0)} · ${pct(a.pnl_pct)}</div></div><div>${kv('Obertes',a.open_count)}${kv('Exposició',usd(a.exposure,0)+' / '+n(a.exposure_pct,2)+'%')}${kv('PF / WR',n(a.profit_factor,2)+' / '+n(a.winrate,0)+'%')}</div></div></div>${bestCard(best)}<div class="symbols">${sy.map(symbolCard).join('')}</div>`;$('market').innerHTML=`${macroCard(s.macro||{},s.news||{})}<div class="symbols">${sy.map(derivCard).join('')}</div><div class="card"><h2>⚠️ WARNINGS</h2>${(s.warnings||[]).map(x=>`<div class="warnbox">${esc(x)}</div>`).join('')||'<div class="okbox">Dades principals correctes.</div>'}</div>`;$('trades').innerHTML=`<div class="card"><h2>📈 POSICIONS OBERTES</h2>${table(s.positions,[['Sym','symbol'],['Side',r=>badge(r.side,bSide(r.side))],['Entry',r=>usd(r.entry,2)],['Stop',r=>usd(r.stop,2),'bad'],['TP',r=>usd(r.tp,2),'good'],['PnL',r=>usd(r.pnl_usd,2)]])}</div><div class="card"><h2>🧠 DECISIONS RECENTS</h2>${table(s.decisions,[['Act',r=>badge(r.action,bAction(r.action))],['Sym','symbol'],['Side','side'],['Score',r=>n(r.final_score||r.score,0)],['Setup','setup']])}</div><div class="card"><h2>✅ TRADES TANCATS</h2>${table(s.trades,[['Sym','symbol'],['Side',r=>badge(r.side,bSide(r.side))],['Setup','setup'],['PnL',r=>usd(r.pnl_usd,2)],['R',r=>n(r.pnl_r,2)]])}</div>`;$('access').innerHTML=`<div class="card"><h2>📡 ACCÉS</h2>${(s.meta.lan_urls||[]).map(u=>`<div class="okbox">Mateix Wi‑Fi: ${esc(u)}</div>`).join('')||'<div class="warnbox">No detecto IP LAN. Usa: ip route get 8.8.8.8</div>'}${(s.meta.tail_urls||[]).map(u=>`<div class="okbox">Fora de casa amb Tailscale: ${esc(u)}</div>`).join('')}<div class="warnbox">Fora de casa sense Tailscale/Cloudflare: NO és segur ni funcional. Cal VPN/túnel.</div></div><div class="card"><h2>🧾 ERRORS REALS</h2>${Object.keys(s.errors.summary||{}).length?Object.entries(s.errors.summary).map(([k,v])=>kv(k,v,'bad')).join('')+(s.errors.examples||[]).map(x=>`<div class="reason">${esc(x)}</div>`).join(''):'<div class="okbox">Sense errors reals filtrats.</div>'}</div><div class="card"><h2>🧬 EDGE MEMORY</h2>${table(s.edge,[['Key',r=>esc(r.key).slice(0,28)],['N',r=>n(r.n,0)],['WR',r=>n(r.wr,0)+'%'],['AvgR',r=>n(r.avg_r,3)],['PF',r=>n(r.pf,2)]])}</div><div class="card"><h2>🔎 FORWARD TEST</h2>${kv('Cases',s.forward.cases)}${kv('Results',s.forward.results)}${kv('Avg R',n(s.forward.avg_r,3),cls(s.forward.avg_r))}</div><div class="card"><h2>DB COUNTS</h2>${Object.entries(s.counts||{}).map(([k,v])=>kv(k,v)).join('')}</div>`}
async function load(){try{let r=await fetch('/state?t='+Date.now(),{cache:'no-store'});render(await r.json())}catch(e){$('sub').innerText='ERROR /state '+e;$('pErr').className='pill bad'}}setInterval(load,R);load();document.querySelectorAll('.nav button').forEach(b=>b.onclick=()=>{document.querySelectorAll('.nav button').forEach(x=>x.classList.toggle('active',x===b));document.querySelectorAll('.screen').forEach(x=>x.classList.toggle('active',x.id===b.dataset.tab));window.scrollTo(0,0)});
</script></body></html>'''


class Handler(BaseHTTPRequestHandler):
    server_version = SERVER_NAME

    def log_message(self, fmt: str, *args: Any) -> None:
        return

    def _send(self, code: int, body: bytes, ctype: str = "text/html; charset=utf-8") -> None:
        try:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
            self.send_header("Pragma", "no-cache")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            return

    def do_HEAD(self) -> None:
        self._send(200, b"")

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        try:
            if path in ("/state", "/api/state"):
                body = json.dumps(build_state(), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                return self._send(200, body, "application/json; charset=utf-8")
            if path == "/health":
                return self._send(200, b"OK", "text/plain; charset=utf-8")
            if path == "/favicon.ico":
                return self._send(204, b"", "image/x-icon")
            return self._send(200, HTML.encode("utf-8"))
        except Exception as e:
            err = {"error": repr(e), "trace": traceback.format_exc(limit=8)}
            return self._send(500, json.dumps(err, ensure_ascii=False, indent=2).encode("utf-8"), "application/json; charset=utf-8")


def main() -> None:
    host = "0.0.0.0"
    port = int(CFG.dashboard_port)
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"{SERVER_NAME} listening on http://{host}:{port}", flush=True)
    ips = get_ips()
    for ip in ips["lan"]:
        print(f"LAN/iPhone same Wi-Fi: http://{ip}:{port}", flush=True)
    for ip in ips["tailscale"]:
        print(f"Tailscale remote: http://{ip}:{port}", flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    main()

