from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

DB = "data/joanbot_v14.sqlite"
VERSION = "LIQUIDATION_STREAM_V16_2_BTC_ETH_HEALTH"
TARGET_SYMBOLS = {"BTCUSDT", "ETHUSDT"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect():
    con = sqlite3.connect(DB, timeout=30)
    con.row_factory = sqlite3.Row
    return con


def ensure(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS liquidation_events_v16 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            event_time INTEGER,
            symbol TEXT NOT NULL,
            side TEXT,
            order_type TEXT,
            original_qty REAL,
            price REAL,
            avg_price REAL,
            order_status TEXT,
            trade_time INTEGER,
            notional_usd REAL,
            version TEXT NOT NULL,
            payload TEXT NOT NULL
        );
    """)

    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_liq_v16_symbol_ts
        ON liquidation_events_v16(symbol, ts);
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS liquidation_stream_heartbeat_v16 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            version TEXT NOT NULL,
            state TEXT NOT NULL,
            stream_url TEXT,
            last_symbol TEXT,
            last_event_ts TEXT,
            stored_event INTEGER NOT NULL DEFAULT 0,
            ignored_event INTEGER NOT NULL DEFAULT 0,
            total_stored INTEGER NOT NULL DEFAULT 0,
            total_ignored INTEGER NOT NULL DEFAULT 0,
            total_errors INTEGER NOT NULL DEFAULT 0,
            message TEXT,
            payload TEXT
        );
    """)

    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_liq_hb_v16_ts
        ON liquidation_stream_heartbeat_v16(ts);
    """)

    cur.execute("DROP VIEW IF EXISTS latest_liquidation_stream_heartbeat_v16;")
    cur.execute("""
        CREATE VIEW latest_liquidation_stream_heartbeat_v16 AS
        SELECT *
        FROM liquidation_stream_heartbeat_v16
        ORDER BY id DESC
        LIMIT 1;
    """)


def heartbeat(
    state: str,
    message: str = "",
    payload=None,
    last_symbol=None,
    stored: int = 0,
    ignored: int = 0,
    error: int = 0,
    stream_url: str | None = None,
    last_event_ts: str | None = None,
):
    con = connect()
    cur = con.cursor()
    ensure(cur)

    prev = cur.execute("""
        SELECT total_stored, total_ignored, total_errors, last_event_ts
        FROM liquidation_stream_heartbeat_v16
        ORDER BY id DESC
        LIMIT 1;
    """).fetchone()

    if prev:
        total_stored = int(prev["total_stored"] or 0) + int(stored)
        total_ignored = int(prev["total_ignored"] or 0) + int(ignored)
        total_errors = int(prev["total_errors"] or 0) + int(error)
        if not last_event_ts:
            last_event_ts = prev["last_event_ts"]
    else:
        total_stored = int(stored)
        total_ignored = int(ignored)
        total_errors = int(error)

    cur.execute("""
        INSERT INTO liquidation_stream_heartbeat_v16 (
            ts, version, state, stream_url, last_symbol, last_event_ts,
            stored_event, ignored_event, total_stored, total_ignored,
            total_errors, message, payload
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
    """, (
        now_iso(),
        VERSION,
        state,
        stream_url,
        last_symbol,
        last_event_ts,
        int(stored),
        int(ignored),
        total_stored,
        total_ignored,
        total_errors,
        message,
        json.dumps(payload or {}, separators=(",", ":"), default=str),
    ))

    cur.execute("""
        DELETE FROM liquidation_stream_heartbeat_v16
        WHERE id NOT IN (
            SELECT id FROM liquidation_stream_heartbeat_v16
            ORDER BY id DESC
            LIMIT 10000
        );
    """)

    con.commit()
    con.close()


def normalize_message(msg: str):
    data = json.loads(msg)

    # Combined stream format:
    # {"stream":"btcusdt@forceOrder","data":{...}}
    if isinstance(data, dict) and "data" in data:
        data = data["data"]

    o = data.get("o", {}) if isinstance(data, dict) else {}
    symbol = o.get("s")
    return data, o, symbol


def store(msg: str):
    try:
        data, o, symbol = normalize_message(msg)

        if symbol not in TARGET_SYMBOLS:
            heartbeat(
                "MESSAGE_IGNORED_NON_TARGET_SYMBOL",
                last_symbol=symbol,
                ignored=1,
                payload={"symbol": symbol},
            )
            return

        price = float(o.get("p", 0) or 0)
        qty = float(o.get("q", 0) or 0)
        avg_price = float(o.get("ap", 0) or 0)
        notional = (avg_price or price) * qty
        event_ts = now_iso()

        con = connect()
        cur = con.cursor()
        ensure(cur)

        cur.execute("""
            INSERT INTO liquidation_events_v16 (
                ts, event_time, symbol, side, order_type, original_qty,
                price, avg_price, order_status, trade_time, notional_usd,
                version, payload
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """, (
            event_ts,
            data.get("E"),
            symbol,
            o.get("S"),
            o.get("o"),
            qty,
            price,
            avg_price,
            o.get("X"),
            o.get("T"),
            notional,
            VERSION,
            json.dumps(data, separators=(",", ":"), default=str),
        ))

        con.commit()
        con.close()

        heartbeat(
            "EVENT_STORED",
            last_symbol=symbol,
            stored=1,
            last_event_ts=event_ts,
            payload={
                "symbol": symbol,
                "side": o.get("S"),
                "notional_usd": notional,
                "price": price,
                "avg_price": avg_price,
            },
        )

    except Exception as e:
        heartbeat("STORE_ERROR", message=repr(e), error=1)


def main():
    try:
        import websocket
    except Exception as e:
        heartbeat("WEBSOCKET_CLIENT_NOT_AVAILABLE", message=repr(e), error=1)
        raise SystemExit(2)

    url = "wss://fstream.binance.com/stream?streams=btcusdt@forceOrder/ethusdt@forceOrder"

    def on_open(ws):
        heartbeat("WS_OPEN", message="BTC/ETH forceOrder stream open", stream_url=url)

    def on_message(ws, message):
        store(message)

    def on_error(ws, error):
        heartbeat("WS_ERROR", message=repr(error), error=1, stream_url=url)

    def on_close(ws, code, msg):
        heartbeat("WS_CLOSED", message=f"{code} {msg}", error=1, stream_url=url)

    def on_pong(ws, message):
        heartbeat("WS_PONG", message="pong", stream_url=url)

    heartbeat("STREAM_STARTING", stream_url=url)

    ws = websocket.WebSocketApp(
        url,
        on_open=on_open,
        on_message=on_message,
        on_error=on_error,
        on_close=on_close,
        on_pong=on_pong,
    )

    ws.run_forever(ping_interval=20, ping_timeout=10)


if __name__ == "__main__":
    main()
