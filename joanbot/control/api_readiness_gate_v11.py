from __future__ import annotations

import argparse
import json
from typing import Any, Dict, List

from joanbot.storage.db import get_db
from joanbot.utils import utc_now_iso

VERSION = "PAID_API_READINESS_GATE_V11_2_WITH_ABLATION"


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


class PaidApiReadinessGateV11:
    """
    Objective gate for CoinGlass/paid API spend.

    This deliberately says NOT READY until the free-data V11 loop has proven
    stable micro-canary execution and measurable positive net KPIs.
    """

    def __init__(self, db=None) -> None:
        self.db = db or get_db()

    def ensure_schema(self) -> None:
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS paid_api_readiness_gate_v11 (
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
                ablation_state TEXT NOT NULL DEFAULT 'NO_ABLATION',
                ablation_closed_n INTEGER NOT NULL DEFAULT 0,
                ablation_expectancy_r REAL NOT NULL DEFAULT 0.0,
                ablation_profit_factor REAL NOT NULL DEFAULT 0.0,
                hard_vetoes TEXT NOT NULL,
                required_before_paid_api TEXT NOT NULL,
                payload TEXT NOT NULL
            );
        """)
        # Migration-safe additions for users who already ran V11 before V11.2.
        for ddl in (
            "ALTER TABLE paid_api_readiness_gate_v11 ADD COLUMN ablation_state TEXT NOT NULL DEFAULT 'NO_ABLATION';",
            "ALTER TABLE paid_api_readiness_gate_v11 ADD COLUMN ablation_closed_n INTEGER NOT NULL DEFAULT 0;",
            "ALTER TABLE paid_api_readiness_gate_v11 ADD COLUMN ablation_expectancy_r REAL NOT NULL DEFAULT 0.0;",
            "ALTER TABLE paid_api_readiness_gate_v11 ADD COLUMN ablation_profit_factor REAL NOT NULL DEFAULT 0.0;",
        ):
            try:
                self.db.execute(ddl)
            except Exception:
                pass

        self.db.execute("DROP VIEW IF EXISTS latest_paid_api_readiness_gate_v11;")
        self.db.execute("""
            CREATE VIEW latest_paid_api_readiness_gate_v11 AS
            SELECT * FROM paid_api_readiness_gate_v11
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
        kpi = self.q1("SELECT * FROM latest_micro_canary_kpi_v11;")
        data_rows = self.qmany("SELECT * FROM latest_derivatives_data_spine_v10;")
        ablation = self.q1("""
            SELECT *
            FROM latest_institutional_ablation_v12
            WHERE scenario='D_FULL_SINGLE_ORDER_V11'
            LIMIT 1;
        """)
        err = self.q1("""
            SELECT COUNT(*) AS n
            FROM runtime_events
            WHERE level='ERROR'
              AND component IN ('runtime_v11','derivatives_data_spine_v10','control_plane_v11')
              AND ts >= datetime('now','-1 day');
        """)

        closed_n = inum(kpi.get("closed_n"))
        pf = fnum(kpi.get("profit_factor"))
        exp = fnum(kpi.get("expectancy_r"))
        dd = fnum(kpi.get("max_drawdown_r"))
        ready_symbols = sum(1 for r in data_rows if str(r.get("data_state")) == "DERIVATIVES_DATA_READY")
        total_symbols = len(data_rows)
        critical_errors = inum(err.get("n"))
        ablation_state = str(ablation.get("ablation_state") or "NO_ABLATION")
        ablation_closed_n = inum(ablation.get("closed_n"))
        ablation_exp = fnum(ablation.get("expectancy_r"))
        ablation_pf = fnum(ablation.get("profit_factor"))

        vetoes: List[str] = []
        required: List[str] = []
        if closed_n < 10:
            vetoes.append("CLOSED_CANARIES_LT_10")
            required.append("Collect at least 10 closed V11 micro-canaries")
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
            required.append("Run 24h without V11 critical runtime errors")
        if ablation_state == "NO_ABLATION":
            vetoes.append("ABLATION_ENGINE_NOT_RUN")
            required.append("Run V12 ablation engine before paid API decision")
        elif ablation_state not in ("ABLATION_READY_FOR_PAID_API_TEST", "ABLATION_SAMPLE_TOO_SMALL"):
            vetoes.append("ABLATION_NOT_SUPPORTIVE")
            required.append("V11 ablation must be healthy before paid API test")
        if ablation_closed_n >= 10 and (ablation_exp < 0.03 or ablation_pf < 1.15):
            vetoes.append("ABLATION_KPI_NOT_GOOD_ENOUGH")
            required.append("Full single-order ablation must show PF>=1.15 and expectancy>=0.03R")

        if not vetoes:
            state = "PAID_API_READY_FOR_1_MONTH_TEST"
            allowed = 1
        else:
            state = "PAID_API_NOT_READY"
            allowed = 0

        payload = {"kpi": kpi, "derivatives_data": data_rows, "ablation": ablation, "strict_paid_api_gate": True}
        self.db.execute("""
            INSERT INTO paid_api_readiness_gate_v11 (
                ts, version, readiness_state, paid_api_allowed,
                closed_canaries, profit_factor, expectancy_r, max_drawdown_r,
                derivatives_ready_symbols, derivatives_total_symbols, critical_errors_24h,
                ablation_state, ablation_closed_n, ablation_expectancy_r, ablation_profit_factor,
                hard_vetoes, required_before_paid_api, payload
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """, (
            utc_now_iso(), VERSION, state, allowed,
            closed_n, pf, exp, dd, ready_symbols, total_symbols, critical_errors,
            ablation_state, ablation_closed_n, ablation_exp, ablation_pf,
            js(vetoes), js(required), js(payload),
        ))
        return {
            "version": VERSION,
            "readiness_state": state,
            "paid_api_allowed": allowed,
            "closed_canaries": closed_n,
            "profit_factor": round(pf, 4),
            "expectancy_r": round(exp, 4),
            "ablation_state": ablation_state,
            "ablation_closed_n": ablation_closed_n,
            "ablation_expectancy_r": round(ablation_exp, 4),
            "ablation_profit_factor": round(ablation_pf, 4),
            "hard_vetoes": vetoes,
            "required_before_paid_api": required,
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    engine = PaidApiReadinessGateV11()
    if args.refresh:
        print(json.dumps(engine.refresh(), indent=2, sort_keys=True, default=str))
    else:
        engine.ensure_schema()
        print(json.dumps({"version": VERSION, "schema": "ok"}, indent=2))


if __name__ == "__main__":
    main()
