#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import sqlite3
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

DB = Path("data/joanbot_v14.sqlite")

LATEST = "institutional_market_data_latest_v18_9"
HISTORY = "institutional_market_data_history_v18_9"
HEALTH = "institutional_market_data_health_v18_9"
AUDIT = "institutional_market_data_semantic_audit_v18_9_1"

VERSION = "V18.9.4_SEMANTIC_DATA_PLANE_LIQUIDATION_BOUND"

OUT = Path("data/v18_9_1_data_plane")

METRICS = {
    "BTC_PRICE": ("BTCUSDT", 5),
    "BTC_CHANGE_24H": ("BTCUSDT", 5),
    "ETH_PRICE": ("ETHUSDT", 5),
    "ETH_CHANGE_24H": ("ETHUSDT", 5),

    "BTC_FUNDING": ("BTCUSDT", 20),
    "BTC_OI": ("BTCUSDT", 20),
    "BTC_LONG_SHORT": ("BTCUSDT", 20),

    "ETH_FUNDING": ("ETHUSDT", 20),
    "ETH_OI": ("ETHUSDT", 20),
    "ETH_LONG_SHORT": ("ETHUSDT", 20),

    "VIX": ("GLOBAL", 360),
    "DXY": ("GLOBAL", 360),
    "NASDAQ": ("GLOBAL", 360),
    "NASDAQ_CHANGE": ("GLOBAL", 360),
    "US10Y": ("GLOBAL", 720),
    "FEAR_GREED": ("GLOBAL", 1800),

    "BTC_CVD": ("BTCUSDT", 60),
    "BTC_LIQUIDATIONS": ("BTCUSDT", 60),
    "ETH_CVD": ("ETHUSDT", 60),
    "ETH_LIQUIDATIONS": ("ETHUSDT", 60),
}

RANGES = {
    "BTC_PRICE": (1000, 500000),
    "ETH_PRICE": (50, 50000),
    "BTC_CHANGE_24H": (-50, 50),
    "ETH_CHANGE_24H": (-50, 50),

    "BTC_FUNDING": (-0.10, 0.10),
    "ETH_FUNDING": (-0.10, 0.10),
    "BTC_OI": (1, 1e12),
    "ETH_OI": (1, 1e12),
    "BTC_LONG_SHORT": (0.05, 20),
    "ETH_LONG_SHORT": (0.05, 20),

    "VIX": (5, 100),
    "DXY": (70, 140),
    "NASDAQ": (1000, 100000),
    "NASDAQ_CHANGE": (-20, 20),
    "US10Y": (0, 15),
    "FEAR_GREED": (0, 100),

    "BTC_CVD": (-1e15, 1e15),
    "ETH_CVD": (-1e15, 1e15),
    "BTC_LIQUIDATIONS": (0, 1e12),
    "ETH_LIQUIDATIONS": (0, 1e12),
}

SELF_TABLES = {LATEST, HISTORY, HEALTH, AUDIT}

ALIASES = {
    "VIX": ["vix", "volatility_index", "cboe_vix"],
    "DXY": ["dxy", "dollar_index"],
    "NASDAQ": ["nasdaq", "ndx", "ixic"],
    "NASDAQ_CHANGE": ["nasdaq_change", "ndx_change", "ixic_change"],
    "US10Y": ["us10y", "10y_yield", "treasury_10y", "yield10y"],
    "FEAR_GREED": ["fear_greed", "feargreed", "crypto_fear"],

    "BTC_CVD": ["btc_cvd", "cvd_btc", "btc_volume_delta", "volume_delta_btc"],
    "ETH_CVD": ["eth_cvd", "cvd_eth", "eth_volume_delta", "volume_delta_eth"],
    "BTC_LIQUIDATIONS": ["btc_liquidations", "btc_liq", "liquidations_btc"],
    "ETH_LIQUIDATIONS": ["eth_liquidations", "eth_liq", "liquidations_eth"],
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def qid(x: str) -> str:
    return '"' + str(x).replace('"', '""') + '"'


def fnum(x: Any, default=None):
    try:
        if x is None or x == "":
            return default
        if isinstance(x, str):
            x = x.replace(",", "").replace("%", "").strip()
        v = float(x)
        if math.isnan(v) or math.isinf(v):
            return default
        return v
    except Exception:
        return default


def parse_ts(x: Any):
    if not x:
        return None
    try:
        d = datetime.fromisoformat(str(x).replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.astimezone(timezone.utc)
    except Exception:
        return None


def age_min(ts: Any):
    d = parse_ts(ts)
    if not d:
        return None
    return max(0.0, (datetime.now(timezone.utc) - d).total_seconds() / 60.0)


def connect():
    con = sqlite3.connect(DB, timeout=120, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=120000")
    try:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=NORMAL")
    except Exception:
        pass
    return con

def rows(con, sql: str, args=()):
    try:
        return [dict(r) for r in con.execute(sql, args).fetchall()]
    except Exception:
        return []


def exists(con, table: str) -> bool:
    return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def cols(con, table: str) -> List[str]:
    if not exists(con, table):
        return []
    return [r[1] for r in con.execute(f"PRAGMA table_info({qid(table)})")]


def create_tables(con):
    con.execute(f"""
        CREATE TABLE IF NOT EXISTS {qid(LATEST)} (
            metric TEXT NOT NULL,
            scope TEXT NOT NULL,
            ts TEXT NOT NULL,
            value REAL,
            value_text TEXT,
            status TEXT NOT NULL,
            age_min REAL,
            stale_limit_min REAL,
            quality REAL,
            source TEXT,
            source_detail TEXT,
            payload TEXT,
            PRIMARY KEY(metric, scope)
        )
    """)

    con.execute(f"""
        CREATE TABLE IF NOT EXISTS {qid(HISTORY)} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            metric TEXT NOT NULL,
            scope TEXT NOT NULL,
            ts TEXT NOT NULL,
            value REAL,
            value_text TEXT,
            status TEXT NOT NULL,
            age_min REAL,
            stale_limit_min REAL,
            quality REAL,
            source TEXT,
            source_detail TEXT,
            payload TEXT
        )
    """)

    con.execute(f"""
        CREATE TABLE IF NOT EXISTS {qid(HEALTH)} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            version TEXT NOT NULL,
            live_count INTEGER,
            stale_count INTEGER,
            miss_count INTEGER,
            invalid_count INTEGER,
            error_count INTEGER,
            summary TEXT,
            payload TEXT
        )
    """)

    con.execute(f"""
        CREATE TABLE IF NOT EXISTS {qid(AUDIT)} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            metric TEXT,
            rejected_value TEXT,
            reason TEXT,
            source TEXT,
            source_detail TEXT,
            payload TEXT
        )
    """)


def audit(con, metric, value, reason, source, detail, payload):
    con.execute(f"""
        INSERT INTO {qid(AUDIT)}
        (ts, metric, rejected_value, reason, source, source_detail, payload)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        now_iso(), metric, str(value), reason, source, detail,
        json.dumps(payload or {}, sort_keys=True),
    ))


def semantic_reason(metric: str, value: Any, source: str, detail: str) -> Optional[str]:
    v = fnum(value)
    low_detail = str(detail).lower()
    low_source = str(source).lower()

    if v is None:
        return "VALUE_MISSING"

    lo, hi = RANGES.get(metric, (-1e99, 1e99))
    if not (lo <= v <= hi):
        return f"OUT_OF_RANGE_{lo}_{hi}"

    is_change_metric = metric.endswith("_CHANGE") or metric.endswith("_CHANGE_24H")

    if not is_change_metric and any(k in low_detail for k in [".chg", "_chg", "change_pct", "changepercent", "change_percent"]):
        return "CHANGE_FIELD_USED_AS_LEVEL"

    if metric.endswith("_CVD"):
        if "cvd" not in low_detail and "delta" not in low_detail:
            return "CVD_REQUIRES_CVD_OR_DELTA_SOURCE"
        if "volume" in low_detail and "volume_delta" not in low_detail:
            return "RAW_VOLUME_IS_NOT_CVD"

    if metric.endswith("_LIQUIDATIONS"):
        if "liq" not in low_detail and "liquid" not in low_detail:
            return "LIQUIDATION_REQUIRES_LIQ_SOURCE"

    if "db_fallback" in low_source and any(t.lower() in low_detail for t in SELF_TABLES):
        return "SELF_REFERENTIAL_DATA_PLANE_FALLBACK"

    return None


def emit(con, metric: str, value: Any, source: str, detail: str, ts: Optional[str] = None, payload=None):
    scope, stale = METRICS[metric]
    ts = ts or now_iso()
    payload = payload or {}

    reason = semantic_reason(metric, value, source, detail)

    if reason == "VALUE_MISSING":
        status = "MISS"
        v = None
        quality = 0.0
    elif reason:
        status = "INVALID"
        v = None
        quality = 0.0
        audit(con, metric, value, reason, source, detail, payload)
    else:
        v = fnum(value)
        a = age_min(ts)
        if a is not None and a > stale:
            status = "STALE"
            quality = max(0.05, 1.0 - min(a / max(stale, 1), 5) / 5)
        else:
            status = "LIVE"
            quality = 1.0

    a = age_min(ts)
    txt = None if value is None else str(value)
    pl = dict(payload)
    if reason:
        pl["semantic_reject_reason"] = reason
        pl["rejected_value"] = txt

    payload_s = json.dumps(pl, sort_keys=True)

    con.execute(f"""
        INSERT OR REPLACE INTO {qid(LATEST)}
        (metric, scope, ts, value, value_text, status, age_min, stale_limit_min, quality, source, source_detail, payload)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        metric, scope, ts, v, txt, status, a, float(stale), quality, source, detail, payload_s,
    ))

    con.execute(f"""
        INSERT INTO {qid(HISTORY)}
        (metric, scope, ts, value, value_text, status, age_min, stale_limit_min, quality, source, source_detail, payload)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        metric, scope, ts, v, txt, status, a, float(stale), quality, source, detail, payload_s,
    ))


def http_json(url: str, timeout: float = 5.0):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 JoanBot Institutional Data Plane",
            "Accept": "application/json,text/plain,*/*",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", errors="ignore"))


def fetch_binance(con, errors: List[str]):
    for sym in ["BTCUSDT", "ETHUSDT"]:
        p = sym[:3]

        try:
            d = http_json(f"https://api.binance.com/api/v3/ticker/24hr?symbol={sym}")
            emit(con, f"{p}_PRICE", d.get("lastPrice"), "binance_spot", f"ticker24h.{sym}.lastPrice", payload=d)
            emit(con, f"{p}_CHANGE_24H", d.get("priceChangePercent"), "binance_spot", f"ticker24h.{sym}.priceChangePercent", payload=d)
        except Exception as e:
            errors.append(f"{p}_ticker:{repr(e)}")

        try:
            d = http_json(f"https://fapi.binance.com/fapi/v1/premiumIndex?symbol={sym}")
            emit(con, f"{p}_FUNDING", d.get("lastFundingRate"), "binance_futures", f"premiumIndex.{sym}.lastFundingRate", payload=d)
        except Exception as e:
            errors.append(f"{p}_funding:{repr(e)}")

        try:
            d = http_json(f"https://fapi.binance.com/fapi/v1/openInterest?symbol={sym}")
            emit(con, f"{p}_OI", d.get("openInterest"), "binance_futures", f"openInterest.{sym}.openInterest", payload=d)
        except Exception as e:
            errors.append(f"{p}_oi:{repr(e)}")

        try:
            url = "https://fapi.binance.com/futures/data/globalLongShortAccountRatio?" + urllib.parse.urlencode({
                "symbol": sym,
                "period": "5m",
                "limit": "1",
            })
            d = http_json(url)
            if isinstance(d, list) and d:
                emit(con, f"{p}_LONG_SHORT", d[-1].get("longShortRatio"), "binance_futures", f"longShortRatio.{sym}.longShortRatio", payload=d[-1])
        except Exception as e:
            errors.append(f"{p}_longshort:{repr(e)}")


def yahoo_quote(symbols: str):
    url = "https://query1.finance.yahoo.com/v7/finance/quote?" + urllib.parse.urlencode({"symbols": symbols})
    d = http_json(url)
    return (((d or {}).get("quoteResponse") or {}).get("result") or [])


def yahoo_chart(symbol: str):
    url = "https://query1.finance.yahoo.com/v8/finance/chart/" + urllib.parse.quote(symbol) + "?range=1d&interval=5m"
    d = http_json(url)
    result = (((d or {}).get("chart") or {}).get("result") or [])
    if not result:
        return None
    meta = result[0].get("meta") or {}
    return meta



def fetch_cvd_proxy(con, errors: List[str]):
    """
    CVD proxy institucional:
    - Usa Binance aggTrades.
    - No és orderflow perfecte de futures, però és una proxy real i auditable.
    - m=True a Binance = buyer is maker => agressió venedora.
    """
    for sym in ["BTCUSDT", "ETHUSDT"]:
        p = sym[:3]
        try:
            d = http_json(f"https://api.binance.com/api/v3/aggTrades?symbol={sym}&limit=1000")

            if not isinstance(d, list) or not d:
                emit(con, f"{p}_CVD", None, "binance_spot_aggtrades", f"aggTrades.{sym}.cvd_proxy_empty")
                continue

            buy_notional = 0.0
            sell_notional = 0.0
            signed = 0.0

            for tr in d:
                price = fnum(tr.get("p"), 0.0) or 0.0
                qty = fnum(tr.get("q"), 0.0) or 0.0
                notional = price * qty

                # buyer maker => seller taker => agressió venedora
                if tr.get("m") is True:
                    sell_notional += notional
                    signed -= notional
                else:
                    buy_notional += notional
                    signed += notional

            payload = {
                "symbol": sym,
                "n": len(d),
                "buy_notional": buy_notional,
                "sell_notional": sell_notional,
                "signed_notional": signed,
                "source_note": "spot aggTrades taker-flow proxy",
            }

            emit(
                con,
                f"{p}_CVD",
                signed,
                "binance_spot_aggtrades",
                f"aggTrades.{sym}.cvd_proxy_1000",
                payload=payload,
            )

        except Exception as e:
            errors.append(f"{p}_cvd_proxy:{repr(e)}")
def fetch_yahoo_macro(con, errors: List[str]):
    """
    V18.9.3:
    No usa Yahoo quote perquè retorna 401 en aquesta tablet.
    Usa Yahoo chart, que a la teva sortida ja funciona.
    """
    chart_map = {
        "VIX": "^VIX",
        "DXY": "DX-Y.NYB",
        "NASDAQ": "^IXIC",
        "US10Y": "^TNX",
    }

    for metric, sym in chart_map.items():
        try:
            meta = yahoo_chart(sym)
            if not meta:
                emit(con, metric, None, "yahoo_chart", f"{sym}.meta_missing")
                continue

            val = meta.get("regularMarketPrice")

            if metric == "US10Y":
                raw = fnum(val)
                val = raw / 10.0 if raw and raw > 20 else raw

            emit(
                con,
                metric,
                val,
                "yahoo_chart",
                f"{sym}.meta.regularMarketPrice",
                payload=meta,
            )

            if metric == "NASDAQ":
                chg = meta.get("regularMarketChangePercent")
                emit(
                    con,
                    "NASDAQ_CHANGE",
                    chg,
                    "yahoo_chart",
                    f"{sym}.meta.regularMarketChangePercent",
                    payload=meta,
                )

        except Exception as e:
            errors.append(f"yahoo_chart_{metric}:{repr(e)}")

def fetch_fear(con, errors):
    try:
        d = http_json("https://api.alternative.me/fng/?limit=1&format=json")
        arr = d.get("data") or []
        if arr:
            emit(con, "FEAR_GREED", arr[0].get("value"), "alternative_me", "fear_greed.value", payload=arr[0])
    except Exception as e:
        errors.append(f"fear_greed:{repr(e)}")


def norm(x: Any) -> str:
    return "".join(ch for ch in str(x).lower() if ch.isalnum())


def alias_hit(name: Any, aliases: List[str]) -> bool:
    n = norm(name)
    for a in aliases:
        aa = norm(a)
        if aa and (aa in n or n in aa):
            return True
    return False


def parse_payload(x):
    if not x:
        return {}
    try:
        d = json.loads(str(x))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def flatten(obj, prefix=""):
    out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            key = f"{prefix}.{k}" if prefix else str(k)
            out.extend(flatten(v, key))
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:20]):
            key = f"{prefix}.{i}" if prefix else str(i)
            out.extend(flatten(v, key))
    else:
        out.append((prefix, obj))
    return out


def candidate_tables(con) -> List[str]:
    out = []
    for r in rows(con, "SELECT name FROM sqlite_master WHERE type='table'"):
        t = r["name"]
        low = t.lower()
        if t in SELF_TABLES:
            continue
        if any(k in low for k in ["macro", "market", "derivative", "liquid", "funding", "snapshot", "context"]):
            out.append(t)
    return out[:80]


def db_fallback(con, metric: str):
    aliases = ALIASES.get(metric, [])
    if not aliases:
        return None

    best = None

    def consider(value, ts, table, col, detail, payload):
        nonlocal best

        low = detail.lower()

        if metric in {"VIX", "DXY", "NASDAQ", "US10Y"}:
            if any(k in low for k in ["chg", "change", "percent", "pct"]):
                return

        if metric.endswith("_CVD") and ("cvd" not in low and "delta" not in low):
            return

        if metric.endswith("_LIQUIDATIONS") and ("liq" not in low and "liquid" not in low):
            return

        v = fnum(value)
        if v is None:
            return

        a = age_min(ts) if ts else None
        score = 0.0 if a is None else max(0.0, 1.0 - min(a, 1440) / 1440)

        item = {
            "value": v,
            "ts": ts or now_iso(),
            "score": score,
            "table": table,
            "col": col,
            "detail": detail,
            "payload": payload or {},
        }

        if best is None or item["score"] > best["score"]:
            best = item

    for t in candidate_tables(con):
        c = cols(con, t)
        if not c:
            continue

        ts_col = next((x for x in ["ts", "created_at", "updated_at", "time", "date"] if x in c), None)
        order = ts_col or ("id" if "id" in c else "rowid")

        for col in c:
            if alias_hit(col, aliases):
                sql = f"""
                    SELECT {qid(col)} AS v, {qid(ts_col) if ts_col else "NULL"} AS ts
                    FROM {qid(t)}
                    WHERE {qid(col)} IS NOT NULL
                    ORDER BY {qid(order) if order != "rowid" else "rowid"} DESC
                    LIMIT 20
                """
                for r in rows(con, sql):
                    consider(r.get("v"), r.get("ts"), t, col, f"{t}.{col}", {})

        for pcol in [x for x in c if x.lower() in {"payload", "data", "json", "raw", "response", "metrics"}]:
            sql = f"""
                SELECT {qid(pcol)} AS payload, {qid(ts_col) if ts_col else "NULL"} AS ts
                FROM {qid(t)}
                WHERE {qid(pcol)} IS NOT NULL
                ORDER BY {qid(order) if order != "rowid" else "rowid"} DESC
                LIMIT 20
            """
            for r in rows(con, sql):
                payload = parse_payload(r.get("payload"))
                for path, val in flatten(payload):
                    if alias_hit(path, aliases):
                        consider(val, r.get("ts"), t, pcol, f"{t}.{pcol}:{path}", payload)

    return best



def fetch_liquidation_rollup(con, errors: List[str]):
    """
    V18.9.4:
    Llegeix només el collector canònic V18.10.
    Si el WebSocket està viu i no hi ha liquidacions, el valor 0 és vàlid.
    """
    table = "institutional_liquidation_rollup_latest_v18_10"

    if not exists(con, table):
        emit(con, "BTC_LIQUIDATIONS", None, "missing", "v18_10_liquidation_rollup_missing")
        emit(con, "ETH_LIQUIDATIONS", None, "missing", "v18_10_liquidation_rollup_missing")
        return

    for sym, metric in [("BTCUSDT", "BTC_LIQUIDATIONS"), ("ETHUSDT", "ETH_LIQUIDATIONS")]:
        r = rows(con, f"""
            SELECT symbol, ts, connection_state, last_event_ts, last_msg_age_min,
                   events_5m, events_15m, events_60m,
                   total_5m_usd, total_15m_usd, total_60m_usd,
                   long_liq_15m_usd, short_liq_15m_usd,
                   long_liq_60m_usd, short_liq_60m_usd,
                   largest_60m_usd, source, payload
            FROM {qid(table)}
            WHERE symbol=?
            LIMIT 1
        """, (sym,))

        if not r:
            emit(con, metric, None, "missing", f"v18_10_rollup_missing.{sym}")
            continue

        row = r[0]
        state = str(row.get("connection_state") or "")

        if "LIVE" not in state:
            emit(con, metric, None, "liquidation_collector", f"{sym}.collector_disconnected", payload=row)
            continue

        # Valor canònic per dashboard/risk: liquidacions totals 15m USD.
        emit(
            con,
            metric,
            row.get("total_15m_usd"),
            "liquidation_collector_v18_10",
            f"{sym}.total_15m_usd",
            ts=row.get("ts"),
            payload=row,
        )


def derive_nasdaq_change(con, errors: List[str]):
    """
    V18.9.4:
    Si Yahoo chart no entrega regularMarketChangePercent, deriva % amb previousClose.
    """
    r = rows(con, f"""
        SELECT value, payload
        FROM {qid(LATEST)}
        WHERE metric='NASDAQ' AND status='LIVE'
        LIMIT 1
    """)
    if not r:
        return

    price = fnum(r[0].get("value"))
    payload = {}
    try:
        payload = json.loads(r[0].get("payload") or "{}")
    except Exception:
        payload = {}

    prev = (
        payload.get("chartPreviousClose")
        or payload.get("previousClose")
        or payload.get("regularMarketPreviousClose")
    )

    prev = fnum(prev)

    if price and prev and prev > 0:
        change = (price - prev) / prev * 100.0
        emit(
            con,
            "NASDAQ_CHANGE",
            change,
            "derived_yahoo_chart",
            "^IXIC.regularMarketPrice_vs_previousClose",
            payload={"price": price, "previous_close": prev, "derived_change_pct": change},
        )
def apply_db_fallbacks(con):
    """
    V18.9.3:
    Fallback DB només per macro estable.
    Prohibit usar fallback brut per:
    - CVD
    - liquidacions
    perquè és massa fàcil confondre volum/id/features antigues amb senyal real.
    """
    forbidden = {
        "BTC_CVD",
        "ETH_CVD",
        "BTC_LIQUIDATIONS",
        "ETH_LIQUIDATIONS",
    }

    for metric in ALIASES:
        if metric in forbidden:
            continue

        live = rows(con, f"SELECT 1 FROM {qid(LATEST)} WHERE metric=? AND status='LIVE' LIMIT 1", (metric,))
        if live:
            continue

        item = db_fallback(con, metric)
        if item:
            emit(
                con,
                metric,
                item["value"],
                "db_fallback_validated",
                item["detail"],
                ts=item["ts"],
                payload=item,
            )

def ensure_all(con):
    for m in METRICS:
        got = rows(con, f"SELECT 1 FROM {qid(LATEST)} WHERE metric=? LIMIT 1", (m,))
        if not got:
            emit(con, m, None, "missing", "no_valid_source_found")



def _runtime_colset(con, table: str):
    try:
        return {r[1] for r in con.execute(f"PRAGMA table_info({qid(table)})")}
    except Exception:
        return set()


def _runtime_table_exists(con, table: str) -> bool:
    try:
        return con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone() is not None
    except Exception:
        return False


def _runtime_exec_retry(con, sql: str, args=(), tries: int = 12):
    import time as _time
    last = None
    for i in range(tries):
        try:
            return con.execute(sql, args)
        except sqlite3.OperationalError as e:
            last = e
            if "locked" not in str(e).lower() and "busy" not in str(e).lower():
                raise
            _time.sleep(1.5 + i * 0.75)
    raise last


def runtime_schema_guard(con):
    """
    V18.9.2:
    El Data Plane no pot dependre d’un migrador extern.
    Abans d’escriure, garanteix les columnes que necessita.
    """
    create_tables(con)

    expected = {
        LATEST: {
            "metric": "TEXT",
            "scope": "TEXT",
            "ts": "TEXT",
            "value": "REAL",
            "value_text": "TEXT",
            "status": "TEXT DEFAULT 'MISS'",
            "age_min": "REAL",
            "stale_limit_min": "REAL",
            "quality": "REAL",
            "source": "TEXT",
            "source_detail": "TEXT",
            "payload": "TEXT",
        },
        HISTORY: {
            "metric": "TEXT",
            "scope": "TEXT",
            "ts": "TEXT",
            "value": "REAL",
            "value_text": "TEXT",
            "status": "TEXT DEFAULT 'MISS'",
            "age_min": "REAL",
            "stale_limit_min": "REAL",
            "quality": "REAL",
            "source": "TEXT",
            "source_detail": "TEXT",
            "payload": "TEXT",
        },
        HEALTH: {
            "ts": "TEXT",
            "version": "TEXT",
            "live_count": "INTEGER DEFAULT 0",
            "stale_count": "INTEGER DEFAULT 0",
            "miss_count": "INTEGER DEFAULT 0",
            "invalid_count": "INTEGER DEFAULT 0",
            "error_count": "INTEGER DEFAULT 0",
            "summary": "TEXT",
            "payload": "TEXT",
        },
        AUDIT: {
            "ts": "TEXT",
            "metric": "TEXT",
            "rejected_value": "TEXT",
            "reason": "TEXT",
            "source": "TEXT",
            "source_detail": "TEXT",
            "payload": "TEXT",
        },
    }

    for table, cols_required in expected.items():
        existing = _runtime_colset(con, table)
        for col, spec in cols_required.items():
            if col not in existing:
                _runtime_exec_retry(
                    con,
                    f"ALTER TABLE {qid(table)} ADD COLUMN {qid(col)} {spec}"
                )
                existing.add(col)

    for col in ["live_count", "stale_count", "miss_count", "invalid_count", "error_count"]:
        if col in _runtime_colset(con, HEALTH):
            _runtime_exec_retry(
                con,
                f"UPDATE {qid(HEALTH)} SET {qid(col)}=0 WHERE {qid(col)} IS NULL"
            )

    final_health_cols = _runtime_colset(con, HEALTH)
    if "invalid_count" not in final_health_cols:
        raise RuntimeError("SCHEMA_GUARD_FAILED_INVALID_COUNT_MISSING")

    return True

def main():
    OUT.mkdir(parents=True, exist_ok=True)

    if not DB.exists():
        raise SystemExit("DB_MISSING")

    errors = []

    con = connect()
    runtime_schema_guard(con)

    qc = con.execute("PRAGMA quick_check").fetchone()[0]

    # Neteja latest per no arrossegar dades falses d'una versió anterior.
    con.execute(
        f"DELETE FROM {qid(LATEST)} WHERE metric IN ({','.join('?' for _ in METRICS)})",
        tuple(METRICS.keys()),
    )

    fetch_binance(con, errors)
    fetch_cvd_proxy(con, errors)
    fetch_yahoo_macro(con, errors)
    derive_nasdaq_change(con, errors)
    fetch_liquidation_rollup(con, errors)
    fetch_fear(con, errors)
    apply_db_fallbacks(con)
    ensure_all(con)

    counts = {
        r["status"]: int(r["n"])
        for r in rows(con, f"SELECT status, COUNT(*) n FROM {qid(LATEST)} GROUP BY status")
    }

    live = counts.get("LIVE", 0)
    stale = counts.get("STALE", 0)
    miss = counts.get("MISS", 0)
    invalid = counts.get("INVALID", 0)

    core = [
        "BTC_PRICE", "ETH_PRICE",
        "BTC_FUNDING", "ETH_FUNDING",
        "BTC_OI", "ETH_OI",
        "BTC_LONG_SHORT", "ETH_LONG_SHORT",
        "FEAR_GREED",
    ]

    core_ok = True
    for m in core:
        r = rows(con, f"SELECT status FROM {qid(LATEST)} WHERE metric=?", (m,))
        if not r or r[0]["status"] != "LIVE":
            core_ok = False


    microstructure = [
        "BTC_CVD",
        "ETH_CVD",
        "BTC_LIQUIDATIONS",
        "ETH_LIQUIDATIONS",
    ]

    micro_status = {
        m: (rows(con, f"SELECT status FROM {qid(LATEST)} WHERE metric=? LIMIT 1", (m,)) or [{"status": "MISS"}])[0]["status"]
        for m in microstructure
    }

    micro_missing = [m for m, s in micro_status.items() if s != "LIVE"]

    if not core_ok:
        summary = "BAD_CORE_MISSING"
    elif invalid > 0:
        summary = "DEGRADED_INVALID_DATA"
    elif errors:
        summary = "DEGRADED_FETCH_ERRORS"
    elif micro_missing:
        summary = "DEGRADED_MICROSTRUCTURE_GAPS"
    elif miss > 0:
        summary = "DEGRADED_NONCORE_MISSING"
    else:
        summary = "OK"

    payload = {
        "version": VERSION,
        "db": qc,
        "counts": counts,
        "core_ok": core_ok,
        "errors": errors,
        "micro_status": micro_status if "micro_status" in locals() else {},
    }

    con.execute(f"""
        INSERT INTO {qid(HEALTH)}
        (ts, version, live_count, stale_count, miss_count, invalid_count, error_count, summary, payload)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        now_iso(), VERSION, live, stale, miss, invalid, len(errors), summary,
        json.dumps(payload, sort_keys=True),
    ))

    con.commit()

    latest = rows(con, f"""
        SELECT metric, scope, value, status, ROUND(COALESCE(age_min,-1),1) age_min, source, source_detail
        FROM {qid(LATEST)}
        ORDER BY metric
    """)

    invalid_rows = rows(con, f"""
        SELECT metric, rejected_value, reason, source, source_detail
        FROM {qid(AUDIT)}
        ORDER BY id DESC LIMIT 20
    """)

    con.close()

    (OUT / "latest.json").write_text(json.dumps(latest, indent=2, sort_keys=True))
    (OUT / "health.json").write_text(json.dumps(payload, indent=2, sort_keys=True))

    print("===== V18.9.4 SEMANTIC DATA PLANE LIQUIDATION BOUND =====")
    print("db:", qc)
    print("summary:", summary)
    print("live:", live, "stale:", stale, "miss:", miss, "invalid:", invalid, "errors:", len(errors))

    for r in latest:
        print(dict(r))

    if invalid_rows:
        print("===== SEMANTIC REJECTS =====")
        for r in invalid_rows:
            print(dict(r))

    if errors:
        print("===== FETCH ERRORS =====")
        for e in errors[:20]:
            print(e)

    return 0 if summary in {"OK", "DEGRADED_CORE_OK"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
