#!/usr/bin/env python3
from __future__ import annotations

import json
import sqlite3
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

DB = Path("data/joanbot_v14.sqlite")
VERSION = "V18.2_MARKET_CONTEXT_COLLECTOR"

MACRO_TABLE = "market_context_v18_2"
DERIV_TABLE = "derivatives_context_v18_2"

YAHOO = {
    "VIX": "^VIX",
    "SPX": "^GSPC",
    "NASDAQ": "^IXIC",
    "DXY": "DX-Y.NYB",
    "US10Y": "^TNX",
}

BINANCE_SYMBOLS = ["BTCUSDT", "ETHUSDT"]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def qid(x: str) -> str:
    return '"' + str(x).replace('"', '""') + '"'


def http_json(url: str, timeout: int = 8) -> Dict[str, Any]:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 Termux JoanBot/18.2",
            "Accept": "application/json,text/plain,*/*",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", errors="ignore"))


def create_tables(con: sqlite3.Connection) -> None:
    con.execute(f"""
        CREATE TABLE IF NOT EXISTS {qid(MACRO_TABLE)} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            version TEXT NOT NULL,
            label TEXT NOT NULL,
            symbol TEXT,
            value REAL,
            change_pct REAL,
            source TEXT,
            status TEXT,
            payload TEXT
        )
    """)

    con.execute(f"""
        CREATE TABLE IF NOT EXISTS {qid(DERIV_TABLE)} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            version TEXT NOT NULL,
            symbol TEXT NOT NULL,
            metric TEXT NOT NULL,
            value REAL,
            source TEXT,
            status TEXT,
            payload TEXT
        )
    """)

    con.execute(f"CREATE INDEX IF NOT EXISTS idx_macro_v18_2_label_ts ON {qid(MACRO_TABLE)}(label, ts)")
    con.execute(f"CREATE INDEX IF NOT EXISTS idx_deriv_v18_2_symbol_metric_ts ON {qid(DERIV_TABLE)}(symbol, metric, ts)")


def insert_macro(con, label, symbol, value, change_pct, source, status, payload):
    con.execute(f"""
        INSERT INTO {qid(MACRO_TABLE)}
        (ts, version, label, symbol, value, change_pct, source, status, payload)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        utc_now(),
        VERSION,
        label,
        symbol,
        value,
        change_pct,
        source,
        status,
        json.dumps(payload, sort_keys=True),
    ))


def insert_deriv(con, symbol, metric, value, source, status, payload):
    con.execute(f"""
        INSERT INTO {qid(DERIV_TABLE)}
        (ts, version, symbol, metric, value, source, status, payload)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        utc_now(),
        VERSION,
        symbol,
        metric,
        value,
        source,
        status,
        json.dumps(payload, sort_keys=True),
    ))


def fetch_yahoo(label: str, symbol: str) -> Dict[str, Any]:
    enc = urllib.parse.quote(symbol, safe="")
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{enc}?range=2d&interval=5m"
    data = http_json(url)

    result = data.get("chart", {}).get("result", [])
    if not result:
        raise RuntimeError("YAHOO_EMPTY_RESULT")

    r = result[0]
    meta = r.get("meta", {})
    price = meta.get("regularMarketPrice")

    closes = []
    try:
        closes = r.get("indicators", {}).get("quote", [{}])[0].get("close", []) or []
        closes = [float(x) for x in closes if x is not None and float(x) > 0]
    except Exception:
        closes = []

    if price is None and closes:
        price = closes[-1]

    change = None
    prev = meta.get("chartPreviousClose") or meta.get("previousClose")
    try:
        if price is not None and prev:
            change = float(price) / float(prev) - 1.0
        elif len(closes) >= 2:
            change = closes[-1] / closes[0] - 1.0
    except Exception:
        change = None

    if price is None:
        raise RuntimeError("YAHOO_NO_PRICE")

    return {
        "label": label,
        "symbol": symbol,
        "value": float(price),
        "change_pct": change,
        "source": "yahoo_chart",
        "status": "OK",
        "payload": {
            "meta": meta,
            "close_n": len(closes),
        },
    }


def fetch_fear_greed() -> Dict[str, Any]:
    url = "https://api.alternative.me/fng/?limit=1&format=json"
    data = http_json(url)
    row = (data.get("data") or [{}])[0]
    value = float(row.get("value"))
    return {
        "label": "FEAR_GREED",
        "symbol": "CRYPTO_FNG",
        "value": value,
        "change_pct": None,
        "source": "alternative_me",
        "status": "OK",
        "payload": row,
    }


def fetch_binance_derivatives(symbol: str) -> Dict[str, Any]:
    out = {}

    # mark price + funding
    try:
        url = f"https://fapi.binance.com/fapi/v1/premiumIndex?symbol={symbol}"
        data = http_json(url)
        out["mark_price"] = float(data.get("markPrice"))
        out["funding_rate"] = float(data.get("lastFundingRate"))
        out["premium_payload"] = data
    except Exception as e:
        out["premium_error"] = repr(e)

    # open interest
    try:
        url = f"https://fapi.binance.com/fapi/v1/openInterest?symbol={symbol}"
        data = http_json(url)
        out["open_interest"] = float(data.get("openInterest"))
        out["open_interest_payload"] = data
    except Exception as e:
        out["open_interest_error"] = repr(e)

    # 24h futures ticker
    try:
        url = f"https://fapi.binance.com/fapi/v1/ticker/24hr?symbol={symbol}"
        data = http_json(url)
        out["price_change_pct_24h"] = float(data.get("priceChangePercent")) / 100.0
        out["volume_24h"] = float(data.get("quoteVolume"))
        out["ticker_payload"] = data
    except Exception as e:
        out["ticker_error"] = repr(e)

    return out


def main() -> int:
    if not DB.exists():
        print("DB_MISSING")
        return 2

    con = sqlite3.connect(DB)
    create_tables(con)

    results = []

    for label, symbol in YAHOO.items():
        try:
            r = fetch_yahoo(label, symbol)
            insert_macro(con, **r)
            results.append((label, "OK", r["value"]))
        except Exception as e:
            insert_macro(con, label, symbol, None, None, "yahoo_chart", "ERROR", {"error": repr(e)})
            results.append((label, "ERROR", repr(e)))

    try:
        r = fetch_fear_greed()
        insert_macro(con, **r)
        results.append(("FEAR_GREED", "OK", r["value"]))
    except Exception as e:
        insert_macro(con, "FEAR_GREED", "CRYPTO_FNG", None, None, "alternative_me", "ERROR", {"error": repr(e)})
        results.append(("FEAR_GREED", "ERROR", repr(e)))

    for sym in BINANCE_SYMBOLS:
        data = fetch_binance_derivatives(sym)
        for metric in ["mark_price", "funding_rate", "open_interest", "price_change_pct_24h", "volume_24h"]:
            val = data.get(metric)
            status = "OK" if val is not None else "ERROR"
            insert_deriv(con, sym, metric, val, "binance_futures", status, data)
            results.append((sym + ":" + metric, status, val))

    con.commit()
    con.close()

    print("V18_2_MARKET_CONTEXT_DONE")
    for x in results:
        print(x)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
