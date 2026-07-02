from __future__ import annotations

import argparse
import json
from typing import Any, Dict, List

from joanbot.storage.db import get_db
from joanbot.utils import utc_now_iso

VERSION = "MICRO_CANARY_KPI_ENGINE_V11"


def fnum(x: Any, default: float = 0.0) -> float:
    try:
        if x is None:
            return default
        return float(x)
    except Exception:
        return default


def js(x: Any) -> str:
    return json.dumps(x, separators=(",", ":"), ensure_ascii=False, default=str)


class MicroCanaryKPIEngineV11:
    """Institutional KPIs for paper micro-canaries, net of fee/slippage model."""

    def __init__(self, db=None) -> None:
        self.db = db or get_db()

    def ensure_schema(self) -> None:
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS micro_canary_kpi_v11 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                version TEXT NOT NULL,
                total_n INTEGER NOT NULL,
                open_n INTEGER NOT NULL,
                closed_n INTEGER NOT NULL,
                wins INTEGER NOT NULL,
                losses INTEGER NOT NULL,
                winrate REAL NOT NULL,
                gross_profit_r REAL NOT NULL,
                gross_loss_r REAL NOT NULL,
                profit_factor REAL NOT NULL,
                expectancy_r REAL NOT NULL,
                max_drawdown_r REAL NOT NULL,
                last5_expectancy_r REAL NOT NULL,
                last10_expectancy_r REAL NOT NULL,
                kpi_state TEXT NOT NULL,
                hard_vetoes TEXT NOT NULL,
                payload TEXT NOT NULL
            );
        """)
        self.db.execute("DROP VIEW IF EXISTS latest_micro_canary_kpi_v11;")
        self.db.execute("""
            CREATE VIEW latest_micro_canary_kpi_v11 AS
            SELECT * FROM micro_canary_kpi_v11
            ORDER BY id DESC LIMIT 1;
        """)

    def qmany(self, sql: str) -> List[Dict[str, Any]]:
        try:
            return [dict(r) for r in self.db.query(sql)]
        except Exception:
            return []

    def refresh(self) -> Dict[str, Any]:
        self.ensure_schema()
        all_rows = self.qmany("SELECT * FROM paper_micro_canary_positions_v11 ORDER BY id ASC;")
        closed = [r for r in all_rows if str(r.get("status")) == "CLOSED"]
        vals = [fnum(r.get("net_pnl_r", r.get("pnl_r"))) for r in closed]
        total_n = len(all_rows)
        open_n = sum(1 for r in all_rows if str(r.get("status")) == "OPEN")
        closed_n = len(closed)
        wins = sum(1 for x in vals if x > 0)
        losses = sum(1 for x in vals if x < 0)
        winrate = wins * 100.0 / closed_n if closed_n else 0.0
        gross_profit = sum(x for x in vals if x > 0)
        gross_loss = abs(sum(x for x in vals if x < 0))
        if gross_loss > 0:
            pf = gross_profit / gross_loss
        elif gross_profit > 0:
            pf = 99.0
        else:
            pf = 0.0
        expectancy = sum(vals) / closed_n if closed_n else 0.0
        last5 = vals[-5:]
        last10 = vals[-10:]
        last5_exp = sum(last5) / len(last5) if last5 else 0.0
        last10_exp = sum(last10) / len(last10) if last10 else 0.0

        equity = 0.0
        peak = 0.0
        max_dd = 0.0
        for x in vals:
            equity += x
            peak = max(peak, equity)
            max_dd = min(max_dd, equity - peak)

        vetoes: List[str] = []
        if closed_n < 10:
            state = "KPI_SAMPLE_TOO_SMALL"
            vetoes.append("CLOSED_CANARIES_LT_10")
        elif expectancy <= 0.0:
            state = "KPI_NEGATIVE_EXPECTANCY"
            vetoes.append("EXPECTANCY_NOT_POSITIVE")
        elif pf < 1.15:
            state = "KPI_PROFIT_FACTOR_TOO_LOW"
            vetoes.append("PF_LT_1_15")
        elif max_dd <= -4.0:
            state = "KPI_DRAWDOWN_TOO_HIGH"
            vetoes.append("MAX_DD_R_LE_-4")
        else:
            state = "KPI_HEALTHY"

        payload = {"closed_net_r": vals, "paper_only": True, "paid_api_gate_input": True}
        self.db.execute("""
            INSERT INTO micro_canary_kpi_v11 (
                ts, version, total_n, open_n, closed_n, wins, losses, winrate,
                gross_profit_r, gross_loss_r, profit_factor, expectancy_r, max_drawdown_r,
                last5_expectancy_r, last10_expectancy_r, kpi_state, hard_vetoes, payload
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """, (
            utc_now_iso(), VERSION, total_n, open_n, closed_n, wins, losses, winrate,
            gross_profit, gross_loss, pf, expectancy, max_dd, last5_exp, last10_exp,
            state, js(vetoes), js(payload),
        ))
        return {
            "version": VERSION,
            "kpi_state": state,
            "closed_n": closed_n,
            "profit_factor": round(pf, 4),
            "expectancy_r": round(expectancy, 4),
            "max_drawdown_r": round(max_dd, 4),
            "hard_vetoes": vetoes,
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    engine = MicroCanaryKPIEngineV11()
    if args.refresh:
        print(json.dumps(engine.refresh(), indent=2, sort_keys=True))
    else:
        engine.ensure_schema()
        print(json.dumps({"version": VERSION, "schema": "ok"}, indent=2))


if __name__ == "__main__":
    main()
