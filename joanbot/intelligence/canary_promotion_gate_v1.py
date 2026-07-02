from __future__ import annotations

import argparse
import json
import os
import sqlite3
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional

VERSION = "CANARY_PROMOTION_GATE_V1_INSTITUTIONAL_FAIL_CLOSED"
DB_PATH = Path(os.environ.get("JOANBOT_DB", "data/joanbot_v14.sqlite"))

MAX_SOURCE_AGE_SEC = 6 * 60 * 60
CACHE_TTL_SEC = 20
REFRESH_COOLDOWN_SEC = 600

MAX_CANARY_PER_SETUP_24H = 2
MAX_GLOBAL_CANARY_24H = 4
MAX_SIZE_MULTIPLIER_CAP = 0.025
MAX_ABSOLUTE_SIZE_USD_CAP = 250.0


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def txt(x: Any) -> str:
    return "" if x is None else str(x)


def parse_iso(ts: str) -> Optional[datetime]:
    try:
        if not ts:
            return None
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return None


@dataclass
class CanaryGateVerdict:
    version: str
    ts: str
    symbol: str
    side: str
    setup: str
    allow_canary_probe: bool
    allow_direct_open: bool
    size_multiplier_cap: float
    absolute_size_usd_cap: float
    gate_status: str
    source_status: str
    source_age_sec: Optional[float]
    fail_closed: bool
    reasons: list[str]
    payload: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class CanaryPromotionGateV1:
    """
    Institutional canary gate.

    Owns:
    - reading latest_research_promotion_v1
    - fail-closed validation
    - freshness checks
    - live 24h canary limits
    - size caps

    Does not own:
    - strategy
    - final decision scoring
    - risk implementation
    - broker
    - execution
    - position management
    """

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self._cache: Dict[tuple, CanaryGateVerdict] = {}
        self._cache_ts = 0.0
        self._last_refresh_attempt = 0.0

    def connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(str(self.db_path), timeout=10)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA busy_timeout=10000;")
        return con

    def table_exists(self, con: sqlite3.Connection, name: str) -> bool:
        row = con.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','view') AND name=?",
            (name,),
        ).fetchone()
        return bool(row)

    def maybe_refresh_sources(self) -> None:
        now = time.time()
        if now - self._last_refresh_attempt < REFRESH_COOLDOWN_SEC:
            return

        self._last_refresh_attempt = now

        try:
            from .evidence_registry_v1 import EvidenceRegistryV1
            from .bayesian_evidence_v1 import BayesianEvidenceV1
            from .research_promotion_policy_v1 import ResearchPromotionPolicyV1

            EvidenceRegistryV1(self.db_path).refresh()
            BayesianEvidenceV1(self.db_path).refresh()
            ResearchPromotionPolicyV1(self.db_path).refresh()
        except Exception:
            return

    def recent_canary_count(self, con: sqlite3.Connection, symbol: str = "", side: str = "", setup: str = "") -> int:
        if not self.table_exists(con, "decisions"):
            return 0

        cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        params: list[Any] = [cutoff]

        where = [
            "ts >= ?",
            "action='PROBE'",
            "(payload LIKE '%CANARY_MICRO_PROBE_ONLY%' OR payload LIKE '%CANARY_PROMOTION_GATE_V1%')",
        ]

        if symbol:
            where.append("symbol=?")
            params.append(symbol)
        if side:
            where.append("side=?")
            params.append(side)
        if setup:
            where.append("setup=?")
            params.append(setup)

        q = "SELECT COUNT(*) AS n FROM decisions WHERE " + " AND ".join(where)
        row = con.execute(q, params).fetchone()
        return int(row["n"] if row else 0)

    def fail(self, symbol: str, side: str, setup: str, status: str, reasons: list[str], payload: Optional[Dict[str, Any]] = None) -> CanaryGateVerdict:
        return CanaryGateVerdict(
            version=VERSION,
            ts=utc_now_iso(),
            symbol=symbol,
            side=side,
            setup=setup,
            allow_canary_probe=False,
            allow_direct_open=False,
            size_multiplier_cap=0.0,
            absolute_size_usd_cap=0.0,
            gate_status=status,
            source_status="FAIL_CLOSED",
            source_age_sec=None,
            fail_closed=True,
            reasons=reasons,
            payload=payload or {},
        )

    def evaluate(self, symbol: str, side: str, setup: str) -> Dict[str, Any]:
        symbol = str(symbol or "").upper()
        side = str(side or "").upper()
        setup = str(setup or "").upper()
        key = (symbol, side, setup)

        now = time.time()
        if now - self._cache_ts > CACHE_TTL_SEC:
            self._cache = {}
            self._cache_ts = now

        if key in self._cache:
            return self._cache[key].to_dict()

        self.maybe_refresh_sources()

        try:
            with self.connect() as con:
                if not self.table_exists(con, "latest_research_promotion_v1"):
                    verdict = self.fail(symbol, side, setup, "NO_SOURCE_VIEW", ["MISSING_LATEST_RESEARCH_PROMOTION_VIEW"])

                else:
                    row = con.execute("""
                        SELECT *
                        FROM latest_research_promotion_v1
                        WHERE symbol=? AND side=? AND setup=?
                        LIMIT 1;
                    """, (symbol, side, setup)).fetchone()

                    if not row:
                        verdict = self.fail(symbol, side, setup, "NO_PROMOTION_ROW", ["NO_RESEARCH_PROMOTION_MATCH"])

                    else:
                        row_ts = txt(row["ts"])
                        dt = parse_iso(row_ts)
                        age = None
                        if dt:
                            age = max(0.0, (datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds())

                        reasons = []
                        allow = True

                        if age is None or age > MAX_SOURCE_AGE_SEC:
                            allow = False
                            reasons.append("SOURCE_STALE_OR_UNPARSEABLE")

                        if inum(row["allow_canary_probe"], 0) != 1:
                            allow = False
                            reasons.append("SOURCE_DOES_NOT_ALLOW_CANARY")

                        if inum(row["allow_direct_open"], 0) != 0:
                            allow = False
                            reasons.append("SOURCE_DIRECT_OPEN_NOT_ALLOWED_BY_GATE")

                        setup_count = self.recent_canary_count(con, symbol, side, setup)
                        global_count = self.recent_canary_count(con)

                        if setup_count >= MAX_CANARY_PER_SETUP_24H:
                            allow = False
                            reasons.append("SETUP_CANARY_LIMIT_24H")

                        if global_count >= MAX_GLOBAL_CANARY_24H:
                            allow = False
                            reasons.append("GLOBAL_CANARY_LIMIT_24H")

                        size_mult = min(fnum(row["size_multiplier_cap"], 0.0), MAX_SIZE_MULTIPLIER_CAP)
                        usd_cap = min(fnum(row["absolute_size_usd_cap"], 0.0), MAX_ABSOLUTE_SIZE_USD_CAP)

                        if size_mult <= 0 or usd_cap <= 0:
                            allow = False
                            reasons.append("INVALID_SIZE_CAPS")

                        if allow:
                            status = "CANARY_ALLOWED"
                            reasons.extend([
                                "CANARY_PROMOTION_GATE_V1",
                                "DIRECT_OPEN_FORBIDDEN",
                                "FAIL_CLOSED_VALIDATED",
                                "SIZE_CAPPED",
                            ])
                        else:
                            status = "CANARY_BLOCKED"

                        verdict = CanaryGateVerdict(
                            version=VERSION,
                            ts=utc_now_iso(),
                            symbol=symbol,
                            side=side,
                            setup=setup,
                            allow_canary_probe=bool(allow),
                            allow_direct_open=False,
                            size_multiplier_cap=size_mult if allow else 0.0,
                            absolute_size_usd_cap=usd_cap if allow else 0.0,
                            gate_status=status,
                            source_status=txt(row["promotion_state"]),
                            source_age_sec=round(age, 2) if age is not None else None,
                            fail_closed=not bool(allow),
                            reasons=reasons,
                            payload={
                                "source_ts": row_ts,
                                "setup_canary_24h": setup_count,
                                "global_canary_24h": global_count,
                                "source_forward_n": inum(row["forward_n"], 0),
                                "source_quality_score": fnum(row["quality_score"], 0.0),
                                "source_shrunk_exp_r": fnum(row["shrunk_exp_r"], 0.0),
                            },
                        )

        except Exception as exc:
            verdict = self.fail(symbol, side, setup, "GATE_ERROR", ["CANARY_GATE_EXCEPTION"], {"error": str(exc)[:300]})

        self._cache[key] = verdict
        return verdict.to_dict()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="")
    parser.add_argument("--side", default="")
    parser.add_argument("--setup", default="")
    args = parser.parse_args()

    gate = CanaryPromotionGateV1()

    tests = []
    if args.symbol and args.side and args.setup:
        tests.append((args.symbol, args.side, args.setup))
    else:
        tests = [
            ("BTCUSDT", "LONG", "CAPITULATION_REBOUND_LONG"),
            ("ETHUSDT", "LONG", "CAPITULATION_REBOUND_LONG"),
            ("BTCUSDT", "SHORT", "TREND_BOUNCE_SHORT"),
            ("ETHUSDT", "SHORT", "TREND_BOUNCE_SHORT"),
        ]

    for symbol, side, setup in tests:
        print(json.dumps(gate.evaluate(symbol, side, setup), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
