from __future__ import annotations

import argparse
import json
from typing import Any, Dict, List

from joanbot.storage.db import get_db
from joanbot.utils import utc_now_iso

VERSION = "PAID_API_READINESS_GATE_V10"


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


class PaidApiReadinessGateV10:
    """
    Objective gate for CoinGlass/paid API spend.

    This deliberately says NOT READY until the free-data V10 loop has proven
    stable micro-canary execution and measurable positive net KPIs.
    """

    def __init__(self, db=None) -> None:
        self.db = db or get_db()

    def ensure_schema(self) -> None:
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS paid_api_readiness_gate_v10 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                version TEXT NOT NULL,
                readiness_state TEXT NOT NULL,
                paid_api_allowed INTEGER NOT NULL,
                closed_canaries INTEGER NOT NULL,
                profit_factor REAL NOT NULL,
                expectancy_r REAL NOT NULL,
                max_drawdown_r REAL NOT NULL,
                derivatives_ready_symbols INTEGER NOT NULL,
                derivatives_total_symbols INTEGER NOT NULL,
                critical_errors_24h INTEGER NOT NULL,
                hard_vetoes TEXT NOT NULL,
                required_before_paid_api TEXT NOT NULL,
                payload TEXT NOT NULL
            );
        """)
        self.db.execute("DROP VIEW IF EXISTS latest_paid_api_readiness_gate_v10;")
        self.db.execute("""
            CREATE VIEW latest_paid_api_readiness_gate_v10 AS
            SELECT * FROM paid_api_readiness_gate_v10
            ORDER BY id DESC LIMIT 1;
        """)

    def qmany(self, sql: str) -> List[Dict[str, Any]]:
        try:
            return [dict(r) for r in self.db.query(sql)]
        except Exception:
            return []

    def q1(self, sql: str) -> Dict[str, Any]:
        rows = self.qmany(sql)
        return rows[0] if rows else {}

    def refresh(self) -> Dict[str, Any]:
        self.ensure_schema()
        kpi = self.q1("SELECT * FROM latest_micro_canary_kpi_v10;")
        data_rows = self.qmany("SELECT * FROM latest_derivatives_data_spine_v10;")
        err = self.q1("""
            SELECT COUNT(*) AS n
            FROM runtime_events
            WHERE level='ERROR'
              AND component IN ('runtime_v10','derivatives_data_spine_v10','control_plane_v10')
              AND ts >= datetime('now','-1 day');
        """)

        closed_n = inum(kpi.get("closed_n"))
        pf = fnum(kpi.get("profit_factor"))
        exp = fnum(kpi.get("expectancy_r"))
        dd = fnum(kpi.get("max_drawdown_r"))
        ready_symbols = sum(1 for r in data_rows if str(r.get("data_state")) == "DERIVATIVES_DATA_READY")
        total_symbols = len(data_rows)
        critical_errors = inum(err.get("n"))

        vetoes: List[str] = []
        required: List[str] = []
        if closed_n < 10:
            vetoes.append("CLOSED_CANARIES_LT_10")
            required.append("Collect at least 10 closed V10 micro-canaries")
        if pf < 1.15:
            vetoes.append("PF_LT_1_15")
            required.append("Profit factor must be >= 1.15 net of fees/slippage")
        if exp < 0.03:
            vetoes.append("EXPECTANCY_LT_0_03R")
            required.append("Expectancy must be >= +0.03R")
        if dd <= -4.0:
            vetoes.append("MAX_DRAWDOWN_R_TOO_LOW")
            required.append("Max drawdown must stay above -4R")
        if total_symbols == 0 or ready_symbols < total_symbols:
            vetoes.append("FREE_DERIVATIVES_DATA_NOT_FULLY_READY")
            required.append("Free Binance derivatives spine must be ready for all enabled symbols")
        if critical_errors > 0:
            vetoes.append("CRITICAL_RUNTIME_ERRORS_24H")
            required.append("Run 24h without V10 critical runtime errors")

        if not vetoes:
            state = "PAID_API_READY_FOR_1_MONTH_TEST"
            allowed = 1
        else:
            state = "PAID_API_NOT_READY"
            allowed = 0

        payload = {"kpi": kpi, "derivatives_data": data_rows, "strict_paid_api_gate": True}
        self.db.execute("""
            INSERT INTO paid_api_readiness_gate_v10 (
                ts, version, readiness_state, paid_api_allowed,
                closed_canaries, profit_factor, expectancy_r, max_drawdown_r,
                derivatives_ready_symbols, derivatives_total_symbols, critical_errors_24h,
                hard_vetoes, required_before_paid_api, payload
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """, (
            utc_now_iso(), VERSION, state, allowed,
            closed_n, pf, exp, dd, ready_symbols, total_symbols, critical_errors,
            js(vetoes), js(required), js(payload),
        ))
        return {
            "version": VERSION,
            "readiness_state": state,
            "paid_api_allowed": allowed,
            "closed_canaries": closed_n,
            "profit_factor": round(pf, 4),
            "expectancy_r": round(exp, 4),
            "hard_vetoes": vetoes,
            "required_before_paid_api": required,
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    engine = PaidApiReadinessGateV10()
    if args.refresh:
        print(json.dumps(engine.refresh(), indent=2, sort_keys=True, default=str))
    else:
        engine.ensure_schema()
        print(json.dumps({"version": VERSION, "schema": "ok"}, indent=2))


if __name__ == "__main__":
    main()
