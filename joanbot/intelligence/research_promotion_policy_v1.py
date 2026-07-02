from __future__ import annotations

import argparse
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

VERSION = "RESEARCH_PROMOTION_POLICY_V1_CANARY_MICRO_PROBE"
DB_PATH = Path(os.environ.get("JOANBOT_DB", "data/joanbot_v14.sqlite"))

ALLOWED_SYMBOLS = {"BTCUSDT", "ETHUSDT"}

MIN_FORWARD_N = 300
MIN_FORWARD_EXP_R = 0.18
MIN_FORWARD_PF = 2.0
MIN_SHRUNK_EXP_R = 0.08
MIN_QUALITY_SCORE = 35.0

MAX_DIVERGENCE_PENALTY = 0.50
MAX_EXCLUDED_EXEC_N = 10

MAX_CANARY_PER_SETUP_24H = 2
MAX_GLOBAL_CANARY_24H = 4

CANARY_SIZE_MULTIPLIER_CAP = 0.025
CANARY_ABSOLUTE_SIZE_USD_CAP = 250.0

STATES_ALLOW_CANARY = {
    "RESEARCH_CANDIDATE_PENDING_CLEAN_SAMPLE",
    "RESEARCH_CANDIDATE",
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def txt(x: Any) -> str:
    return "" if x is None else str(x)


def fnum(x: Any, default: float = 0.0) -> float:
    try:
        if x is None:
            return default
        return float(x)
    except Exception:
        return default


def in_last_24h_sql() -> str:
    return "datetime(ts) >= datetime('now','-24 hours')"


class ResearchPromotionPolicyV1:
    """
    Institutional research promotion policy.

    It converts strong Bayesian evidence into controlled canary micro-probe eligibility.

    It does not:
    - allow direct OPEN
    - change broker
    - change execution
    - change risk implementation
    - write positions or trades
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
            CREATE TABLE IF NOT EXISTS research_promotion_decisions_v1 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                version TEXT NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                setup TEXT NOT NULL,

                source_status TEXT NOT NULL,
                forward_n INTEGER NOT NULL,
                forward_exp_r REAL NOT NULL,
                forward_pf REAL,
                clean_exec_n INTEGER NOT NULL,
                excluded_exec_n INTEGER NOT NULL,
                shrunk_exp_r REAL NOT NULL,
                divergence_penalty REAL NOT NULL,
                quality_score REAL NOT NULL,

                allow_canary_probe INTEGER NOT NULL,
                allow_direct_open INTEGER NOT NULL,
                size_multiplier_cap REAL NOT NULL,
                absolute_size_usd_cap REAL NOT NULL,

                promotion_state TEXT NOT NULL,
                reasons TEXT NOT NULL,
                payload TEXT NOT NULL
            );
        """)

        con.execute("""
            CREATE INDEX IF NOT EXISTS idx_research_promotion_decisions_v1_key
            ON research_promotion_decisions_v1(symbol, side, setup, id);
        """)

        con.execute("DROP VIEW IF EXISTS latest_research_promotion_v1;")
        con.execute("""
            CREATE VIEW latest_research_promotion_v1 AS
            SELECT p.*
            FROM research_promotion_decisions_v1 p
            JOIN (
                SELECT symbol, side, setup, MAX(id) AS max_id
                FROM research_promotion_decisions_v1
                GROUP BY symbol, side, setup
            ) x ON x.max_id = p.id;
        """)

    def table_exists(self, con: sqlite3.Connection, name: str) -> bool:
        row = con.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','view') AND name=?",
            (name,),
        ).fetchone()
        return bool(row)

    def recent_canary_count(self, con: sqlite3.Connection, symbol: str = "", side: str = "", setup: str = "") -> int:
        if not self.table_exists(con, "decisions"):
            return 0

        params: List[Any] = []
        where = [
            "action IN ('PROBE','OPEN')",
            "UPPER(COALESCE(payload,'')) LIKE '%CANARY%'",
            in_last_24h_sql(),
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

    def latest_bayesian_rows(self, con: sqlite3.Connection) -> List[sqlite3.Row]:
        if not self.table_exists(con, "latest_bayesian_evidence_v1"):
            raise RuntimeError("Missing latest_bayesian_evidence_v1. Run Bayesian Evidence first.")

        return list(con.execute("""
            SELECT *
            FROM latest_bayesian_evidence_v1
            ORDER BY quality_score DESC, shrunk_exp_r DESC;
        """).fetchall())

    def evaluate_row(self, con: sqlite3.Connection, r: sqlite3.Row) -> Dict[str, Any]:
        symbol = txt(r["symbol"])
        side = txt(r["side"])
        setup = txt(r["setup"])

        forward_n = int(fnum(r["forward_n"], 0))
        forward_exp_r = fnum(r["forward_exp_r"], 0.0)
        forward_pf = fnum(r["forward_pf"], 0.0)
        clean_exec_n = int(fnum(r["clean_exec_n"], 0))
        excluded_exec_n = int(fnum(r["excluded_exec_n"], 0))
        shrunk_exp_r = fnum(r["shrunk_exp_r"], 0.0)
        divergence_penalty = fnum(r["divergence_penalty"], 0.0)
        quality_score = fnum(r["quality_score"], 0.0)
        source_status = txt(r["status"])

        reasons: List[str] = []

        allow_canary = 1
        state = "CANARY_MICRO_PROBE_ELIGIBLE"

        if symbol not in ALLOWED_SYMBOLS:
            allow_canary = 0
            state = "REJECTED_SYMBOL_NOT_ALLOWED"
            reasons.append("SYMBOL_NOT_ALLOWED")

        if source_status not in STATES_ALLOW_CANARY:
            allow_canary = 0
            state = "REJECTED_SOURCE_STATUS"
            reasons.append("SOURCE_STATUS_NOT_RESEARCH_PROMOTABLE")

        if forward_n < MIN_FORWARD_N:
            allow_canary = 0
            state = "REJECTED_FORWARD_SAMPLE_LOW"
            reasons.append("FORWARD_SAMPLE_LOW")

        if forward_exp_r < MIN_FORWARD_EXP_R:
            allow_canary = 0
            state = "REJECTED_FORWARD_EXPECTANCY_LOW"
            reasons.append("FORWARD_EXPECTANCY_LOW")

        if forward_pf < MIN_FORWARD_PF:
            allow_canary = 0
            state = "REJECTED_FORWARD_PF_LOW"
            reasons.append("FORWARD_PF_LOW")

        if shrunk_exp_r < MIN_SHRUNK_EXP_R:
            allow_canary = 0
            state = "REJECTED_SHRUNK_EXPECTANCY_LOW"
            reasons.append("SHRUNK_EXPECTANCY_LOW")

        if quality_score < MIN_QUALITY_SCORE:
            allow_canary = 0
            state = "REJECTED_QUALITY_LOW"
            reasons.append("QUALITY_SCORE_LOW")

        if divergence_penalty > MAX_DIVERGENCE_PENALTY:
            allow_canary = 0
            state = "REJECTED_DIVERGENCE_TOO_HIGH"
            reasons.append("DIVERGENCE_TOO_HIGH")

        if excluded_exec_n > MAX_EXCLUDED_EXEC_N:
            allow_canary = 0
            state = "REJECTED_TOO_MUCH_EXCLUDED_HISTORY"
            reasons.append("EXCLUDED_EXEC_HISTORY_TOO_LARGE")

        setup_canary_24h = self.recent_canary_count(con, symbol, side, setup)
        global_canary_24h = self.recent_canary_count(con)

        if setup_canary_24h >= MAX_CANARY_PER_SETUP_24H:
            allow_canary = 0
            state = "REJECTED_SETUP_CANARY_LIMIT_24H"
            reasons.append("SETUP_CANARY_LIMIT_24H")

        if global_canary_24h >= MAX_GLOBAL_CANARY_24H:
            allow_canary = 0
            state = "REJECTED_GLOBAL_CANARY_LIMIT_24H"
            reasons.append("GLOBAL_CANARY_LIMIT_24H")

        if clean_exec_n == 0:
            reasons.append("FORWARD_ONLY_ALLOWED_AS_CANARY_RESEARCH_NOT_OPEN")

        reasons.append("DIRECT_OPEN_FORBIDDEN")
        reasons.append("SIZE_CAPPED")
        reasons.append("CANARY_RESEARCH_ONLY")

        return {
            "ts": utc_now_iso(),
            "version": VERSION,
            "symbol": symbol,
            "side": side,
            "setup": setup,
            "source_status": source_status,
            "forward_n": forward_n,
            "forward_exp_r": round(forward_exp_r, 8),
            "forward_pf": round(forward_pf, 8),
            "clean_exec_n": clean_exec_n,
            "excluded_exec_n": excluded_exec_n,
            "shrunk_exp_r": round(shrunk_exp_r, 8),
            "divergence_penalty": round(divergence_penalty, 8),
            "quality_score": round(quality_score, 8),
            "allow_canary_probe": int(allow_canary),
            "allow_direct_open": 0,
            "size_multiplier_cap": CANARY_SIZE_MULTIPLIER_CAP if allow_canary else 0.0,
            "absolute_size_usd_cap": CANARY_ABSOLUTE_SIZE_USD_CAP if allow_canary else 0.0,
            "promotion_state": state,
            "reasons": reasons,
            "payload": {
                "limits": {
                    "max_canary_per_setup_24h": MAX_CANARY_PER_SETUP_24H,
                    "max_global_canary_24h": MAX_GLOBAL_CANARY_24H,
                    "canary_size_multiplier_cap": CANARY_SIZE_MULTIPLIER_CAP,
                    "canary_absolute_size_usd_cap": CANARY_ABSOLUTE_SIZE_USD_CAP,
                    "direct_open_allowed": False,
                },
                "recent_counts": {
                    "setup_canary_24h": setup_canary_24h,
                    "global_canary_24h": global_canary_24h,
                },
            },
        }

    def insert_decision(self, con: sqlite3.Connection, d: Dict[str, Any]) -> None:
        con.execute("""
            INSERT INTO research_promotion_decisions_v1 (
                ts, version, symbol, side, setup,
                source_status, forward_n, forward_exp_r, forward_pf,
                clean_exec_n, excluded_exec_n, shrunk_exp_r, divergence_penalty, quality_score,
                allow_canary_probe, allow_direct_open, size_multiplier_cap, absolute_size_usd_cap,
                promotion_state, reasons, payload
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """, (
            d["ts"], d["version"], d["symbol"], d["side"], d["setup"],
            d["source_status"], d["forward_n"], d["forward_exp_r"], d["forward_pf"],
            d["clean_exec_n"], d["excluded_exec_n"], d["shrunk_exp_r"], d["divergence_penalty"], d["quality_score"],
            d["allow_canary_probe"], d["allow_direct_open"], d["size_multiplier_cap"], d["absolute_size_usd_cap"],
            d["promotion_state"],
            json.dumps(d["reasons"], separators=(",", ":"), ensure_ascii=False),
            json.dumps(d["payload"], separators=(",", ":"), ensure_ascii=False),
        ))

    def apply_retention(self, con: sqlite3.Connection) -> None:
        con.execute("""
            DELETE FROM research_promotion_decisions_v1
            WHERE id NOT IN (
                SELECT id
                FROM research_promotion_decisions_v1
                ORDER BY id DESC
                LIMIT 500
            );
        """)

    def refresh(self) -> Dict[str, Any]:
        with self.connect() as con:
            self.ensure_schema(con)
            rows = self.latest_bayesian_rows(con)
            decisions = [self.evaluate_row(con, r) for r in rows]

            for d in decisions:
                self.insert_decision(con, d)

            self.apply_retention(con)
            con.commit()

        return {
            "version": VERSION,
            "evaluated": len(decisions),
            "canary_probe_eligible": sum(1 for d in decisions if d["allow_canary_probe"]),
            "direct_open_eligible": sum(1 for d in decisions if d["allow_direct_open"]),
            "states": self.state_counts(decisions),
        }

    def state_counts(self, decisions: List[Dict[str, Any]]) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for d in decisions:
            s = d["promotion_state"]
            out[s] = out.get(s, 0) + 1
        return out

    def latest(self) -> List[Dict[str, Any]]:
        with self.connect() as con:
            self.ensure_schema(con)
            rows = con.execute("""
                SELECT
                    symbol, side, setup,
                    source_status,
                    forward_n,
                    ROUND(forward_exp_r,4) AS forward_exp_r,
                    ROUND(forward_pf,3) AS forward_pf,
                    clean_exec_n,
                    excluded_exec_n,
                    ROUND(shrunk_exp_r,4) AS shrunk_exp_r,
                    ROUND(divergence_penalty,3) AS divergence_penalty,
                    ROUND(quality_score,2) AS quality_score,
                    allow_canary_probe,
                    allow_direct_open,
                    ROUND(size_multiplier_cap,4) AS size_multiplier_cap,
                    ROUND(absolute_size_usd_cap,2) AS absolute_size_usd_cap,
                    promotion_state,
                    reasons
                FROM latest_research_promotion_v1
                ORDER BY allow_canary_probe DESC, quality_score DESC, shrunk_exp_r DESC;
            """).fetchall()
            return [dict(r) for r in rows]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--latest", action="store_true")
    args = parser.parse_args()

    policy = ResearchPromotionPolicyV1()

    if args.refresh:
        print(json.dumps(policy.refresh(), indent=2, sort_keys=True))

    if args.latest:
        for row in policy.latest():
            print(row)


if __name__ == "__main__":
    main()
