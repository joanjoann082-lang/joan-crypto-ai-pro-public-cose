from __future__ import annotations

import argparse
import json
from typing import Any, Dict

from joanbot.storage.db import get_db
from joanbot.utils import utc_now_iso

VERSION = "REGIME_ADAPTIVE_ROUTER_V6"


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


class RegimeAdaptiveRouterV6:
    """
    Regime router for cluster evidence.

    Reads:
    - latest_alpha_cluster_aggregator_v6
    - universal_shadow_results_v2

    Writes:
    - regime_adaptive_router_v6
    - latest_regime_adaptive_router_v6

    Does not mutate execution/trading tables.
    """

    def __init__(self, db=None) -> None:
        self.db = db or get_db()

    def ensure_schema(self) -> None:
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS regime_adaptive_router_v6 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                version TEXT NOT NULL,

                regime_state TEXT NOT NULL,
                regime_score REAL NOT NULL,

                selected_symbol TEXT,
                selected_side TEXT,
                selected_family TEXT,
                selected_horizon_min INTEGER NOT NULL,

                cluster_n INTEGER NOT NULL,
                cluster_avg_r REAL NOT NULL,
                cluster_lcb_r REAL NOT NULL,
                cluster_winrate REAL NOT NULL,
                cluster_score REAL NOT NULL,
                cluster_state TEXT NOT NULL,

                shadow_100_avg_r REAL NOT NULL,
                shadow_300_avg_r REAL NOT NULL,
                shadow_600_avg_r REAL NOT NULL,

                allow_cluster_review INTEGER NOT NULL,
                allow_micro_canary_candidate INTEGER NOT NULL,
                hard_vetoes TEXT NOT NULL,
                reasons TEXT NOT NULL,
                payload TEXT NOT NULL
            );
        """)

        self.db.execute("DROP VIEW IF EXISTS latest_regime_adaptive_router_v6;")
        self.db.execute("""
            CREATE VIEW latest_regime_adaptive_router_v6 AS
            SELECT *
            FROM regime_adaptive_router_v6
            ORDER BY id DESC
            LIMIT 1;
        """)

    def q1(self, sql: str) -> Dict[str, Any]:
        try:
            rows = self.db.query(sql)
            return dict(rows[0]) if rows else {}
        except Exception:
            return {}

    def shadow_avg(self, n: int) -> float:
        r = self.q1(f"""
            SELECT COALESCE(AVG(result_r), 0) AS avg_r
            FROM (
              SELECT result_r
              FROM universal_shadow_results_v2
              WHERE result_r IS NOT NULL
              ORDER BY id DESC
              LIMIT {int(n)}
            );
        """)
        return fnum(r.get("avg_r"))

    def best_cluster(self) -> Dict[str, Any]:
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

    def refresh(self) -> Dict[str, Any]:
        self.ensure_schema()

        c = self.best_cluster()
        sh100 = self.shadow_avg(100)
        sh300 = self.shadow_avg(300)
        sh600 = self.shadow_avg(600)

        hard_vetoes = []
        reasons = []

        state = str(c.get("cluster_state") or "NO_CLUSTER")
        cluster_n = inum(c.get("n"))
        cluster_avg = fnum(c.get("avg_r"))
        cluster_lcb = fnum(c.get("lcb_r"))
        cluster_wr = fnum(c.get("winrate"))
        cluster_score = fnum(c.get("cluster_score"))

        allow_review = 0
        allow_micro_candidate = 0

        if not c:
            regime_state = "NO_CLUSTER_EDGE"
            hard_vetoes.append("NO_CLUSTER_AVAILABLE")

        elif state == "CLUSTER_READY_FOR_REGIME" and cluster_lcb > 0 and sh100 >= -0.025 and sh300 >= -0.035:
            regime_state = "REGIME_SUPPORTS_CLUSTER"
            allow_review = 1
            allow_micro_candidate = 1
            reasons.append("CLUSTER_READY_AND_RECENT_SHADOW_NOT_TOXIC")

        elif state in ("CLUSTER_READY_FOR_REGIME", "CLUSTER_CANDIDATE") and sh100 < -0.03 and sh300 < -0.02:
            regime_state = "REGIME_BLOCKS_CLUSTER"
            hard_vetoes.append("RECENT_SHADOW_NEGATIVE")

        elif state in ("CLUSTER_READY_FOR_REGIME", "CLUSTER_CANDIDATE"):
            regime_state = "REGIME_MIXED_CLUSTER_REVIEW_ONLY"
            allow_review = 1
            hard_vetoes.append("REGIME_NOT_CONFIRMED")

        else:
            regime_state = "DEFENSIVE_LEARNING"
            hard_vetoes.append("NO_ACTIONABLE_CLUSTER")

        regime_score = 0.0
        regime_score += min(35.0, max(0.0, cluster_lcb) * 500.0)
        regime_score += min(25.0, max(0.0, cluster_avg) * 100.0)
        regime_score += min(20.0, max(0.0, sh100) * 250.0)
        regime_score += min(20.0, max(0.0, sh300) * 250.0)

        payload = {
            "cluster": c,
            "shadow": {"100": sh100, "300": sh300, "600": sh600},
            "no_execution_permission": True,
        }

        self.db.execute("""
            INSERT INTO regime_adaptive_router_v6 (
                ts, version,
                regime_state, regime_score,
                selected_symbol, selected_side, selected_family, selected_horizon_min,
                cluster_n, cluster_avg_r, cluster_lcb_r, cluster_winrate, cluster_score, cluster_state,
                shadow_100_avg_r, shadow_300_avg_r, shadow_600_avg_r,
                allow_cluster_review, allow_micro_canary_candidate,
                hard_vetoes, reasons, payload
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """, (
            utc_now_iso(), VERSION,
            regime_state, round(regime_score, 4),
            c.get("symbol"), c.get("side"), c.get("family_name"), inum(c.get("horizon_min")),
            cluster_n, cluster_avg, cluster_lcb, cluster_wr, cluster_score, state,
            sh100, sh300, sh600,
            allow_review, allow_micro_candidate,
            js(hard_vetoes), js(reasons), js(payload),
        ))

        return {
            "version": VERSION,
            "regime_state": regime_state,
            "regime_score": round(regime_score, 4),
            "allow_cluster_review": allow_review,
            "allow_micro_canary_candidate": allow_micro_candidate,
            "hard_vetoes": hard_vetoes,
            "reasons": reasons,
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()

    engine = RegimeAdaptiveRouterV6()

    if args.refresh:
        print(json.dumps(engine.refresh(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
