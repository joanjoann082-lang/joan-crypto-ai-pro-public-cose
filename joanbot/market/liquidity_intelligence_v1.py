from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sqlite3
import statistics
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

VERSION = "LIQUIDITY_INTELLIGENCE_V1_INSTITUTIONAL_BOUNDED"

DB_PATH = Path(os.environ.get("JOANBOT_DB", "data/joanbot_v14.sqlite"))

DEFAULT_SYMBOLS = ("BTCUSDT", "ETHUSDT")
BINANCE_FSTREAM_COMBINED = "wss://fstream.binance.com/stream?streams="

DEFAULT_RETENTION_EVENTS_PER_SYMBOL = 2000
DEFAULT_RETENTION_FEATURES_PER_SYMBOL = 600

SOURCE = "BINANCE_USDM_FORCE_ORDER_STREAM"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def now_ms() -> int:
    return int(time.time() * 1000)


def fnum(x: Any, default: float = 0.0) -> float:
    try:
        if x is None:
            return default
        return float(x)
    except Exception:
        return default


def compact_error(exc: Exception) -> str:
    return f"{type(exc).__name__}: {str(exc)[:260]}"


def event_hash(parts: Iterable[Any]) -> str:
    raw = "|".join(str(p) for p in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:24]


def percentile(vals: List[float], p: float) -> float:
    if not vals:
        return 0.0
    vals = sorted(vals)
    if len(vals) == 1:
        return vals[0]
    k = (len(vals) - 1) * p
    lo = math.floor(k)
    hi = math.ceil(k)
    if lo == hi:
        return vals[int(k)]
    return vals[lo] * (hi - k) + vals[hi] * (k - lo)


@dataclass
class NormalizedLiquidationEvent:
    event_id: str
    recv_ts: str
    event_ms: int
    symbol: str
    side: str
    liquidation_side: str
    price: float
    qty: float
    usd: float
    source: str
    payload: Dict[str, Any]


@dataclass
class LiquidityFeatureSnapshot:
    version: str
    ts: str
    symbol: str
    source: str
    lookback_min: int
    data_status: str
    ref_price: Optional[float]

    event_count: int
    buy_liq_usd: float
    sell_liq_usd: float
    total_liq_usd: float
    net_liq_usd: float
    imbalance: float

    decayed_buy_liq_usd: float
    decayed_sell_liq_usd: float
    decayed_imbalance: float

    short_squeeze_pressure: float
    long_flush_pressure: float
    stress_score: float

    max_event_usd: float
    p95_event_usd: float
    latest_event_age_sec: Optional[float]

    dominant_side: str
    dominant_bucket_bps: Optional[int]
    dominant_bucket_usd: float

    nearest_above_bucket_bps: Optional[int]
    nearest_above_usd: float
    nearest_below_bucket_bps: Optional[int]
    nearest_below_usd: float

    source_health: str
    payload: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class LiquidityIntelligenceV1:
    """
    Institutional bounded liquidation intelligence.

    Owns:
    - Binance force-order websocket ingestion.
    - Normalized liquidation event storage.
    - Compact liquidity feature snapshots.
    - Source health.
    - Hard retention.

    Does not own:
    - Trade decisions.
    - Risk sizing.
    - Execution permission.
    - Position management.
    - Dashboard rendering.
    - Telegram commands.
    """

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path

    def connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(str(self.db_path), timeout=10)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA busy_timeout=10000;")
        return con

    def ensure_schema(self, con: sqlite3.Connection) -> None:
        con.execute("""
            CREATE TABLE IF NOT EXISTS liquidity_liquidation_events_v1 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL UNIQUE,
                recv_ts TEXT NOT NULL,
                event_ms INTEGER NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                liquidation_side TEXT NOT NULL,
                price REAL NOT NULL,
                qty REAL NOT NULL,
                usd REAL NOT NULL,
                source TEXT NOT NULL,
                payload TEXT NOT NULL
            );
        """)

        con.execute("""
            CREATE INDEX IF NOT EXISTS idx_liq_events_symbol_event_ms_v1
            ON liquidity_liquidation_events_v1(symbol, event_ms);
        """)

        con.execute("""
            CREATE TABLE IF NOT EXISTS liquidity_features_v1 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                symbol TEXT NOT NULL,
                source TEXT NOT NULL,
                version TEXT NOT NULL,
                lookback_min INTEGER NOT NULL,
                data_status TEXT NOT NULL,
                ref_price REAL,

                event_count INTEGER NOT NULL,
                buy_liq_usd REAL NOT NULL,
                sell_liq_usd REAL NOT NULL,
                total_liq_usd REAL NOT NULL,
                net_liq_usd REAL NOT NULL,
                imbalance REAL NOT NULL,

                decayed_buy_liq_usd REAL NOT NULL,
                decayed_sell_liq_usd REAL NOT NULL,
                decayed_imbalance REAL NOT NULL,

                short_squeeze_pressure REAL NOT NULL,
                long_flush_pressure REAL NOT NULL,
                stress_score REAL NOT NULL,

                max_event_usd REAL NOT NULL,
                p95_event_usd REAL NOT NULL,
                latest_event_age_sec REAL,

                dominant_side TEXT NOT NULL,
                dominant_bucket_bps INTEGER,
                dominant_bucket_usd REAL NOT NULL,

                nearest_above_bucket_bps INTEGER,
                nearest_above_usd REAL NOT NULL,
                nearest_below_bucket_bps INTEGER,
                nearest_below_usd REAL NOT NULL,

                source_health TEXT NOT NULL,
                payload TEXT NOT NULL
            );
        """)

        con.execute("""
            CREATE INDEX IF NOT EXISTS idx_liq_features_symbol_id_v1
            ON liquidity_features_v1(symbol, id);
        """)

        con.execute("""
            CREATE TABLE IF NOT EXISTS liquidity_source_health_v1 (
                source TEXT PRIMARY KEY,
                updated_at TEXT NOT NULL,
                status TEXT NOT NULL,
                message TEXT NOT NULL,
                latency_ms REAL
            );
        """)

    def record_source_health(self, con: sqlite3.Connection, status: str, message: str, latency_ms: Optional[float] = None) -> None:
        con.execute("""
            INSERT OR REPLACE INTO liquidity_source_health_v1 (
                source, updated_at, status, message, latency_ms
            )
            VALUES (?, ?, ?, ?, ?);
        """, (SOURCE, utc_now_iso(), status, message[:500], latency_ms))

    def latest_price(self, con: sqlite3.Connection, symbol: str) -> Optional[float]:
        try:
            row = con.execute("""
                SELECT price
                FROM market_snapshots
                WHERE symbol=?
                ORDER BY rowid DESC
                LIMIT 1;
            """, (symbol,)).fetchone()
            if not row:
                return None
            price = fnum(row["price"], 0.0)
            return price if price > 0 else None
        except Exception:
            return None

    def parse_binance_force_order(self, msg: Dict[str, Any]) -> Optional[NormalizedLiquidationEvent]:
        data = msg.get("data", msg)
        order = data.get("o", data)

        symbol = str(order.get("s") or "").upper()
        side = str(order.get("S") or "").upper()

        if symbol not in DEFAULT_SYMBOLS:
            return None

        price = fnum(order.get("ap"), 0.0) or fnum(order.get("p"), 0.0)
        qty = fnum(order.get("z"), 0.0) or fnum(order.get("l"), 0.0) or fnum(order.get("q"), 0.0)
        event_ms = int(fnum(order.get("T"), fnum(data.get("E"), now_ms())))

        if price <= 0 or qty <= 0 or event_ms <= 0:
            return None

        usd = abs(price * qty)

        liquidation_side = "SHORT_LIQUIDATION" if side == "BUY" else "LONG_LIQUIDATION" if side == "SELL" else "UNKNOWN"

        eid = event_hash([SOURCE, symbol, event_ms, side, round(price, 2), round(qty, 6), round(usd, 2)])

        return NormalizedLiquidationEvent(
            event_id=eid,
            recv_ts=utc_now_iso(),
            event_ms=event_ms,
            symbol=symbol,
            side=side,
            liquidation_side=liquidation_side,
            price=round(price, 8),
            qty=round(qty, 8),
            usd=round(usd, 4),
            source=SOURCE,
            payload={
                "binance_event_type": data.get("e"),
                "order_type": order.get("o"),
                "order_status": order.get("X"),
                "raw_side": side,
            },
        )

    def insert_event(self, con: sqlite3.Connection, ev: NormalizedLiquidationEvent) -> bool:
        try:
            con.execute("""
                INSERT OR IGNORE INTO liquidity_liquidation_events_v1 (
                    event_id, recv_ts, event_ms, symbol, side, liquidation_side,
                    price, qty, usd, source, payload
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """, (
                ev.event_id,
                ev.recv_ts,
                ev.event_ms,
                ev.symbol,
                ev.side,
                ev.liquidation_side,
                ev.price,
                ev.qty,
                ev.usd,
                ev.source,
                json.dumps(ev.payload, separators=(",", ":"), ensure_ascii=False),
            ))
            return True
        except Exception:
            return False

    def apply_retention(self, con: sqlite3.Connection, symbols: List[str], events_per_symbol: int, features_per_symbol: int) -> None:
        events_per_symbol = max(200, int(events_per_symbol))
        features_per_symbol = max(100, int(features_per_symbol))

        for symbol in symbols:
            con.execute("""
                DELETE FROM liquidity_liquidation_events_v1
                WHERE symbol=?
                  AND id NOT IN (
                    SELECT id
                    FROM liquidity_liquidation_events_v1
                    WHERE symbol=?
                    ORDER BY id DESC
                    LIMIT ?
                  );
            """, (symbol, symbol, events_per_symbol))

            con.execute("""
                DELETE FROM liquidity_features_v1
                WHERE symbol=?
                  AND id NOT IN (
                    SELECT id
                    FROM liquidity_features_v1
                    WHERE symbol=?
                    ORDER BY id DESC
                    LIMIT ?
                  );
            """, (symbol, symbol, features_per_symbol))

    def rows_for_window(self, con: sqlite3.Connection, symbol: str, lookback_min: int) -> List[sqlite3.Row]:
        cutoff = now_ms() - lookback_min * 60 * 1000
        return list(con.execute("""
            SELECT *
            FROM liquidity_liquidation_events_v1
            WHERE symbol=?
              AND event_ms >= ?
            ORDER BY event_ms ASC;
        """, (symbol, cutoff)).fetchall())

    def build_features(self, con: sqlite3.Connection, symbol: str, lookback_min: int, bucket_bps: int = 25) -> LiquidityFeatureSnapshot:
        rows = self.rows_for_window(con, symbol, lookback_min)
        ref_price = self.latest_price(con, symbol)

        if ref_price is None and rows:
            ref_price = fnum(rows[-1]["price"], 0.0) or None

        buy_usd = 0.0
        sell_usd = 0.0
        decayed_buy = 0.0
        decayed_sell = 0.0
        usd_vals: List[float] = []
        latest_event_ms = 0

        buckets: Dict[int, float] = {}
        bucket_sides: Dict[int, Dict[str, float]] = {}

        half_life_sec = max(60.0, lookback_min * 60 / 2)
        current_ms = now_ms()

        for r in rows:
            side = str(r["side"]).upper()
            price = fnum(r["price"], 0.0)
            usd = fnum(r["usd"], 0.0)
            event_ms = int(r["event_ms"])

            if usd <= 0:
                continue

            latest_event_ms = max(latest_event_ms, event_ms)
            age_sec = max(0.0, (current_ms - event_ms) / 1000.0)
            decay = 0.5 ** (age_sec / half_life_sec)

            usd_vals.append(usd)

            if side == "BUY":
                buy_usd += usd
                decayed_buy += usd * decay
            elif side == "SELL":
                sell_usd += usd
                decayed_sell += usd * decay

            if ref_price and ref_price > 0 and price > 0:
                bps = int(round(((price / ref_price) - 1.0) * 10000 / bucket_bps) * bucket_bps)
                buckets[bps] = buckets.get(bps, 0.0) + usd
                bucket_sides.setdefault(bps, {"BUY": 0.0, "SELL": 0.0})
                if side in ("BUY", "SELL"):
                    bucket_sides[bps][side] += usd

        total = buy_usd + sell_usd
        net = buy_usd - sell_usd
        imbalance = net / total if total > 0 else 0.0

        decayed_total = decayed_buy + decayed_sell
        decayed_imbalance = (decayed_buy - decayed_sell) / decayed_total if decayed_total > 0 else 0.0

        latest_age = None
        if latest_event_ms > 0:
            latest_age = max(0.0, (current_ms - latest_event_ms) / 1000.0)

        dominant_bucket = None
        dominant_bucket_usd = 0.0
        if buckets:
            dominant_bucket = max(buckets.keys(), key=lambda k: buckets[k])
            dominant_bucket_usd = buckets[dominant_bucket]

        above = {k: v for k, v in buckets.items() if k > 0}
        below = {k: v for k, v in buckets.items() if k < 0}

        nearest_above_bucket = min(above.keys(), key=lambda k: abs(k)) if above else None
        nearest_below_bucket = max(below.keys(), key=lambda k: abs(k)) if below else None

        dominant_side = "NEUTRAL"
        if total > 0:
            dominant_side = "SHORT_SQUEEZE_PRESSURE" if buy_usd > sell_usd else "LONG_FLUSH_PRESSURE" if sell_usd > buy_usd else "NEUTRAL"

        stress_score = 0.0
        if total > 0:
            stress_score = min(100.0, math.log10(1.0 + total / 1000.0) * 18.0 + min(25.0, len(rows) * 1.5))

        if not rows:
            data_status = "EMPTY_OK"
            source_health = "NO_RECENT_LIQUIDATIONS"
        elif latest_age is not None and latest_age > lookback_min * 60:
            data_status = "STALE"
            source_health = "STALE_EVENTS"
        else:
            data_status = "OK"
            source_health = "OK"

        return LiquidityFeatureSnapshot(
            version=VERSION,
            ts=utc_now_iso(),
            symbol=symbol,
            source=SOURCE,
            lookback_min=lookback_min,
            data_status=data_status,
            ref_price=ref_price,
            event_count=len(rows),
            buy_liq_usd=round(buy_usd, 4),
            sell_liq_usd=round(sell_usd, 4),
            total_liq_usd=round(total, 4),
            net_liq_usd=round(net, 4),
            imbalance=round(imbalance, 6),
            decayed_buy_liq_usd=round(decayed_buy, 4),
            decayed_sell_liq_usd=round(decayed_sell, 4),
            decayed_imbalance=round(decayed_imbalance, 6),
            short_squeeze_pressure=round(buy_usd / total if total > 0 else 0.0, 6),
            long_flush_pressure=round(sell_usd / total if total > 0 else 0.0, 6),
            stress_score=round(stress_score, 4),
            max_event_usd=round(max(usd_vals), 4) if usd_vals else 0.0,
            p95_event_usd=round(percentile(usd_vals, 0.95), 4) if usd_vals else 0.0,
            latest_event_age_sec=round(latest_age, 2) if latest_age is not None else None,
            dominant_side=dominant_side,
            dominant_bucket_bps=dominant_bucket,
            dominant_bucket_usd=round(dominant_bucket_usd, 4),
            nearest_above_bucket_bps=nearest_above_bucket,
            nearest_above_usd=round(above.get(nearest_above_bucket, 0.0), 4) if nearest_above_bucket is not None else 0.0,
            nearest_below_bucket_bps=nearest_below_bucket,
            nearest_below_usd=round(below.get(nearest_below_bucket, 0.0), 4) if nearest_below_bucket is not None else 0.0,
            source_health=source_health,
            payload={
                "interpretation": {
                    "BUY": "possible short liquidation pressure",
                    "SELL": "possible long liquidation pressure",
                },
                "bucket_bps": bucket_bps,
                "event_usd_mean": round(statistics.mean(usd_vals), 4) if usd_vals else 0.0,
                "event_usd_median": round(statistics.median(usd_vals), 4) if usd_vals else 0.0,
                "top_buckets": sorted(
                    [{"bucket_bps": k, "usd": round(v, 4), "sides": bucket_sides.get(k, {})} for k, v in buckets.items()],
                    key=lambda x: x["usd"],
                    reverse=True,
                )[:8],
            },
        )

    def insert_features(self, con: sqlite3.Connection, snap: LiquidityFeatureSnapshot) -> None:
        con.execute("""
            INSERT INTO liquidity_features_v1 (
                ts, symbol, source, version, lookback_min, data_status, ref_price,
                event_count, buy_liq_usd, sell_liq_usd, total_liq_usd, net_liq_usd, imbalance,
                decayed_buy_liq_usd, decayed_sell_liq_usd, decayed_imbalance,
                short_squeeze_pressure, long_flush_pressure, stress_score,
                max_event_usd, p95_event_usd, latest_event_age_sec,
                dominant_side, dominant_bucket_bps, dominant_bucket_usd,
                nearest_above_bucket_bps, nearest_above_usd,
                nearest_below_bucket_bps, nearest_below_usd,
                source_health, payload
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """, (
            snap.ts, snap.symbol, snap.source, snap.version, snap.lookback_min, snap.data_status, snap.ref_price,
            snap.event_count, snap.buy_liq_usd, snap.sell_liq_usd, snap.total_liq_usd, snap.net_liq_usd, snap.imbalance,
            snap.decayed_buy_liq_usd, snap.decayed_sell_liq_usd, snap.decayed_imbalance,
            snap.short_squeeze_pressure, snap.long_flush_pressure, snap.stress_score,
            snap.max_event_usd, snap.p95_event_usd, snap.latest_event_age_sec,
            snap.dominant_side, snap.dominant_bucket_bps, snap.dominant_bucket_usd,
            snap.nearest_above_bucket_bps, snap.nearest_above_usd,
            snap.nearest_below_bucket_bps, snap.nearest_below_usd,
            snap.source_health, json.dumps(snap.payload, separators=(",", ":"), ensure_ascii=False),
        ))

    def snapshot_once(self, symbols: List[str], lookback_min: int, apply: bool, retention_events: int, retention_features: int) -> List[Dict[str, Any]]:
        with self.connect() as con:
            self.ensure_schema(con)

            snaps = [self.build_features(con, s, lookback_min) for s in symbols]

            if apply:
                for snap in snaps:
                    self.insert_features(con, snap)
                self.apply_retention(con, symbols, retention_events, retention_features)
                con.commit()

            return [s.to_dict() for s in snaps]

    def stream(self, symbols: List[str], seconds: int, lookback_min: int, retention_events: int, retention_features: int, apply: bool) -> Dict[str, Any]:
        import websocket

        streams = "/".join(f"{s.lower()}@forceOrder" for s in symbols)
        url = BINANCE_FSTREAM_COMBINED + streams

        inserted = 0
        received = 0
        started = time.time()
        last_snapshot = 0.0
        last_retention = 0.0

        with self.connect() as con:
            self.ensure_schema(con)

            try:
                ws = websocket.create_connection(url, timeout=10)
                ws.settimeout(5)
                self.record_source_health(con, "CONNECTED", "websocket connected")
                con.commit()
            except Exception as exc:
                self.record_source_health(con, "SOURCE_ERROR", compact_error(exc))
                con.commit()
                return {
                    "status": "SOURCE_ERROR",
                    "message": compact_error(exc),
                    "received": received,
                    "inserted": inserted,
                }

            try:
                while time.time() - started < seconds:
                    try:
                        raw = ws.recv()
                    except Exception as exc:
                        self.record_source_health(con, "RECV_TIMEOUT_OR_ERROR", compact_error(exc))
                        con.commit()
                        continue

                    received += 1

                    try:
                        msg = json.loads(raw)
                        ev = self.parse_binance_force_order(msg)
                    except Exception:
                        ev = None

                    if ev:
                        ok = self.insert_event(con, ev)
                        inserted += 1 if ok else 0

                    if time.time() - last_snapshot >= 20:
                        for symbol in symbols:
                            snap = self.build_features(con, symbol, lookback_min)
                            if apply:
                                self.insert_features(con, snap)
                        last_snapshot = time.time()

                    if time.time() - last_retention >= 60:
                        if apply:
                            self.apply_retention(con, symbols, retention_events, retention_features)
                        last_retention = time.time()

                    if apply:
                        con.commit()

                if apply:
                    self.apply_retention(con, symbols, retention_events, retention_features)
                    con.commit()

                self.record_source_health(con, "OK", f"stream completed seconds={seconds} received={received} inserted={inserted}")
                con.commit()

            finally:
                try:
                    ws.close()
                except Exception:
                    pass

        return {
            "status": "OK",
            "received": received,
            "inserted": inserted,
            "seconds": round(time.time() - started, 2),
        }

    def self_test(self) -> None:
        symbols = ["BTCUSDT", "ETHUSDT"]

        with self.connect() as con:
            self.ensure_schema(con)

            base = now_ms()
            synthetic = [
                ("BTCUSDT", "BUY", 60000, 0.4, base - 60000),
                ("BTCUSDT", "SELL", 59800, 0.2, base - 30000),
                ("ETHUSDT", "SELL", 1550, 12, base - 40000),
                ("ETHUSDT", "BUY", 1570, 4, base - 20000),
            ]

            for symbol, side, price, qty, event_ms in synthetic:
                liq_side = "SHORT_LIQUIDATION" if side == "BUY" else "LONG_LIQUIDATION"
                ev = NormalizedLiquidationEvent(
                    event_id=event_hash(["SELF_TEST", symbol, side, price, qty, event_ms]),
                    recv_ts=utc_now_iso(),
                    event_ms=event_ms,
                    symbol=symbol,
                    side=side,
                    liquidation_side=liq_side,
                    price=price,
                    qty=qty,
                    usd=price * qty,
                    source="SELF_TEST",
                    payload={"self_test": True},
                )
                self.insert_event(con, ev)

            for symbol in symbols:
                snap = self.build_features(con, symbol, 15)
                assert snap.symbol == symbol
                assert snap.event_count >= 1
                assert snap.total_liq_usd > 0
                print(symbol, "SELF_TEST_OK", snap.to_dict())

            con.commit()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS))
    parser.add_argument("--lookback-min", type=int, default=15)
    parser.add_argument("--retention-events", type=int, default=DEFAULT_RETENTION_EVENTS_PER_SYMBOL)
    parser.add_argument("--retention-features", type=int, default=DEFAULT_RETENTION_FEATURES_PER_SYMBOL)
    parser.add_argument("--init", action="store_true")
    parser.add_argument("--snapshot", action="store_true")
    parser.add_argument("--stream-seconds", type=int, default=0)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    engine = LiquidityIntelligenceV1()

    if args.self_test:
        engine.self_test()
        print("LIQUIDITY_INTELLIGENCE_SELF_TEST_OK")
        return

    if args.init:
        with engine.connect() as con:
            engine.ensure_schema(con)
            con.commit()
        print("LIQUIDITY_INTELLIGENCE_SCHEMA_OK")

    if args.stream_seconds > 0:
        res = engine.stream(
            symbols=symbols,
            seconds=args.stream_seconds,
            lookback_min=args.lookback_min,
            retention_events=args.retention_events,
            retention_features=args.retention_features,
            apply=args.apply,
        )
        print("STREAM_RESULT", json.dumps(res, sort_keys=True))

    if args.snapshot:
        snaps = engine.snapshot_once(
            symbols=symbols,
            lookback_min=args.lookback_min,
            apply=args.apply,
            retention_events=args.retention_events,
            retention_features=args.retention_features,
        )
        for s in snaps:
            print(
                s["symbol"],
                "status=", s["data_status"],
                "events=", s["event_count"],
                "total=", s["total_liq_usd"],
                "imb=", s["imbalance"],
                "stress=", s["stress_score"],
                "dominant=", s["dominant_side"],
                "age=", s["latest_event_age_sec"],
            )


if __name__ == "__main__":
    main()
