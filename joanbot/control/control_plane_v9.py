from __future__ import annotations

import argparse
import json
from typing import Any, Dict

from joanbot.storage.db import get_db
from joanbot.utils import utc_now_iso

VERSION = "INSTITUTIONAL_CONTROL_PLANE_V9_2"


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


class InstitutionalControlPlaneV9:
    """
    Final V9 control layer:
    exact edge + robustness + regime + canary feedback.

    Allows only paper micro-canary.
    Never allows standard/direct opens.
    """

    def __init__(self, db=None) -> None:
        self.db = db or get_db()

    def ensure_schema(self) -> None:
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS institutional_control_plane_v9 (
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

                regime_state TEXT NOT NULL,
                regime_score REAL NOT NULL,
                feedback_state TEXT NOT NULL,

                open_legacy_positions INTEGER NOT NULL,
                open_canaries INTEGER NOT NULL,
                today_canaries INTEGER NOT NULL,

                hard_vetoes TEXT NOT NULL,
                reasons TEXT NOT NULL,
                control_contract_json TEXT NOT NULL,
                payload TEXT NOT NULL
            );
        """)

        self.db.execute("DROP VIEW IF EXISTS latest_institutional_control_plane_v9;")
        self.db.execute("""
            CREATE VIEW latest_institutional_control_plane_v9 AS
            SELECT *
            FROM institutional_control_plane_v9
            ORDER BY id DESC
            LIMIT 1;
        """)

    def q1(self, sql: str) -> Dict[str, Any]:
        try:
            rows = self.db.query(sql)
            return dict(rows[0]) if rows else {}
        except Exception:
            return {}

    def best_validated_edge(self) -> Dict[str, Any]:
        return self.q1("""
            SELECT *
            FROM latest_edge_robustness_validator_v9
            ORDER BY
              canary_permission DESC,
              robustness_score DESC,
              lcb_r DESC,
              avg_r DESC,
              n DESC
            LIMIT 1;
        """)

    def regime(self) -> Dict[str, Any]:
        return self.q1("SELECT * FROM latest_regime_adaptive_router_v6;")

    def feedback(self) -> Dict[str, Any]:
        return self.q1("SELECT * FROM latest_micro_canary_outcome_feedback_v9;")

    def open_legacy_positions(self) -> int:
        r = self.q1("SELECT COUNT(*) AS n FROM positions WHERE status='OPEN';")
        return inum(r.get("n"))

    def open_canaries(self) -> int:
        r = self.q1("SELECT COUNT(*) AS n FROM paper_micro_canary_positions_v9 WHERE status='OPEN';")
        return inum(r.get("n"))

    def today_canaries(self) -> int:
        r = self.q1("""
            SELECT COUNT(*) AS n
            FROM paper_micro_canary_positions_v9
            WHERE substr(opened_at,1,10)=substr(datetime('now'),1,10);
        """)
        return inum(r.get("n"))

    def refresh(self) -> Dict[str, Any]:
        self.ensure_schema()

        e = self.best_validated_edge()
        r = self.regime()
        f = self.feedback()

        open_legacy = self.open_legacy_positions()
        open_canaries = self.open_canaries()
        today_canaries = self.today_canaries()

        hard_vetoes = []
        reasons = []

        allow_standard = 0
        allow_direct = 0
        allow_micro = 0
        force_learning = 1
        veto_new = 1
        max_size = 0.0
        max_daily = 1
        mode = "NONE"

        validation = str(e.get("validation_state") or "NO_VALIDATED_EDGE")
        regime_state = str(r.get("regime_state") or "NO_REGIME")
        feedback_state = str(f.get("feedback_state") or "NO_FEEDBACK")

        if open_legacy > 0:
            global_state = "MANAGE_LEGACY_ONLY"
            action = "NO_NEW_CANARY"
            next_build = "LEGACY_POSITION_EXIT_OR_RECONCILIATION"
            hard_vetoes.append("OPEN_LEGACY_POSITION_EXISTS")

        elif open_canaries > 0:
            global_state = "MICRO_CANARY_ACTIVE"
            action = "MANAGE_EXISTING_MICRO_CANARY"
            next_build = "MICRO_CANARY_OUTCOME_FEEDBACK_V9"
            hard_vetoes.append("MICRO_CANARY_ALREADY_OPEN")

        elif today_canaries >= 1:
            global_state = "DAILY_CANARY_LIMIT_REACHED"
            action = "WAIT_NEXT_SESSION"
            next_build = "MICRO_CANARY_OUTCOME_FEEDBACK_V9"
            hard_vetoes.append("DAILY_CANARY_LIMIT_REACHED")

        elif feedback_state == "CANARY_COOLDOWN":
            global_state = "CANARY_COOLDOWN"
            action = "BLOCK_CANARY_UNTIL_FEEDBACK_RECOVERS"
            next_build = "EDGE_REVALIDATION_AFTER_CANARY_COOLDOWN"
            hard_vetoes.append("CANARY_FEEDBACK_COOLDOWN")

        elif validation == "ROBUST_EDGE_READY" and inum(e.get("canary_permission")) == 1 and regime_state == "REGIME_SUPPORTS_CLUSTER":
            global_state = "PAPER_MICRO_CANARY_READY"
            action = "OPEN_ONE_FULL_PAPER_MICRO_CANARY"
            next_build = "MICRO_CANARY_OUTCOME_FEEDBACK_V9"
            allow_micro = 1
            force_learning = 0
            veto_new = 0
            max_size = 50.0
            mode = "PAPER_MICRO_CANARY"
            reasons.append("ROBUST_EDGE_READY")
            reasons.append("REGIME_SUPPORTS_CLUSTER")
            reasons.append("CANARY_FEEDBACK_OK_OR_EMPTY")

        elif (
            validation == "ROBUST_EDGE_READY"
            and inum(e.get("canary_permission")) == 1
            and regime_state == "REGIME_MIXED_CLUSTER_REVIEW_ONLY"
            and fnum(e.get("recent20_avg_r")) >= 0.03
            and fnum(e.get("recent50_lcb_r")) >= 0.0
        ):
            global_state = "PAPER_MICRO_CANARY_PROBE_READY"
            action = "OPEN_ONE_REDUCED_PAPER_MICRO_CANARY"
            next_build = "MICRO_CANARY_OUTCOME_FEEDBACK_V9"
            allow_micro = 1
            force_learning = 0
            veto_new = 0
            max_size = 15.0
            mode = "PAPER_MICRO_CANARY_PROBE"
            reasons.append("ROBUST_EDGE_READY")
            reasons.append("REGIME_MIXED_BUT_NOT_TOXIC")
            reasons.append("REDUCED_SIZE_PROBE")

        elif validation == "ROBUST_EDGE_READY":
            global_state = "ROBUST_EDGE_REGIME_BLOCKED"
            action = "WAIT_FOR_REGIME_SUPPORT"
            next_build = "REGIME_ADAPTIVE_ROUTER_V9"
            hard_vetoes.append("REGIME_NOT_SUPPORTIVE")

        elif validation == "ROBUST_EDGE_CANDIDATE":
            global_state = "ROBUST_EDGE_CANDIDATE"
            action = "COLLECT_MORE_SAMPLE"
            next_build = "EDGE_FACTORY_SAMPLE_EXPANSION_V9"
            hard_vetoes.append("EDGE_NOT_READY_FOR_CANARY")

        else:
            global_state = "LEARNING_ONLY"
            action = "NO_ROBUST_EDGE"
            next_build = "EDGE_FACTORY_V9_REFINEMENT"
            hard_vetoes.append("NO_ROBUST_EDGE_READY")

        score = 0.0
        score += min(35.0, max(0.0, fnum(e.get("lcb_r"))) * 500.0)
        score += min(25.0, max(0.0, fnum(e.get("avg_r"))) * 120.0)
        score += min(20.0, fnum(e.get("robustness_score")) * 0.20)
        score += min(20.0, max(0.0, fnum(r.get("regime_score"))) * 0.5)

        contract = {
            "version": VERSION,
            "global_state": global_state,
            "allow_standard_open": allow_standard,
            "allow_direct_open": allow_direct,
            "allow_paper_micro_canary": allow_micro,
            "force_learning_only": force_learning,
            "veto_new_positions": veto_new,
            "max_size_usd": max_size,
            "max_daily_canaries": max_daily,
            "required_execution_mode": mode,
            "source_edge_id": inum(e.get("source_edge_id")),
            "hard_vetoes": hard_vetoes,
        }

        payload = {
            "validated_edge": e,
            "regime": r,
            "feedback": f,
            "open_legacy_positions": open_legacy,
            "open_canaries": open_canaries,
            "today_canaries": today_canaries,
            "paper_only": True,
        }

        self.db.execute("""
            INSERT INTO institutional_control_plane_v9 (
                ts, version, global_state, control_score,
                allow_standard_open, allow_direct_open, allow_paper_micro_canary,
                force_learning_only, veto_new_positions,
                max_size_usd, max_daily_canaries, required_execution_mode,
                recommended_action, next_required_build,
                source_edge_id, edge_symbol, edge_side, edge_family, edge_setup, edge_profile, edge_horizon_min,
                edge_n, edge_avg_r, edge_lcb_r, edge_winrate, robustness_score, validation_state,
                regime_state, regime_score, feedback_state,
                open_legacy_positions, open_canaries, today_canaries,
                hard_vetoes, reasons, control_contract_json, payload
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """, (
            utc_now_iso(), VERSION, global_state, round(score, 4),
            allow_standard, allow_direct, allow_micro,
            force_learning, veto_new,
            max_size, max_daily, mode,
            action, next_build,
            inum(e.get("source_edge_id")), e.get("symbol"), e.get("side"), e.get("family_name"), e.get("setup"), e.get("profile"), inum(e.get("horizon_min")),
            inum(e.get("n")), fnum(e.get("avg_r")), fnum(e.get("lcb_r")), fnum(e.get("winrate")), fnum(e.get("robustness_score")), validation,
            regime_state, fnum(r.get("regime_score")), feedback_state,
            open_legacy, open_canaries, today_canaries,
            js(hard_vetoes), js(reasons), js(contract), js(payload),
        ))

        return {
            "version": VERSION,
            "global_state": global_state,
            "control_score": round(score, 4),
            "allow_paper_micro_canary": allow_micro,
            "recommended_action": action,
            "next_required_build": next_build,
            "validation_state": validation,
            "regime_state": regime_state,
            "feedback_state": feedback_state,
            "hard_vetoes": hard_vetoes,
            "reasons": reasons,
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()

    engine = InstitutionalControlPlaneV9()

    if args.refresh:
        print(json.dumps(engine.refresh(), indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
