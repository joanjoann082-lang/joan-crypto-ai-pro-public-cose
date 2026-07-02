from __future__ import annotations

import argparse
import json
from typing import Any, Dict, List

from joanbot.storage.db import get_db
from joanbot.utils import utc_now_iso

VERSION = "INSTITUTIONAL_CONTROL_PLANE_V10_PRE_API"


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


class InstitutionalControlPlaneV10:
    """
    Final pre-paid-API control layer.

    Required stack:
    robust exact edge + shadow regime + Binance derivatives regime + canary feedback.

    It can only permit paper micro-canary. Standard/direct opens remain hard-disabled.
    """

    def __init__(self, db=None) -> None:
        self.db = db or get_db()

    def ensure_schema(self) -> None:
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS institutional_control_plane_v10 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                version TEXT NOT NULL,
                global_state TEXT NOT NULL,
                control_score REAL NOT NULL,

                allow_standard_open INTEGER NOT NULL,
                allow_direct_open INTEGER NOT NULL,
                allow_paper_micro_canary INTEGER NOT NULL,
                force_learning_only INTEGER NOT NULL,
                veto_new_positions INTEGER NOT NULL,

                max_size_usd REAL NOT NULL,
                max_daily_canaries INTEGER NOT NULL,
                required_execution_mode TEXT NOT NULL,
                recommended_action TEXT NOT NULL,
                next_required_build TEXT NOT NULL,

                source_edge_id INTEGER NOT NULL,
                edge_symbol TEXT,
                edge_side TEXT,
                edge_family TEXT,
                edge_setup TEXT,
                edge_profile TEXT,
                edge_horizon_min INTEGER NOT NULL,
                edge_n INTEGER NOT NULL,
                edge_avg_r REAL NOT NULL,
                edge_lcb_r REAL NOT NULL,
                edge_winrate REAL NOT NULL,
                robustness_score REAL NOT NULL,
                validation_state TEXT NOT NULL,

                shadow_regime_state TEXT NOT NULL,
                shadow_regime_score REAL NOT NULL,
                derivatives_state TEXT NOT NULL,
                derivatives_score REAL NOT NULL,
                derivatives_data_quality REAL NOT NULL,
                feedback_state TEXT NOT NULL,
                kpi_state TEXT NOT NULL,

                open_legacy_positions INTEGER NOT NULL,
                open_canaries INTEGER NOT NULL,
                today_canaries INTEGER NOT NULL,

                hard_vetoes TEXT NOT NULL,
                reasons TEXT NOT NULL,
                control_contract_json TEXT NOT NULL,
                payload TEXT NOT NULL
            );
        """)
        self.db.execute("DROP VIEW IF EXISTS latest_institutional_control_plane_v10;")
        self.db.execute("""
            CREATE VIEW latest_institutional_control_plane_v10 AS
            SELECT * FROM institutional_control_plane_v10
            ORDER BY id DESC LIMIT 1;
        """)

    def q1(self, sql: str, params=()) -> Dict[str, Any]:
        try:
            rows = self.db.query(sql, params)
            return dict(rows[0]) if rows else {}
        except Exception:
            return {}

    def best_edge(self) -> Dict[str, Any]:
        return self.q1("""
            SELECT * FROM latest_edge_robustness_validator_v9
            ORDER BY canary_permission DESC, robustness_score DESC, lcb_r DESC, avg_r DESC, n DESC
            LIMIT 1;
        """)

    def open_legacy_positions(self) -> int:
        r = self.q1("SELECT COUNT(*) AS n FROM positions WHERE status='OPEN';")
        return inum(r.get("n"))

    def open_canaries(self) -> int:
        r = self.q1("SELECT COUNT(*) AS n FROM paper_micro_canary_positions_v10 WHERE status='OPEN';")
        return inum(r.get("n"))

    def today_canaries(self) -> int:
        r = self.q1("""
            SELECT COUNT(*) AS n
            FROM paper_micro_canary_positions_v10
            WHERE substr(opened_at,1,10)=substr(datetime('now'),1,10);
        """)
        return inum(r.get("n"))

    def refresh(self) -> Dict[str, Any]:
        self.ensure_schema()
        e = self.best_edge()
        r = self.q1("SELECT * FROM latest_regime_adaptive_router_v6;")
        d = self.q1("SELECT * FROM latest_derivatives_regime_v10;")
        f = self.q1("SELECT * FROM latest_micro_canary_outcome_feedback_v10;")
        k = self.q1("SELECT * FROM latest_micro_canary_kpi_v10;")

        validation = str(e.get("validation_state") or "NO_VALIDATED_EDGE")
        shadow_state = str(r.get("regime_state") or "NO_SHADOW_REGIME")
        derivatives_state = str(d.get("derivatives_state") or "NO_DERIVATIVES_REGIME")
        feedback_state = str(f.get("feedback_state") or "NO_FEEDBACK")
        kpi_state = str(k.get("kpi_state") or "NO_KPI")

        open_legacy = self.open_legacy_positions()
        open_canaries = self.open_canaries()
        today_canaries = self.today_canaries()

        hard_vetoes: List[str] = []
        reasons: List[str] = []
        allow_standard = 0
        allow_direct = 0
        allow_micro = 0
        force_learning = 1
        veto_new = 1
        max_size = 0.0
        max_daily = 1
        mode = "NONE"

        if open_legacy > 0:
            global_state = "MANAGE_LEGACY_ONLY"
            action = "NO_NEW_CANARY"
            next_build = "LEGACY_POSITION_EXIT_OR_RECONCILIATION"
            hard_vetoes.append("OPEN_LEGACY_POSITION_EXISTS")
        elif open_canaries > 0:
            global_state = "MICRO_CANARY_ACTIVE"
            action = "MANAGE_EXISTING_MICRO_CANARY"
            next_build = "MICRO_CANARY_OUTCOME_FEEDBACK_V10"
            hard_vetoes.append("MICRO_CANARY_ALREADY_OPEN")
        elif today_canaries >= max_daily:
            global_state = "DAILY_CANARY_LIMIT_REACHED"
            action = "WAIT_NEXT_SESSION"
            next_build = "MICRO_CANARY_OUTCOME_FEEDBACK_V10"
            hard_vetoes.append("DAILY_CANARY_LIMIT_REACHED")
        elif feedback_state == "CANARY_COOLDOWN":
            global_state = "CANARY_COOLDOWN"
            action = "BLOCK_CANARY_UNTIL_FEEDBACK_RECOVERS"
            next_build = "EDGE_REVALIDATION_AFTER_CANARY_COOLDOWN"
            hard_vetoes.append("CANARY_FEEDBACK_COOLDOWN")
        elif validation != "ROBUST_EDGE_READY" or inum(e.get("canary_permission")) != 1:
            global_state = "LEARNING_ONLY" if validation != "ROBUST_EDGE_CANDIDATE" else "ROBUST_EDGE_CANDIDATE"
            action = "NO_CANARY"
            next_build = "ROBUSTNESS_SAMPLE_EXPANSION"
            hard_vetoes.append("NO_ROBUST_EDGE_READY")
        elif str(d.get("veto_canary")) == "1" or derivatives_state.startswith("DERIVATIVES_CONFLICT"):
            global_state = "DERIVATIVES_CONFLICT_BLOCK"
            action = "WAIT_FOR_DERIVATIVES_CONFIRMATION"
            next_build = "DERIVATIVES_REGIME_V10_MONITOR"
            hard_vetoes.append("DERIVATIVES_CONFLICT_OR_NOT_READY")
        elif shadow_state == "REGIME_BLOCKS_CLUSTER":
            global_state = "SHADOW_REGIME_BLOCKED"
            action = "WAIT_FOR_SHADOW_REGIME_SUPPORT"
            next_build = "REGIME_ADAPTIVE_ROUTER_V10"
            hard_vetoes.append("SHADOW_REGIME_BLOCKS_EDGE")
        elif derivatives_state.startswith("DERIVATIVES_CONFIRM") and shadow_state == "REGIME_SUPPORTS_CLUSTER":
            global_state = "PAPER_MICRO_CANARY_READY"
            action = "OPEN_ONE_V10_PAPER_MICRO_CANARY"
            next_build = "MICRO_CANARY_OUTCOME_FEEDBACK_V10"
            allow_micro = 1
            force_learning = 0
            veto_new = 0
            max_size = 50.0
            mode = "PAPER_MICRO_CANARY_V10_FULL"
            reasons += ["ROBUST_EDGE_READY", "SHADOW_REGIME_SUPPORTS", "DERIVATIVES_CONFIRM_EDGE"]
        elif derivatives_state.startswith("DERIVATIVES_CONFIRM") and shadow_state == "REGIME_MIXED_CLUSTER_REVIEW_ONLY":
            global_state = "PAPER_MICRO_CANARY_PROBE_READY"
            action = "OPEN_ONE_REDUCED_V10_PAPER_MICRO_CANARY"
            next_build = "MICRO_CANARY_OUTCOME_FEEDBACK_V10"
            allow_micro = 1
            force_learning = 0
            veto_new = 0
            max_size = 15.0
            mode = "PAPER_MICRO_CANARY_V10_PROBE"
            reasons += ["ROBUST_EDGE_READY", "SHADOW_REGIME_MIXED", "DERIVATIVES_CONFIRM_EDGE", "REDUCED_SIZE"]
        elif derivatives_state.startswith("DERIVATIVES_NEUTRAL_SUPPORTIVE") and shadow_state == "REGIME_SUPPORTS_CLUSTER":
            global_state = "PAPER_MICRO_CANARY_REDUCED_READY"
            action = "OPEN_ONE_REDUCED_V10_PAPER_MICRO_CANARY"
            next_build = "MICRO_CANARY_OUTCOME_FEEDBACK_V10"
            allow_micro = 1
            force_learning = 0
            veto_new = 0
            max_size = 25.0
            mode = "PAPER_MICRO_CANARY_V10_REDUCED"
            reasons += ["ROBUST_EDGE_READY", "SHADOW_REGIME_SUPPORTS", "DERIVATIVES_NEUTRAL_SUPPORTIVE", "REDUCED_SIZE"]
        else:
            global_state = "WAIT_DERIVATIVES_OR_REGIME_CONFIRMATION"
            action = "WAIT"
            next_build = "DATA_SPINE_V10_ACCUMULATE"
            hard_vetoes.append("INSUFFICIENT_COMBINED_CONFIRMATION")

        score = 0.0
        score += min(30.0, max(0.0, fnum(e.get("lcb_r"))) * 500.0)
        score += min(20.0, max(0.0, fnum(e.get("avg_r"))) * 120.0)
        score += min(15.0, fnum(e.get("robustness_score")) * 0.15)
        score += min(15.0, max(0.0, fnum(r.get("regime_score"))) * 0.5)
        score += min(20.0, max(0.0, fnum(d.get("selected_score"))) * 0.4)

        contract = {
            "version": VERSION,
            "global_state": global_state,
            "allow_standard_open": allow_standard,
            "allow_direct_open": allow_direct,
            "allow_paper_micro_canary": allow_micro,
            "required_execution_mode": mode,
            "max_size_usd": max_size,
            "source_edge_id": inum(e.get("source_edge_id")),
            "hard_vetoes": hard_vetoes,
            "paper_only": True,
            "paid_api_required": False,
        }
        payload = {
            "edge": e,
            "shadow_regime": r,
            "derivatives_regime": d,
            "feedback": f,
            "kpi": k,
            "control_contract": contract,
        }

        self.db.execute("""
            INSERT INTO institutional_control_plane_v10 (
                ts, version, global_state, control_score,
                allow_standard_open, allow_direct_open, allow_paper_micro_canary,
                force_learning_only, veto_new_positions,
                max_size_usd, max_daily_canaries, required_execution_mode,
                recommended_action, next_required_build,
                source_edge_id, edge_symbol, edge_side, edge_family, edge_setup, edge_profile, edge_horizon_min,
                edge_n, edge_avg_r, edge_lcb_r, edge_winrate, robustness_score, validation_state,
                shadow_regime_state, shadow_regime_score, derivatives_state, derivatives_score, derivatives_data_quality,
                feedback_state, kpi_state, open_legacy_positions, open_canaries, today_canaries,
                hard_vetoes, reasons, control_contract_json, payload
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """, (
            utc_now_iso(), VERSION, global_state, score,
            allow_standard, allow_direct, allow_micro, force_learning, veto_new,
            max_size, max_daily, mode, action, next_build,
            inum(e.get("source_edge_id")), e.get("symbol"), e.get("side"), e.get("family_name"), e.get("setup"), e.get("profile"), inum(e.get("horizon_min")),
            inum(e.get("n")), fnum(e.get("avg_r")), fnum(e.get("lcb_r")), fnum(e.get("winrate")), fnum(e.get("robustness_score")), validation,
            shadow_state, fnum(r.get("regime_score")), derivatives_state, fnum(d.get("selected_score")), fnum(d.get("data_quality")),
            feedback_state, kpi_state, open_legacy, open_canaries, today_canaries,
            js(hard_vetoes), js(reasons), js(contract), js(payload),
        ))
        return {
            "version": VERSION,
            "global_state": global_state,
            "allow_paper_micro_canary": allow_micro,
            "max_size_usd": max_size,
            "validation_state": validation,
            "shadow_regime_state": shadow_state,
            "derivatives_state": derivatives_state,
            "feedback_state": feedback_state,
            "hard_vetoes": hard_vetoes,
            "reasons": reasons,
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    engine = InstitutionalControlPlaneV10()
    if args.refresh:
        print(json.dumps(engine.refresh(), indent=2, sort_keys=True, default=str))
    else:
        engine.ensure_schema()
        print(json.dumps({"version": VERSION, "schema": "ok"}, indent=2))


if __name__ == "__main__":
    main()
