#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import sqlite3
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

DB = Path("data/joanbot_v14.sqlite")

LATEST = "institutional_market_data_latest_v18_9"
HISTORY = "institutional_market_data_history_v18_9"
HEALTH = "institutional_market_data_health_v18_9"

VERSION = "V18.9_INSTITUTIONAL_DATA_PLANE"

OUT = Path("data/v18_9_data_plane")

METRICS = {
    "BTC_PRICE": {"scope": "BTCUSDT", "stale": 5},
    "BTC_CHANGE_24H": {"scope": "BTCUSDT", "stale": 5},
    "ETH_PRICE": {"scope": "ETHUSDT", "stale": 5},
    "ETH_CHANGE_24H": {"scope": "ETHUSDT", "stale": 5},

    "BTC_FUNDING": {"scope": "BTCUSDT", "stale": 20},
    "BTC_OI": {"scope": "BTCUSDT", "stale": 20},
    "BTC_LONG_SHORT": {"scope": "BTCUSDT", "stale": 20},

    "ETH_FUNDING": {"scope": "ETHUSDT", "stale": 20},
    "ETH_OI": {"scope": "ETHUSDT", "stale": 20},
    "ETH_LONG_SHORT": {"scope": "ETHUSDT", "stale": 20},

    "VIX": {"scope": "GLOBAL", "stale": 360},
    "DXY": {"scope": "GLOBAL", "stale": 360},
    "NASDAQ": {"scope": "GLOBAL", "stale": 360},
    "NASDAQ_CHANGE": {"scope": "GLOBAL", "stale": 360},
    "US10Y": {"scope": "GLOBAL", "stale": 720},
    "FEAR_GREED": {"scope": "GLOBAL", "stale": 1800},

    "BTC_CVD": {"scope": "BTCUSDT", "stale": 60},
    "BTC_LIQUIDATIONS": {"scope": "BTCUSDT", "stale": 60},
    "ETH_CVD": {"scope": "ETHUSDT", "stale": 60},
    "ETH_LIQUIDATIONS": {"scope": "ETHUSDT", "stale": 60},
}

ALIASES = {
    "VIX": ["vix", "volatility_index", "cboe_vix"],
    "DXY": ["dxy", "dollar_index", "dollar"],
    "NASDAQ": ["nasdaq", "ndx", "ixic"],
    "NASDAQ_CHANGE": ["nasdaq_change", "ndx_change", "ixic_change"],
    "US10Y": ["us10y", "10y", "treasury_10y", "yield"],
    "FEAR_GREED": ["fear", "fear_greed", "feargreed", "crypto_fear"],

    "BTC_CVD": ["btc_cvd", "cvd_btc", "volume_delta_btc", "btc_delta"],
    "BTC_LIQUIDATIONS": ["btc_liquidation", "btc_liq", "liquidations_btc"],
    "ETH_CVD": ["eth_cvd", "cvd_eth", "volume_delta_eth", "eth_delta"],
    "ETH_LIQUIDATIONS": ["eth_liquidation", "eth_liq", "liquidations_eth"],
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_ts(x: Any) -> Optional[datetime]:
    if not x:
        return None
    try:
        s = str(x).replace("Z", "+00:00")
        d = datetime.fromisoformat(s)
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.astimezone(timezone.utc)
    except Exception:
        return None


def age_min(ts: Any) -> Optional[float]:
    d = parse_ts(ts)
    if not d:
        return None
    return max(0.0, (datetime.now(timezone.utc) - d).total_seconds() / 60.0)


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


def qid(x: str) -> str:
    return '"' + str(x).replace('"', '""') + '"'


def connect():
    con = sqlite3.connect(DB, timeout=5)
    con.row_factory = sqlite3.Row
    return con


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
            error_count INTEGER,
            summary TEXT,
            payload TEXT
        )
    """)


def http_json(url: str, timeout: float = 4.0) -> Any:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 JoanBot Institutional Data Plane",
            "Accept": "application/json,text/plain,*/*",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read().decode("utf-8", errors="ignore")
    return json.loads(raw)


def emit(
    con,
    metric: str,
    value: Any,
    source: str,
    source_detail: str,
    ts: Optional[str] = None,
    payload: Optional[Dict[str, Any]] = None,
):
    cfg = METRICS[metric]
    scope = cfg["scope"]
    stale = float(cfg["stale"])
    ts = ts or now_iso()
    v = fnum(value)
    value_text = None if value is None else str(value)

    a = age_min(ts)
    if v is None:
        status = "MISS"
        quality = 0.0
    elif a is not None and a > stale:
        status = "STALE"
        quality = max(0.05, 1.0 - min(a / max(stale, 1), 5.0) / 5.0)
    else:
        status = "LIVE"
        quality = 1.0

    pl = json.dumps(payload or {}, sort_keys=True)

    con.execute(f"""
        INSERT OR REPLACE INTO {qid(LATEST)}
        (metric, scope, ts, value, value_text, status, age_min, stale_limit_min,
         quality, source, source_detail, payload)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        metric, scope, ts, v, value_text, status, a, stale,
        quality, source, source_detail, pl,
    ))

    con.execute(f"""
        INSERT INTO {qid(HISTORY)}
        (metric, scope, ts, value, value_text, status, age_min, stale_limit_min,
         quality, source, source_detail, payload)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        metric, scope, ts, v, value_text, status, a, stale,
        quality, source, source_detail, pl,
    ))


def table_exists(con, t: str) -> bool:
    try:
        return con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (t,),
        ).fetchone() is not None
    except Exception:
        return False


def cols(con, t: str) -> List[str]:
    if not table_exists(con, t):
        return []
    try:
        return [r[1] for r in con.execute(f"PRAGMA table_info({qid(t)})")]
    except Exception:
        return []


def rows(con, sql: str, args=()) -> List[Dict[str, Any]]:
    try:
        return [dict(r) for r in con.execute(sql, args).fetchall()]
    except Exception:
        return []


def parse_payload(x: Any) -> Dict[str, Any]:
    if not x:
        return {}
    if isinstance(x, dict):
        return x
    try:
        d = json.loads(str(x))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def flat_json(obj: Any, prefix: str = "") -> List[Tuple[str, Any]]:
    out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            key = f"{prefix}.{k}" if prefix else str(k)
            out.extend(flat_json(v, key))
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:20]):
            key = f"{prefix}.{i}" if prefix else str(i)
            out.extend(flat_json(v, key))
    else:
        out.append((prefix, obj))
    return out


def norm(x: Any) -> str:
    return "".join(ch for ch in str(x).lower() if ch.isalnum())


def alias_hit(name: Any, aliases: List[str]) -> bool:
    n = norm(name)
    for a in aliases:
        an = norm(a)
        if not an:
            continue
        if an in n or n in an:
            return True
    return False


def candidate_tables(con) -> List[str]:
    out = []
    for r in rows(con, "SELECT name FROM sqlite_master WHERE type='table'"):
        t = r["name"]
        low = t.lower()
        if any(k in low for k in [
            "market", "macro", "derivative", "funding", "open_interest",
            "liquid", "fear", "context", "snapshot", "data"
        ]):
            out.append(t)
    return out[:80]


def db_fallback_metric(con, metric: str) -> Optional[Dict[str, Any]]:
    aliases = ALIASES.get(metric, [])
    if not aliases:
        return None

    best = None

    def consider(value, ts, table, col, path, source):
        nonlocal best
        v = fnum(value)
        if v is None:
            return
        a = age_min(ts) if ts else None
        freshness = 0.0 if a is None else max(0.0, 1.0 - min(a, 1440.0) / 1440.0)
        score = freshness + (0.3 if source == "column" else 0.15)
        item = {
            "value": v,
            "ts": ts or now_iso(),
            "score": score,
            "table": table,
            "col": col,
            "path": path,
            "source": source,
        }
        if best is None or score > best["score"]:
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
                    consider(r.get("v"), r.get("ts"), t, col, col, "column")

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
                for path, val in flat_json(payload):
                    if alias_hit(path, aliases):
                        consider(val, r.get("ts"), t, pcol, path, "payload")

    return best


def fetch_binance(con, errors: List[str]):
    for sym in ["BTCUSDT", "ETHUSDT"]:
        prefix = sym[:3]

        try:
            d = http_json(f"https://api.binance.com/api/v3/ticker/24hr?symbol={sym}")
            emit(con, f"{prefix}_PRICE", d.get("lastPrice"), "binance_spot", f"ticker24h:{sym}", payload=d)
            emit(con, f"{prefix}_CHANGE_24H", d.get("priceChangePercent"), "binance_spot", f"ticker24h:{sym}", payload=d)
        except Exception as e:
            errors.append(f"{prefix}_spot:{repr(e)}")

        try:
            d = http_json(f"https://fapi.binance.com/fapi/v1/premiumIndex?symbol={sym}")
            emit(con, f"{prefix}_FUNDING", d.get("lastFundingRate"), "binance_futures", f"premiumIndex:{sym}", payload=d)
        except Exception as e:
            errors.append(f"{prefix}_funding:{repr(e)}")

        try:
            d = http_json(f"https://fapi.binance.com/fapi/v1/openInterest?symbol={sym}")
            emit(con, f"{prefix}_OI", d.get("openInterest"), "binance_futures", f"openInterest:{sym}", payload=d)
        except Exception as e:
            errors.append(f"{prefix}_oi:{repr(e)}")

        try:
            url = (
                "https://fapi.binance.com/futures/data/globalLongShortAccountRatio?"
                + urllib.parse.urlencode({"symbol": sym, "period": "5m", "limit": "1"})
            )
            d = http_json(url)
            if isinstance(d, list) and d:
                emit(con, f"{prefix}_LONG_SHORT", d[-1].get("longShortRatio"), "binance_futures", f"longShortRatio:{sym}", payload=d[-1])
        except Exception as e:
            errors.append(f"{prefix}_longshort:{repr(e)}")


def fetch_yahoo_macro(con, errors: List[str]):
    try:
        symbols = "^VIX,DX-Y.NYB,^IXIC,^TNX"
        url = "https://query1.finance.yahoo.com/v7/finance/quote?" + urllib.parse.urlencode({"symbols": symbols})
        d = http_json(url)
        result = (((d or {}).get("quoteResponse") or {}).get("result") or [])

        by_symbol = {x.get("symbol"): x for x in result if isinstance(x, dict)}

        if "^VIX" in by_symbol:
            emit(con, "VIX", by_symbol["^VIX"].get("regularMarketPrice"), "yahoo_quote", "^VIX", payload=by_symbol["^VIX"])

        if "DX-Y.NYB" in by_symbol:
            emit(con, "DXY", by_symbol["DX-Y.NYB"].get("regularMarketPrice"), "yahoo_quote", "DX-Y.NYB", payload=by_symbol["DX-Y.NYB"])

        if "^IXIC" in by_symbol:
            emit(con, "NASDAQ", by_symbol["^IXIC"].get("regularMarketPrice"), "yahoo_quote", "^IXIC", payload=by_symbol["^IXIC"])
            emit(con, "NASDAQ_CHANGE", by_symbol["^IXIC"].get("regularMarketChangePercent"), "yahoo_quote", "^IXIC:change_pct", payload=by_symbol["^IXIC"])

        if "^TNX" in by_symbol:
            raw = fnum(by_symbol["^TNX"].get("regularMarketPrice"))
            val = raw / 10.0 if raw and raw > 20 else raw
            emit(con, "US10Y", val, "yahoo_quote", "^TNX", payload=by_symbol["^TNX"])

    except Exception as e:
        errors.append(f"yahoo_macro:{repr(e)}")


def fetch_fear_greed(con, errors: List[str]):
    try:
        d = http_json("https://api.alternative.me/fng/?limit=1&format=json")
        data = d.get("data") or []
        if data:
            emit(con, "FEAR_GREED", data[0].get("value"), "alternative_me", "fear_greed", payload=data[0])
    except Exception as e:
        errors.append(f"fear_greed:{repr(e)}")


def db_fallbacks(con, errors: List[str]):
    for metric in ["VIX", "DXY", "NASDAQ", "NASDAQ_CHANGE", "US10Y", "FEAR_GREED", "BTC_CVD", "BTC_LIQUIDATIONS", "ETH_CVD", "ETH_LIQUIDATIONS"]:
        current = rows(con, f"SELECT * FROM {qid(LATEST)} WHERE metric=? AND status='LIVE' LIMIT 1", (metric,))
        if current:
            continue

        item = db_fallback_metric(con, metric)
        if item:
            emit(
                con,
                metric,
                item["value"],
                "db_fallback",
                f"{item['table']}.{item['col']}:{item['path']}",
                ts=item.get("ts"),
                payload=item,
            )


def ensure_missing(con):
    for metric in METRICS:
        r = rows(con, f"SELECT metric FROM {qid(LATEST)} WHERE metric=? LIMIT 1", (metric,))
        if not r:
            emit(con, metric, None, "missing", "no_source_found", ts=now_iso(), payload={})


def main():
    OUT.mkdir(parents=True, exist_ok=True)

    if not DB.exists():
        raise SystemExit("DB_MISSING")

    errors = []

    con = connect()
    create_tables(con)

    qc = con.execute("PRAGMA quick_check").fetchone()[0]

    fetch_binance(con, errors)
    fetch_yahoo_macro(con, errors)
    fetch_fear_greed(con, errors)
    db_fallbacks(con, errors)
    ensure_missing(con)

    summary_rows = rows(con, f"""
        SELECT status, COUNT(*) AS n
        FROM {qid(LATEST)}
        GROUP BY status
    """)

    counts = {r["status"]: int(r["n"]) for r in summary_rows}

    live = counts.get("LIVE", 0)
    stale = counts.get("STALE", 0)
    miss = counts.get("MISS", 0)

    summary = "OK" if live >= 8 and miss <= 4 else "DEGRADED" if live >= 4 else "BAD"

    payload = {
        "version": VERSION,
        "db_quick_check": qc,
        "counts": counts,
        "errors": errors,
    }

    con.execute(f"""
        INSERT INTO {qid(HEALTH)}
        (ts, version, live_count, stale_count, miss_count, error_count, summary, payload)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        now_iso(), VERSION, live, stale, miss, len(errors), summary, json.dumps(payload, sort_keys=True)
    ))

    con.commit()

    latest = rows(con, f"""
        SELECT metric, scope, value, status, age_min, source, source_detail
        FROM {qid(LATEST)}
        ORDER BY metric
    """)

    con.close()

    (OUT / "data_plane_latest.json").write_text(json.dumps(latest, indent=2, sort_keys=True))
    (OUT / "data_plane_health.json").write_text(json.dumps(payload, indent=2, sort_keys=True))

    print("===== V18.9 DATA PLANE =====")
    print("db:", qc)
    print("summary:", summary)
    print("live:", live, "stale:", stale, "miss:", miss, "errors:", len(errors))
    for r in latest:
        print(
            f"{r['metric']:<18} {r['status']:<6} value={r['value']} age={r['age_min']} "
            f"source={r['source']} {r['source_detail']}"
        )

    if errors:
        print("===== ERRORS =====")
        for e in errors[:20]:
            print(e)

    return 0 if summary in {"OK", "DEGRADED"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
