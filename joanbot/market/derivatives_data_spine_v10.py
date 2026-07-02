from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from joanbot.config import CFG
from joanbot.storage.db import get_db
from joanbot.utils import utc_now_iso

VERSION = "DERIVATIVES_DATA_SPINE_V10"


def fnum(x: Any, default: float = 0.0) -> float:
    try:
        if x is None:
            return default
        return float(x)
    except Exception:
        return default


def inum(x: Any, default: int = 0) -> int:
    try:
        if x is None:
            return default
        return int(float(x))
    except Exception:
        return default


def js(x: Any) -> str:
    return json.dumps(x, separators=(",", ":"), ensure_ascii=False, default=str)


def parse_ts(ts: Any) -> Optional[datetime]:
    try:
        d = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.astimezone(timezone.utc)
    except Exception:
        return None


def payload(row: Dict[str, Any]) -> Dict[str, Any]:
    try:
        p = row.get("payload")
        if isinstance(p, str) and p:
            v = json.loads(p)
            return v if isinstance(v, dict) else {}
        return p if isinstance(p, dict) else {}
    except Exception:
        return {}


class DerivativesDataSpineV10:
    """
    Free pre-API derivatives data spine.

    Reads existing Binance-derived tables already populated by MarketDataHub:
    - derivatives_snapshots
    - orderflow_snapshots
    - market_snapshots

    Writes normalized institutional snapshots:
    - derivatives_data_spine_v10
    - latest_derivatives_data_spine_v10

    No paid API. No trade execution. No mutation of legacy trading tables.
    """

    def __init__(self, db=None, symbols=None) -> None:
        self.db = db or get_db()
        self.symbols = tuple(symbols or CFG.symbols)

    def ensure_schema(self) -> None:
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS derivatives_data_spine_v10 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                refresh_id INTEGER NOT NULL,
                ts TEXT NOT NULL,
                version TEXT NOT NULL,
                symbol TEXT NOT NULL,

                price REAL NOT NULL,
                funding_rate REAL NOT NULL,
                open_interest REAL NOT NULL,
                oi_change_5m REAL NOT NULL,
                oi_change_30m REAL NOT NULL,
                long_short_ratio REAL NOT NULL,
                top_long_short_ratio REAL NOT NULL,
                taker_buy_sell_ratio REAL NOT NULL,
                basis_bps REAL NOT NULL,

                spread_bps REAL NOT NULL,
                imbalance_25bps REAL NOT NULL,
                wall_pressure REAL NOT NULL,
                cvd_ratio REAL NOT NULL,

                long_liq_usd REAL NOT NULL,
                short_liq_usd REAL NOT NULL,
                liq_imbalance REAL NOT NULL,

                sample_n INTEGER NOT NULL,
                data_age_sec REAL NOT NULL,
                data_quality REAL NOT NULL,
                data_state TEXT NOT NULL,
                hard_vetoes TEXT NOT NULL,
                payload TEXT NOT NULL
            );
        """)

        self.db.execute("DROP VIEW IF EXISTS latest_derivatives_data_spine_v10;")
        self.db.execute("""
            CREATE VIEW latest_derivatives_data_spine_v10 AS
            SELECT *
            FROM derivatives_data_spine_v10
            WHERE refresh_id = (
                SELECT MAX(refresh_id)
                FROM derivatives_data_spine_v10
            );
        """)

    def qmany(self, sql: str, params=()) -> List[Dict[str, Any]]:
        try:
            return [dict(r) for r in self.db.query(sql, params)]
        except Exception:
            return []

    def q1(self, sql: str, params=()) -> Dict[str, Any]:
        rows = self.qmany(sql, params)
        return rows[0] if rows else {}

    def last_rows(self, table: str, symbol: str, limit: int = 120) -> List[Dict[str, Any]]:
        return self.qmany(
            f"SELECT * FROM {table} WHERE symbol=? ORDER BY id DESC LIMIT ?;",
            (symbol, int(limit)),
        )

    def age_sec(self, row: Dict[str, Any]) -> float:
        dt = parse_ts(row.get("ts"))
        if not dt:
            return 999999.0
        return max(0.0, (datetime.now(timezone.utc) - dt).total_seconds())

    def older_value(self, rows: List[Dict[str, Any]], key: str, seconds_back: int) -> float:
        now = datetime.now(timezone.utc)
        best = None
        best_delta = 10**12
        for r in rows:
            dt = parse_ts(r.get("ts"))
            if not dt:
                continue
            age = (now - dt).total_seconds()
            if age >= seconds_back:
                delta = abs(age - seconds_back)
                if delta < best_delta:
                    best = r
                    best_delta = delta
        if best is None and rows:
            best = rows[-1]
        return fnum((best or {}).get(key))

    def normalized_symbol(self, symbol: str, refresh_id: int) -> Dict[str, Any]:
        der_rows = self.last_rows("derivatives_snapshots", symbol, 180)
        of_rows = self.last_rows("orderflow_snapshots", symbol, 60)
        mkt = self.q1("SELECT * FROM market_snapshots WHERE symbol=? ORDER BY id DESC LIMIT 1;", (symbol,))

        latest = der_rows[0] if der_rows else {}
        latest_of = of_rows[0] if of_rows else {}
        p_der = payload(latest)
        p_of = payload(latest_of)

        price = fnum(mkt.get("price")) or fnum(p_der.get("mark_price")) or fnum(p_der.get("index_price"))
        oi = fnum(latest.get("open_interest"))
        oi_5 = self.older_value(der_rows, "open_interest", 5 * 60)
        oi_30 = self.older_value(der_rows, "open_interest", 30 * 60)

        oi_change_5m = (oi / oi_5 - 1.0) * 100.0 if oi > 0 and oi_5 > 0 else 0.0
        oi_change_30m = (oi / oi_30 - 1.0) * 100.0 if oi > 0 and oi_30 > 0 else 0.0

        long_liq = fnum(p_of.get("long_liq_usd"))
        short_liq = fnum(p_of.get("short_liq_usd"))
        liq_total = long_liq + short_liq
        liq_imbalance = fnum(p_of.get("liq_imbalance"), (short_liq - long_liq) / liq_total if liq_total else 0.0)

        age = min(self.age_sec(latest), self.age_sec(latest_of), self.age_sec(mkt))
        sample_n = len(der_rows)

        vetoes: List[str] = []
        quality = 100.0
        if not latest:
            quality -= 60.0
            vetoes.append("NO_DERIVATIVES_SNAPSHOT")
        if not latest_of:
            quality -= 20.0
            vetoes.append("NO_ORDERFLOW_SNAPSHOT")
        if age > 600:
            quality -= 35.0
            vetoes.append("DERIVATIVES_DATA_STALE_GT_10M")
        elif age > 300:
            quality -= 15.0
            vetoes.append("DERIVATIVES_DATA_STALE_GT_5M")
        if sample_n < 3:
            quality -= 20.0
            vetoes.append("DERIVATIVES_SAMPLE_LT_3")
        if oi <= 0:
            quality -= 20.0
            vetoes.append("OPEN_INTEREST_MISSING")
        if fnum(latest.get("long_short"), 1.0) <= 0:
            quality -= 10.0
            vetoes.append("LONG_SHORT_RATIO_INVALID")
        if p_der.get("endpoint_errors"):
            quality -= min(25.0, 5.0 * len(p_der.get("endpoint_errors") or []))
            vetoes.append("BINANCE_ENDPOINT_ERRORS")

        quality = max(0.0, min(100.0, quality))
        if quality >= 70:
            state = "DERIVATIVES_DATA_READY"
        elif quality >= 45:
            state = "DERIVATIVES_DATA_DEGRADED"
        else:
            state = "DERIVATIVES_DATA_NOT_READY"

        row = {
            "refresh_id": refresh_id,
            "ts": utc_now_iso(),
            "version": VERSION,
            "symbol": symbol,
            "price": price,
            "funding_rate": fnum(latest.get("funding")),
            "open_interest": oi,
            "oi_change_5m": oi_change_5m,
            "oi_change_30m": oi_change_30m,
            "long_short_ratio": fnum(latest.get("long_short"), 1.0),
            "top_long_short_ratio": fnum(latest.get("top_long_short"), 1.0),
            "taker_buy_sell_ratio": fnum(latest.get("taker_buy_ratio"), 1.0),
            "basis_bps": fnum(latest.get("basis_bps")),
            "spread_bps": fnum(latest_of.get("spread_bps"), 999.0),
            "imbalance_25bps": fnum(latest_of.get("imbalance_25bps")),
            "wall_pressure": fnum(latest_of.get("wall_pressure")),
            "cvd_ratio": fnum(p_of.get("cvd_ratio")),
            "long_liq_usd": long_liq,
            "short_liq_usd": short_liq,
            "liq_imbalance": liq_imbalance,
            "sample_n": sample_n,
            "data_age_sec": age,
            "data_quality": quality,
            "data_state": state,
            "hard_vetoes": vetoes,
            "payload": {
                "source": VERSION,
                "free_binance_only": True,
                "latest_derivatives_payload": p_der,
                "latest_orderflow_payload": p_of,
            },
        }
        return row

    def insert_row(self, r: Dict[str, Any]) -> None:
        self.db.execute("""
            INSERT INTO derivatives_data_spine_v10 (
                refresh_id, ts, version, symbol,
                price, funding_rate, open_interest, oi_change_5m, oi_change_30m,
                long_short_ratio, top_long_short_ratio, taker_buy_sell_ratio, basis_bps,
                spread_bps, imbalance_25bps, wall_pressure, cvd_ratio,
                long_liq_usd, short_liq_usd, liq_imbalance,
                sample_n, data_age_sec, data_quality, data_state, hard_vetoes, payload
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """, (
            int(r["refresh_id"]), r["ts"], r["version"], r["symbol"],
            fnum(r["price"]), fnum(r["funding_rate"]), fnum(r["open_interest"]), fnum(r["oi_change_5m"]), fnum(r["oi_change_30m"]),
            fnum(r["long_short_ratio"]), fnum(r["top_long_short_ratio"]), fnum(r["taker_buy_sell_ratio"]), fnum(r["basis_bps"]),
            fnum(r["spread_bps"]), fnum(r["imbalance_25bps"]), fnum(r["wall_pressure"]), fnum(r["cvd_ratio"]),
            fnum(r["long_liq_usd"]), fnum(r["short_liq_usd"]), fnum(r["liq_imbalance"]),
            inum(r["sample_n"]), fnum(r["data_age_sec"]), fnum(r["data_quality"]), r["data_state"], js(r["hard_vetoes"]), js(r["payload"]),
        ))

    def refresh(self) -> Dict[str, Any]:
        self.ensure_schema()
        refresh_id = int(datetime.now(timezone.utc).timestamp() * 1000)
        rows = []
        for symbol in self.symbols:
            row = self.normalized_symbol(symbol, refresh_id)
            self.insert_row(row)
            rows.append(row)
        ready = sum(1 for r in rows if r["data_state"] == "DERIVATIVES_DATA_READY")
        return {
            "version": VERSION,
            "refresh_id": refresh_id,
            "symbols": len(rows),
            "ready_symbols": ready,
            "states": {r["symbol"]: r["data_state"] for r in rows},
            "quality": {r["symbol"]: round(fnum(r["data_quality"]), 2) for r in rows},
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    engine = DerivativesDataSpineV10()
    if args.refresh:
        print(json.dumps(engine.refresh(), indent=2, sort_keys=True, default=str))
    else:
        engine.ensure_schema()
        print(json.dumps({"version": VERSION, "schema": "ok"}, indent=2))


if __name__ == "__main__":
    main()
