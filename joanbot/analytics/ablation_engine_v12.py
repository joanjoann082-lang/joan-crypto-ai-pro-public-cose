from __future__ import annotations

import argparse
import json
from typing import Any, Callable, Dict, List

from joanbot.storage.db import get_db
from joanbot.utils import utc_now_iso

VERSION = "INSTITUTIONAL_ABLATION_ENGINE_V12"


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
    return json.dumps(x, separators=(",", ":"), ensure_ascii=False, sort_keys=True, default=str)


class InstitutionalAblationEngineV12:
    """
    Observational ablation for V11/V10.2.

    This does not pretend to know counterfactual PnL for trades that were not opened.
    It creates a strict measurement layer that separates:
      - how many control-plane moments each filter would have allowed
      - how many actual V11 canaries were opened/closed under each filter set
      - net-R outcomes of executed canaries

    Its main purpose is to prevent paying for an external API before the free-data
    filter proves it can improve or at least not damage the V11 paper loop.
    """

    def __init__(self, db=None) -> None:
        self.db = db or get_db()

    def ensure_schema(self) -> None:
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS institutional_ablation_v12 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                version TEXT NOT NULL,
                refresh_id INTEGER NOT NULL,
                scenario TEXT NOT NULL,
                scenario_rank INTEGER NOT NULL,
                eligible_control_n INTEGER NOT NULL,
                opened_n INTEGER NOT NULL,
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
                ablation_state TEXT NOT NULL,
                hard_vetoes TEXT NOT NULL,
                interpretation TEXT NOT NULL,
                payload TEXT NOT NULL
            );
        """)
        self.db.execute("DROP VIEW IF EXISTS latest_institutional_ablation_v12;")
        self.db.execute("""
            CREATE VIEW latest_institutional_ablation_v12 AS
            SELECT *
            FROM institutional_ablation_v12
            WHERE refresh_id = (SELECT MAX(refresh_id) FROM institutional_ablation_v12);
        """)

    def qmany(self, sql: str, params=()) -> List[Dict[str, Any]]:
        try:
            return [dict(r) for r in self.db.query(sql, params)]
        except Exception:
            return []

    def table_exists(self, table: str) -> bool:
        rows = self.qmany("SELECT name FROM sqlite_master WHERE type IN ('table','view') AND name=?;", (table,))
        return bool(rows)

    def controls(self) -> List[Dict[str, Any]]:
        if not self.table_exists("institutional_control_plane_v11"):
            return []
        return self.qmany("SELECT * FROM institutional_control_plane_v11 ORDER BY id ASC;")

    def canaries(self) -> List[Dict[str, Any]]:
        if not self.table_exists("paper_micro_canary_positions_v11"):
            return []
        if not self.table_exists("institutional_control_plane_v11"):
            return self.qmany("SELECT * FROM paper_micro_canary_positions_v11 ORDER BY id ASC;")
        return self.qmany("""
            SELECT
                p.*,
                c.validation_state AS c_validation_state,
                c.shadow_regime_state AS c_shadow_regime_state,
                c.derivatives_state AS c_derivatives_state,
                c.derivatives_data_quality AS c_derivatives_data_quality,
                c.allow_paper_micro_canary AS c_allow_paper_micro_canary,
                c.decision_tier AS c_decision_tier,
                c.global_state AS c_global_state,
                c.control_score AS c_control_score
            FROM paper_micro_canary_positions_v11 p
            LEFT JOIN institutional_control_plane_v11 c ON c.id = p.control_id
            ORDER BY p.id ASC;
        """)

    @staticmethod
    def validation_ok(r: Dict[str, Any]) -> bool:
        return str(r.get("validation_state") or r.get("c_validation_state")) == "ROBUST_EDGE_READY"

    @staticmethod
    def shadow_ok(r: Dict[str, Any]) -> bool:
        return str(r.get("shadow_regime_state") or r.get("c_shadow_regime_state")) in ("REGIME_SUPPORTS_CLUSTER", "REGIME_MIXED_CLUSTER_REVIEW_ONLY")

    @staticmethod
    def derivatives_ok(r: Dict[str, Any]) -> bool:
        s = str(r.get("derivatives_state") or r.get("c_derivatives_state") or "")
        return s.startswith("DERIVATIVES_CONFIRM") or s.startswith("DERIVATIVES_NEUTRAL_SUPPORTIVE")

    @staticmethod
    def single_order_ok(r: Dict[str, Any]) -> bool:
        return inum(r.get("allow_paper_micro_canary") or r.get("c_allow_paper_micro_canary")) == 1

    def scenario_defs(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": "A_EDGE_ONLY",
                "rank": 10,
                "control_filter": lambda r: self.validation_ok(r),
                "canary_filter": lambda r: self.validation_ok(r),
                "interpretation": "Robust statistical edge only; ignores shadow/derivatives gates.",
            },
            {
                "name": "B_EDGE_PLUS_SHADOW_REGIME",
                "rank": 20,
                "control_filter": lambda r: self.validation_ok(r) and self.shadow_ok(r),
                "canary_filter": lambda r: self.validation_ok(r) and self.shadow_ok(r),
                "interpretation": "Edge plus shadow regime alignment.",
            },
            {
                "name": "C_EDGE_PLUS_DERIVATIVES_REGIME",
                "rank": 30,
                "control_filter": lambda r: self.validation_ok(r) and self.derivatives_ok(r),
                "canary_filter": lambda r: self.validation_ok(r) and self.derivatives_ok(r),
                "interpretation": "Edge plus derivatives regime alignment, independent of shadow gate.",
            },
            {
                "name": "D_FULL_SINGLE_ORDER_V11",
                "rank": 40,
                "control_filter": lambda r: self.single_order_ok(r),
                "canary_filter": lambda r: True,
                "interpretation": "Actual V11 single-order decision kernel: edge + shadow + derivatives + feedback + KPI + overlap.",
            },
        ]

    @staticmethod
    def kpi(vals: List[float]) -> Dict[str, float]:
        closed_n = len(vals)
        wins = sum(1 for x in vals if x > 0)
        losses = sum(1 for x in vals if x < 0)
        winrate = wins * 100.0 / closed_n if closed_n else 0.0
        gp = sum(x for x in vals if x > 0)
        gl = abs(sum(x for x in vals if x < 0))
        if gl > 0:
            pf = gp / gl
        elif gp > 0:
            pf = 99.0
        else:
            pf = 0.0
        exp = sum(vals) / closed_n if closed_n else 0.0
        equity = 0.0
        peak = 0.0
        max_dd = 0.0
        for x in vals:
            equity += x
            peak = max(peak, equity)
            max_dd = min(max_dd, equity - peak)
        last5 = vals[-5:]
        last5_exp = sum(last5) / len(last5) if last5 else 0.0
        return {
            "closed_n": closed_n,
            "wins": wins,
            "losses": losses,
            "winrate": winrate,
            "gross_profit_r": gp,
            "gross_loss_r": gl,
            "profit_factor": pf,
            "expectancy_r": exp,
            "max_drawdown_r": max_dd,
            "last5_expectancy_r": last5_exp,
        }

    def state_for(self, scenario: str, eligible_n: int, closed_n: int, pf: float, exp: float, dd: float) -> Dict[str, Any]:
        vetoes: List[str] = []
        if eligible_n == 0:
            vetoes.append("NO_ELIGIBLE_CONTROL_EVENTS")
            state = "ABLATION_NO_SIGNAL"
        elif closed_n < 10:
            vetoes.append("CLOSED_CANARIES_LT_10")
            state = "ABLATION_SAMPLE_TOO_SMALL"
        elif exp <= 0.0:
            vetoes.append("EXPECTANCY_NOT_POSITIVE")
            state = "ABLATION_NEGATIVE_OR_FLAT_EXPECTANCY"
        elif pf < 1.15:
            vetoes.append("PF_LT_1_15")
            state = "ABLATION_PF_TOO_LOW"
        elif dd <= -4.0:
            vetoes.append("MAX_DRAWDOWN_R_TOO_LOW")
            state = "ABLATION_DRAWDOWN_TOO_HIGH"
        else:
            state = "ABLATION_HEALTHY"
        if scenario == "D_FULL_SINGLE_ORDER_V11" and state == "ABLATION_HEALTHY":
            state = "ABLATION_READY_FOR_PAID_API_TEST"
        return {"state": state, "vetoes": vetoes}

    def refresh(self) -> Dict[str, Any]:
        self.ensure_schema()
        refresh_id = inum(self.qmany("SELECT COALESCE(MAX(refresh_id),0)+1 AS n FROM institutional_ablation_v12;")[0].get("n"), 1)
        controls = self.controls()
        canaries = self.canaries()
        closed = [r for r in canaries if str(r.get("status")) == "CLOSED"]
        output: List[Dict[str, Any]] = []

        for sd in self.scenario_defs():
            control_filter: Callable[[Dict[str, Any]], bool] = sd["control_filter"]
            canary_filter: Callable[[Dict[str, Any]], bool] = sd["canary_filter"]
            eligible_controls = [r for r in controls if control_filter(r)]
            scenario_closed = [r for r in closed if canary_filter(r)]
            opened = [r for r in canaries if canary_filter(r)]
            vals = [fnum(r.get("net_pnl_r", r.get("pnl_r"))) for r in scenario_closed]
            metrics = self.kpi(vals)
            st = self.state_for(sd["name"], len(eligible_controls), metrics["closed_n"], metrics["profit_factor"], metrics["expectancy_r"], metrics["max_drawdown_r"])
            payload = {
                "scenario": sd["name"],
                "eligible_control_ids_sample": [r.get("id") for r in eligible_controls[-20:]],
                "closed_canary_ids_sample": [r.get("id") for r in scenario_closed[-20:]],
                "observational_not_counterfactual": True,
                "paper_only": True,
            }
            self.db.execute("""
                INSERT INTO institutional_ablation_v12 (
                    ts, version, refresh_id, scenario, scenario_rank,
                    eligible_control_n, opened_n, closed_n, wins, losses, winrate,
                    gross_profit_r, gross_loss_r, profit_factor, expectancy_r, max_drawdown_r,
                    last5_expectancy_r, ablation_state, hard_vetoes, interpretation, payload
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """, (
                utc_now_iso(), VERSION, refresh_id, sd["name"], sd["rank"],
                len(eligible_controls), len(opened), metrics["closed_n"], metrics["wins"], metrics["losses"], metrics["winrate"],
                metrics["gross_profit_r"], metrics["gross_loss_r"], metrics["profit_factor"], metrics["expectancy_r"], metrics["max_drawdown_r"],
                metrics["last5_expectancy_r"], st["state"], js(st["vetoes"]), sd["interpretation"], js(payload),
            ))
            output.append({
                "scenario": sd["name"],
                "eligible_control_n": len(eligible_controls),
                "opened_n": len(opened),
                "closed_n": metrics["closed_n"],
                "profit_factor": round(metrics["profit_factor"], 4),
                "expectancy_r": round(metrics["expectancy_r"], 4),
                "ablation_state": st["state"],
                "hard_vetoes": st["vetoes"],
            })
        return {"version": VERSION, "refresh_id": refresh_id, "scenarios": output}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    engine = InstitutionalAblationEngineV12()
    if args.refresh:
        print(json.dumps(engine.refresh(), indent=2, sort_keys=True, default=str))
    else:
        engine.ensure_schema()
        print(json.dumps({"version": VERSION, "schema": "ok"}, indent=2))


if __name__ == "__main__":
    main()
