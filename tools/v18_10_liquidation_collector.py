#!/usr/bin/env python3
from __future__ import annotations

import json, os, sqlite3, ssl, threading, time, traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import websocket

DB = Path("data/joanbot_v14.sqlite")
OUT = Path("data/v18_10_liquidations")

VERSION = "V18.10.1_RELIABLE_BINANCE_LIQUIDATION_COLLECTOR"

EVENTS = "institutional_liquidation_events_v18_10"
LATEST = "institutional_liquidation_rollup_latest_v18_10"
HEALTH = "institutional_liquidation_collector_health_v18_10"

# Stream específic BTC/ETH. Més net que !forceOrder@arr.
URLS = [
    "wss://fstream.binance.com/stream?streams=btcusdt@forceOrder/ethusdt@forceOrder",
    "wss://fstream.binance.com/ws/!forceOrder@arr",
]

SYMBOLS = {"BTCUSDT", "ETHUSDT"}

STATE = {
    "connected": False,
    "url": None,
    "opened_ts": None,
    "last_msg_ts": None,
    "messages": 0,
    "accepted": 0,
    "errors": 0,
    "last_error": None,
    "last_heartbeat": None,
}

LOCK = threading.Lock()


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def iso_from_ms(ms):
    try:
        return datetime.fromtimestamp(float(ms) / 1000.0, tz=timezone.utc).isoformat()
    except Exception:
        return None


def age_min(ts):
    if not ts:
        return None
    try:
        d = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - d.astimezone(timezone.utc)).total_seconds() / 60)
    except Exception:
        return None


def fnum(x, default=0.0):
    try:
        if x is None or x == "":
            return default
        return float(str(x).replace(",", ""))
    except Exception:
        return default


def qid(x):
    return '"' + x.replace('"', '""') + '"'


def connect():
    con = sqlite3.connect(DB, timeout=60, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=60000")
    try:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=NORMAL")
    except Exception:
        pass
    return con


def create_tables(con):
    con.execute(f"""
    CREATE TABLE IF NOT EXISTS {qid(EVENTS)} (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL,
        event_time_ms INTEGER,
        trade_time_ms INTEGER,
        event_ts TEXT,
        trade_ts TEXT,
        symbol TEXT NOT NULL,
        force_side TEXT,
        liquidation_type TEXT,
        price REAL,
        avg_price REAL,
        qty REAL,
        last_qty REAL,
        notional_usd REAL,
        order_status TEXT,
        source TEXT,
        payload TEXT,
        UNIQUE(event_time_ms, trade_time_ms, symbol, force_side, price, qty)
    )
    """)

    con.execute(f"""
    CREATE TABLE IF NOT EXISTS {qid(LATEST)} (
        symbol TEXT PRIMARY KEY,
        ts TEXT NOT NULL,
        connection_state TEXT,
        last_event_ts TEXT,
        last_msg_age_min REAL,
        events_5m INTEGER,
        events_15m INTEGER,
        events_60m INTEGER,
        total_5m_usd REAL,
        total_15m_usd REAL,
        total_60m_usd REAL,
        long_liq_15m_usd REAL,
        short_liq_15m_usd REAL,
        long_liq_60m_usd REAL,
        short_liq_60m_usd REAL,
        largest_60m_usd REAL,
        source TEXT,
        payload TEXT
    )
    """)

    con.execute(f"""
    CREATE TABLE IF NOT EXISTS {qid(HEALTH)} (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL,
        version TEXT NOT NULL,
        connection_state TEXT,
        messages INTEGER,
        accepted INTEGER,
        errors INTEGER,
        last_msg_ts TEXT,
        payload TEXT
    )
    """)


def write_event(obj: Dict[str, Any]):
    # Combined stream: {"stream": "...", "data": {...}}
    if "data" in obj and isinstance(obj.get("data"), dict):
        obj = obj["data"]

    o = obj.get("o") or {}
    sym = o.get("s")

    if sym not in SYMBOLS:
        return

    event_ms = obj.get("E")
    trade_ms = o.get("T")
    side = o.get("S")

    price = fnum(o.get("p"))
    avg_price = fnum(o.get("ap"))
    qty = fnum(o.get("q"))
    last_qty = fnum(o.get("l"))
    notional = (avg_price or price) * qty

    # SELL forced order = long liquidation. BUY forced order = short liquidation.
    liq_type = "LONG_LIQ" if side == "SELL" else "SHORT_LIQ" if side == "BUY" else "UNKNOWN"

    con = connect()
    create_tables(con)

    con.execute(f"""
    INSERT OR IGNORE INTO {qid(EVENTS)}
    (ts, event_time_ms, trade_time_ms, event_ts, trade_ts, symbol, force_side,
     liquidation_type, price, avg_price, qty, last_qty, notional_usd,
     order_status, source, payload)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        now_iso(), event_ms, trade_ms, iso_from_ms(event_ms), iso_from_ms(trade_ms),
        sym, side, liq_type, price, avg_price, qty, last_qty, notional,
        o.get("X"), "binance_usdm_forceOrder_stream", json.dumps(obj, sort_keys=True)
    ))

    con.close()

    with LOCK:
        STATE["accepted"] += 1


def window_rollup(con, sym, minutes):
    cutoff = datetime.fromtimestamp(datetime.now(timezone.utc).timestamp() - minutes * 60, tz=timezone.utc).isoformat()
    r = con.execute(f"""
    SELECT
        COUNT(*) n,
        COALESCE(SUM(notional_usd),0) total,
        COALESCE(SUM(CASE WHEN liquidation_type='LONG_LIQ' THEN notional_usd ELSE 0 END),0) long_liq,
        COALESCE(SUM(CASE WHEN liquidation_type='SHORT_LIQ' THEN notional_usd ELSE 0 END),0) short_liq,
        COALESCE(MAX(notional_usd),0) largest,
        MAX(trade_ts) last_event_ts
    FROM {qid(EVENTS)}
    WHERE symbol=? AND trade_ts >= ?
    """, (sym, cutoff)).fetchone()
    return dict(r)


def heartbeat_once():
    con = connect()
    create_tables(con)

    with LOCK:
        st = dict(STATE)

    opened_age = age_min(st.get("opened_ts"))
    msg_age = age_min(st.get("last_msg_ts"))
    connection_state = "LIVE_CONNECTED" if st.get("connected") else "DISCONNECTED"

    for sym in SYMBOLS:
        w5 = window_rollup(con, sym, 5)
        w15 = window_rollup(con, sym, 15)
        w60 = window_rollup(con, sym, 60)

        payload = {
            "version": VERSION,
            "state": st,
            "opened_age_min": opened_age,
            "note": "If connected and no forceOrder messages exist, total liquidation flow is valid 0.0 for the window.",
        }

        con.execute(f"""
        INSERT OR REPLACE INTO {qid(LATEST)}
        (symbol, ts, connection_state, last_event_ts, last_msg_age_min,
         events_5m, events_15m, events_60m,
         total_5m_usd, total_15m_usd, total_60m_usd,
         long_liq_15m_usd, short_liq_15m_usd,
         long_liq_60m_usd, short_liq_60m_usd,
         largest_60m_usd, source, payload)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            sym, now_iso(), connection_state, w60.get("last_event_ts"), msg_age,
            int(w5["n"]), int(w15["n"]), int(w60["n"]),
            float(w5["total"]), float(w15["total"]), float(w60["total"]),
            float(w15["long_liq"]), float(w15["short_liq"]),
            float(w60["long_liq"]), float(w60["short_liq"]),
            float(w60["largest"]),
            "binance_usdm_forceOrder_stream",
            json.dumps(payload, sort_keys=True),
        ))

    con.execute(f"""
    INSERT INTO {qid(HEALTH)}
    (ts, version, connection_state, messages, accepted, errors, last_msg_ts, payload)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        now_iso(), VERSION, connection_state, int(st["messages"]), int(st["accepted"]),
        int(st["errors"]), st.get("last_msg_ts"), json.dumps(st, sort_keys=True)
    ))

    con.close()


def heartbeat_loop():
    OUT.mkdir(parents=True, exist_ok=True)

    while True:
        try:
            with LOCK:
                STATE["last_heartbeat"] = now_iso()
            heartbeat_once()
            (OUT / "collector_state.json").write_text(json.dumps(dict(STATE), indent=2, sort_keys=True))
        except Exception as e:
            with LOCK:
                STATE["errors"] += 1
                STATE["last_error"] = "heartbeat:" + repr(e)
        time.sleep(15)


def on_open(ws):
    with LOCK:
        STATE["connected"] = True
        STATE["opened_ts"] = now_iso()
        STATE["last_error"] = None


def on_close(ws, code, msg):
    with LOCK:
        STATE["connected"] = False
        STATE["last_error"] = f"closed code={code} msg={msg}"


def on_error(ws, error):
    with LOCK:
        STATE["connected"] = False
        STATE["errors"] += 1
        STATE["last_error"] = repr(error)


def on_message(ws, message):
    with LOCK:
        STATE["messages"] += 1
        STATE["last_msg_ts"] = now_iso()

    try:
        data = json.loads(message)
        if isinstance(data, list):
            for x in data:
                if isinstance(x, dict):
                    write_event(x)
        elif isinstance(data, dict):
            write_event(data)
    except Exception as e:
        with LOCK:
            STATE["errors"] += 1
            STATE["last_error"] = "message:" + repr(e)


def main():
    if not DB.exists():
        raise SystemExit("DB_MISSING")

    os.environ.pop("http_proxy", None)
    os.environ.pop("https_proxy", None)
    os.environ.pop("HTTP_PROXY", None)
    os.environ.pop("HTTPS_PROXY", None)
    os.environ["NO_PROXY"] = "*"

    websocket.setdefaulttimeout(20)

    con = connect()
    create_tables(con)
    con.close()

    threading.Thread(target=heartbeat_loop, daemon=True).start()

    while True:
        for url in URLS:
            with LOCK:
                STATE["url"] = url
                STATE["connected"] = False
                STATE["last_error"] = None

            try:
                ws = websocket.WebSocketApp(
                    url,
                    on_open=on_open,
                    on_message=on_message,
                    on_error=on_error,
                    on_close=on_close,
                )
                ws.run_forever(
                    ping_interval=20,
                    ping_timeout=10,
                    sslopt={"cert_reqs": ssl.CERT_REQUIRED},
                    skip_utf8_validation=True,
                )
            except Exception:
                with LOCK:
                    STATE["connected"] = False
                    STATE["errors"] += 1
                    STATE["last_error"] = traceback.format_exc()[-500:]

            time.sleep(5)


if __name__ == "__main__":
    raise SystemExit(main())
