from __future__ import annotations

import argparse
import json
from typing import Any, Dict

from joanbot.storage.db import get_db
from joanbot.utils import utc_now_iso

VERSION = "INSTITUTIONAL_CONTROL_PLANE_V7"


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
    return json.dumps(x, separators=(",", ":"), ensure_ascii=False)


class InstitutionalControlPlaneV7:
    """
    Canonical V7 control plane.

    Reads:
    - latest_alpha_promotion_contract_v5
    - latest_alpha_cluster_aggregator_v6
    - latest_regime_adaptive_router_v6
    - trades
    - positions

    Writes:
    - institutional_control_plane_v7
    - latest_institutional_control_plane_v7

    No direct execution permission.
    """

    def __init__(self, db=None) -> None:
        self.db = db or get_db()

    def ensure_schema(self) -> None:
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS institutional_control_plane_v7 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                version TEXT NOT NULL,

                global_state TEXT NOT NULL,
                control_score REAL NOT NULL,

                allow_standard_open INTEGER NOT NULL,
                allow_direct_open INTEGER NOT NULL,
                allow_paper_micro_canary INTEGER NOT NULL,
                micro_canary_candidate INTEGER NOT NULL,
                force_learning_only INTEGER NOT NULL,
                veto_new_positions INTEGER NOT NULL,

                recommended_action TEXT NOT NULL,
                next_required_build TEXT NOT NULL,
                required_execution_mode TEXT NOT NULL,

                cluster_symbol TEXT,
                cluster_side TEXT,
                cluster_family TEXT,
                cluster_horizon_min INTEGER NOT NULL,
                cluster_n INTEGER NOT NULL,
                cluster_avg_r REAL NOT NULL,
                cluster_lcb_r REAL NOT NULL,
                cluster_winrate REAL NOT NULL,
                cluster_score REAL NOT NULL,
                cluster_state TEXT NOT NULL,

                regime_state TEXT NOT NULL,
                regime_score REAL NOT NULL,

                contracts_n INTEGER NOT NULL,
                micro_contract_ready INTEGER NOT NULL,
                max_contract_lcb REAL NOT NULL,
                max_tensor_quality REAL NOT NULL,

                open_positions INTEGER NOT NULL,
                last_trade_ts TEXT,
                last10_pnl_usd REAL NOT NULL,

                hard_vetoes TEXT NOT NULL,
                reasons TEXT NOT NULL,
                control_contract_json TEXT NOT NULL,
                payload TEXT NOT NULL
            );
        """)

        self.db.execute("DROP VIEW IF EXISTS latest_institutional_control_plane_v7;")
        self.db.execute("""
            CREATE VIEW latest_institutional_control_plane_v7 AS
            SELECT *
            FROM institutional_control_plane_v7
            ORDER BY id DESC
            LIMIT 1;
        """)

    def q1(self, sql: str) -> Dict[str, Any]:
        try:
            rows = self.db.query(sql)
            return dict(rows[0]) if rows else {}
        except Exception:
            return {}

    def contract_summary(self) -> Dict[str, Any]:
        return self.q1("""
            SELECT
              COUNT(*) AS contracts_n,
              COALESCE(SUM(allowed_paper_micro_canary),0) AS micro_ready,
              COALESCE(MAX(posterior_lcb_r),0) AS max_lcb,
              COALESCE(MAX(tensor_quality),0) AS max_tensor
            FROM latest_alpha_promotion_contract_v5;
        """)

    def cluster(self) -> Dict[str, Any]:
        return self.q1("""
            SELECT *
            FROM latest_alpha_cluster_aggregator_v6
            ORDER BY
              CASE cluster_state
                WHEN 'CLUSTER_READY_FOR_REGIME' THEN 3
                WHEN 'CLUSTER_CANDIDATE' THEN 2
                ELSE 1
              END DESC,
              cluster_score DESC,
              lcb_r DESC,
              avg_r DESC
            LIMIT 1;
        """)

    def regime(self) -> Dict[str, Any]:
        return self.q1("SELECT * FROM latest_regime_adaptive_router_v6;")

    def open_positions(self) -> int:
        r = self.q1("SELECT COUNT(*) AS n FROM positions WHERE status='OPEN';")
        return inum(r.get("n"))

    def trades10(self) -> Dict[str, Any]:
        return self.q1("""
            SELECT
              COALESCE(SUM(pnl_usd),0) AS pnl_usd,
              MAX(ts) AS last_trade_ts
            FROM (
              SELECT *
              FROM trades
              WHERE pnl_usd IS NOT NULL
              ORDER BY id DESC
              LIMIT 10
            );
        """)

    def refresh(self) -> Dict[str, Any]:
        self.ensure_schema()

        c = self.contract_summary()
        cl = self.cluster()
        rg = self.regime()
        op = self.open_positions()
        tr = self.trades10()

        hard_vetoes = []
        reasons = []

        allow_standard = 0
        allow_direct = 0
        allow_micro = 0
        micro_candidate = 0
        force_learning = 1
        veto_new = 1
        required_mode = "NONE"

        cluster_state = str(cl.get("cluster_state") or "NO_CLUSTER")
        regime_state = str(rg.get("regime_state") or "NO_REGIME")

        if op > 0:
            global_state = "MANAGE_ONLY"
            action = "MANAGE_EXISTING_POSITION_NO_NEW_OPEN"
            next_build = "POSITION_OUTCOME_FEEDBACK_V7"
            hard_vetoes.append("OPEN_POSITIONS_EXIST")

        elif cluster_state == "CLUSTER_READY_FOR_REGIME" and regime_state == "REGIME_SUPPORTS_CLUSTER":
            global_state = "MICRO_CANARY_BRIDGE_REQUIRED"
            action = "BUILD_PAPER_MICRO_CANARY_BRIDGE_V7"
            next_build = "PAPER_MICRO_CANARY_BRIDGE_V7"
            micro_candidate = 1
            hard_vetoes.append("NO_MICRO_CANARY_BRIDGE_INSTALLED")
            reasons.append("CLUSTER_READY_AND_REGIME_SUPPORTIVE")

        elif cluster_state in ("CLUSTER_READY_FOR_REGIME", "CLUSTER_CANDIDATE"):
            global_state = "CLUSTER_EDGE_READY_REGIME_PENDING"
            action = "KEEP_LEARNING_VALIDATE_REGIME"
            next_build = "REGIME_ADAPTIVE_ROUTER_V7"
            hard_vetoes.append("REGIME_NOT_SUPPORTIVE_ENOUGH")
            reasons.append("CLUSTER_EDGE_EXISTS")

        elif regime_state == "REGIME_BLOCKS_CLUSTER":
            global_state = "DEFENSIVE_LEARNING_ONLY"
            action = "BLOCK_NEW_OPENS_RECENT_EDGE_TOXIC"
            next_build = "REGIME_ADAPTIVE_ROUTER_V7"
            hard_vetoes.append("RECENT_REGIME_BLOCK")

        else:
            global_state = "LEARNING_ONLY"
            action = "ACCUMULATE_CLUSTER_AND_REGIME_EVIDENCE"
            next_build = "ALPHA_CLUSTER_AGGREGATOR_V7"
            hard_vetoes.append("NO_ACTIONABLE_CLUSTER")

        control_score = 0.0
        control_score += min(35.0, max(0.0, fnum(cl.get("lcb_r"))) * 500.0)
        control_score += min(25.0, max(0.0, fnum(cl.get("avg_r"))) * 120.0)
        control_score += min(20.0, max(0.0, fnum(rg.get("regime_score"))) * 0.5)
        control_score += min(20.0, max(0.0, fnum(cl.get("cluster_score"))) * 0.2)

        contract = {
            "version": VERSION,
            "global_state": global_state,
            "allow_standard_open": allow_standard,
            "allow_direct_open": allow_direct,
            "allow_paper_micro_canary": allow_micro,
            "micro_canary_candidate": micro_candidate,
            "force_learning_only": force_learning,
            "veto_new_positions": veto_new,
            "required_execution_mode": required_mode,
            "next_required_build": next_build,
            "hard_vetoes": hard_vetoes,
        }

        payload = {
            "cluster": cl,
            "regime": rg,
            "contracts": c,
            "trades10": tr,
            "open_positions": op,
            "no_execution_permission": True,
        }

        self.db.execute("""
            INSERT INTO institutional_control_plane_v7 (
                ts, version, global_state, control_score,
                allow_standard_open, allow_direct_open, allow_paper_micro_canary,
                micro_canary_candidate, force_learning_only, veto_new_positions,
                recommended_action, next_required_build, required_execution_mode,
                cluster_symbol, cluster_side, cluster_family, cluster_horizon_min,
                cluster_n, cluster_avg_r, cluster_lcb_r, cluster_winrate, cluster_score, cluster_state,
                regime_state, regime_score,
                contracts_n, micro_contract_ready, max_contract_lcb, max_tensor_quality,
                open_positions, last_trade_ts, last10_pnl_usd,
                hard_vetoes, reasons, control_contract_json, payload
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """, (
            utc_now_iso(), VERSION, global_state, round(control_score, 4),
            allow_standard, allow_direct, allow_micro,
            micro_candidate, force_learning, veto_new,
            action, next_build, required_mode,
            cl.get("symbol"), cl.get("side"), cl.get("family_name"), inum(cl.get("horizon_min")),
            inum(cl.get("n")), fnum(cl.get("avg_r")), fnum(cl.get("lcb_r")), fnum(cl.get("winrate")), fnum(cl.get("cluster_score")), cluster_state,
            regime_state, fnum(rg.get("regime_score")),
            inum(c.get("contracts_n")), inum(c.get("micro_ready")), fnum(c.get("max_lcb")), fnum(c.get("max_tensor")),
            op, tr.get("last_trade_ts"), fnum(tr.get("pnl_usd")),
            js(hard_vetoes), js(reasons), js(contract), js(payload),
        ))

        return {
            "version": VERSION,
            "global_state": global_state,
            "control_score": round(control_score, 4),
            "recommended_action": action,
            "next_required_build": next_build,
            "micro_canary_candidate": micro_candidate,
            "hard_vetoes": hard_vetoes,
            "reasons": reasons,
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()

    engine = InstitutionalControlPlaneV7()

    if args.refresh:
        print(json.dumps(engine.refresh(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
