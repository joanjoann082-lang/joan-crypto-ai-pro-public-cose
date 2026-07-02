from __future__ import annotations

import argparse
import json
import time
import traceback
from typing import Any, Dict

from joanbot.runner import Runner
from joanbot.config import CFG
from joanbot.alpha.alpha_cluster_aggregator_v6 import AlphaClusterAggregatorV6
from joanbot.alpha.regime_adaptive_router_v6 import RegimeAdaptiveRouterV6
from joanbot.alpha.institutional_edge_factory_v8 import InstitutionalEdgeFactoryV8
from joanbot.alpha.edge_robustness_validator_v9 import EdgeRobustnessValidatorV9
from joanbot.market.derivatives_data_spine_v10 import DerivativesDataSpineV10
from joanbot.alpha.derivatives_regime_v10 import DerivativesRegimeV10
from joanbot.execution.micro_canary_outcome_feedback_v11 import MicroCanaryOutcomeFeedbackV11
from joanbot.analytics.micro_canary_kpi_v11 import MicroCanaryKPIEngineV11
from joanbot.analytics.ablation_engine_v12 import InstitutionalAblationEngineV12
from joanbot.control.overlap_guard_v11 import OverlapGuardV11
from joanbot.institutional.decision_order_v11 import InstitutionalDecisionOrderV11
from joanbot.control.control_plane_v11 import InstitutionalControlPlaneV11
from joanbot.execution.paper_micro_canary_bridge_v11 import PaperMicroCanaryBridgeV11
from joanbot.control.api_readiness_gate_v11 import PaidApiReadinessGateV11
from joanbot.utils import utc_now_iso

VERSION = "INSTITUTIONAL_RUNTIME_V11_3_TERMUX_SAFE_SLIM"


class InstitutionalRuntimeV11:
    """
    Professional single-order runtime.

    The runner is not an execution authority. It is a data/context/shadow adapter.

    Strict pipeline:
    00 safety/legacy counts
    10 market/context/shadow adapter
    20 cluster aggregation
    30 exact edge factory
    40 robustness validator
    50 shadow regime
    60 free Binance derivatives spine
    70 derivatives regime
    80 V11 feedback + KPI
    90 overlap guard
    100 ordered stimulus contract
    110 final control plane
    120 isolated V11 paper micro-canary bridge
    130 post-trade feedback/KPI/readiness
    140 retention/state
    """

    def __init__(self, allow_canary: bool = True) -> None:
        self.adapter = Runner()
        self.db = self.adapter.db
        self.cycles = 0
        self.started = utc_now_iso()
        self.allow_canary = allow_canary

        self.cluster = AlphaClusterAggregatorV6(self.db)
        self.shadow_regime = RegimeAdaptiveRouterV6(self.db)
        self.edge_factory = InstitutionalEdgeFactoryV8(self.db)
        self.validator = EdgeRobustnessValidatorV9(self.db)
        self.derivatives_spine = DerivativesDataSpineV10(self.db)
        self.derivatives_regime = DerivativesRegimeV10(self.db)
        self.feedback = MicroCanaryOutcomeFeedbackV11(self.db)
        self.kpi = MicroCanaryKPIEngineV11(self.db)
        self.ablation = InstitutionalAblationEngineV12(self.db)
        self.overlap = OverlapGuardV11(self.db)
        self.order = InstitutionalDecisionOrderV11(self.db)
        self.control = InstitutionalControlPlaneV11(self.db)
        self.canary = PaperMicroCanaryBridgeV11(self.db)
        self.readiness = PaidApiReadinessGateV11(self.db)

        self.disable_legacy_path()

    def disable_legacy_path(self) -> None:
        def forbidden(*args, **kwargs):
            raise RuntimeError("LEGACY_TRADING_PATH_FORBIDDEN_BY_RUNTIME_V11")
        self.adapter.step_decisions = forbidden
        self.adapter.step_positions = forbidden
        if hasattr(self.adapter, "broker") and hasattr(self.adapter.broker, "open_from_decision"):
            self.adapter.broker.open_from_decision = forbidden

    def slim_event_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        out: Dict[str, Any] = {"version": VERSION}
        for k, v in payload.items():
            if k in {"cluster", "edge_factory", "validation", "shadow_regime", "derivatives_spine",
                     "derivatives_regime", "control", "canary", "paid_api_readiness",
                     "legacy_counts", "mode", "cycle", "allow_canary"}:
                out[k] = v
            elif k in {"ordered_pipeline"}:
                out[k] = v[:20] if isinstance(v, list) else v
            else:
                out[k] = "__DROPPED_RUNTIME_DETAIL__"
        return out

    def event(self, level: str, message: str, payload: Dict[str, Any]) -> None:
        try:
            self.db.runtime_event("runtime_v11", level, message, self.slim_event_payload(payload))
        except Exception:
            pass

    def count_table(self, table: str) -> int:
        try:
            rows = self.db.query(f"SELECT COUNT(*) AS n FROM {table};")
            return int(dict(rows[0]).get("n") or 0) if rows else 0
        except Exception:
            return 0

    def legacy_counts(self) -> Dict[str, int]:
        return {
            "decisions": self.count_table("decisions"),
            "positions": self.count_table("positions"),
            "trades": self.count_table("trades"),
        }

    def assert_legacy_unchanged(self, before: Dict[str, int], after: Dict[str, int]) -> None:
        changed = {k: (before[k], after[k]) for k in before if before[k] != after[k]}
        if changed:
            raise RuntimeError(f"LEGACY_TRADING_TABLES_CHANGED_BY_V11:{changed}")

    def prices(self) -> Dict[str, Any]:
        p = getattr(self.adapter, "prices", {})
        return p if isinstance(p, dict) else {}

    def termux_light_retention(self) -> None:
        # Critical for Android/Termux: prevent SQLite payload tables from exploding.
        # Does not touch trades/positions/decisions or canary history.
        if self.cycles % 3 != 0:
            return
        try:
            self.db.execute("""
                DELETE FROM market_snapshots
                WHERE rowid NOT IN (
                    SELECT rowid FROM market_snapshots ORDER BY rowid DESC LIMIT 300
                );
            """)
            self.db.execute("""
                DELETE FROM institutional_decision_order_v11
                WHERE rowid NOT IN (
                    SELECT rowid FROM institutional_decision_order_v11 ORDER BY rowid DESC LIMIT 80
                );
            """)
            self.db.execute("""
                DELETE FROM institutional_control_plane_v11
                WHERE rowid NOT IN (
                    SELECT rowid FROM institutional_control_plane_v11 ORDER BY rowid DESC LIMIT 80
                );
            """)
            self.db.execute("""
                DELETE FROM runtime_events
                WHERE rowid NOT IN (
                    SELECT rowid FROM runtime_events ORDER BY rowid DESC LIMIT 1500
                );
            """)
            self.db.execute("PRAGMA wal_checkpoint(PASSIVE);")
        except Exception as e:
            self.event("WARN", "termux_light_retention_failed", {"error": repr(e)})

    def cycle_once(self) -> Dict[str, Any]:
        self.cycles += 1
        before = self.legacy_counts()

        # 10. Input adapter. No decisions/positions/trades are allowed here.
        self.adapter.step_market()
        self.adapter.step_context()
        self.adapter.step_alpha_shadow()

        # 20-70. Ordered evidence chain.
        cluster_result = self.cluster.refresh()
        edge_result = self.edge_factory.refresh()
        validation_result = self.validator.refresh()
        shadow_regime_result = self.shadow_regime.refresh()
        derivatives_spine_result = self.derivatives_spine.refresh()
        derivatives_regime_result = self.derivatives_regime.refresh()

        # 80-100. Outcome memory and explicit anti-overlap guard before final control.
        feedback_before = self.feedback.refresh()
        kpi_before = self.kpi.refresh()
        overlap_result = self.overlap.refresh()
        order_pre = self.order.refresh("PRE_CONTROL")

        # 110. Single final authority.
        control_result = self.control.refresh()

        # 120. Execution bridge only reads the final V11 contract.
        canary_result = self.canary.refresh(self.prices(), allow_open=self.allow_canary)

        # 130. Post-execution feedback/readiness.
        feedback_after = self.feedback.refresh()
        kpi_after = self.kpi.refresh()
        ablation_result = self.ablation.refresh()
        readiness_result = self.readiness.refresh()
        order_post = self.order.refresh("POST_EXECUTION")

        # 140. Adapter maintenance only.
        self.adapter.step_forward()
        self.adapter.step_retention()
        self.adapter.write_state()
        self.termux_light_retention()

        after = self.legacy_counts()
        self.assert_legacy_unchanged(before, after)

        payload = {
            "cycle": self.cycles,
            "mode": VERSION,
            "allow_canary": self.allow_canary,
            "ordered_pipeline": [
                "market_context_shadow", "cluster", "edge_factory", "robustness", "shadow_regime",
                "derivatives_spine", "derivatives_regime_v10_2", "feedback_kpi", "overlap_guard",
                "decision_order", "control_plane", "paper_canary_bridge", "post_feedback", "ablation", "readiness"
            ],
            "cluster": cluster_result,
            "edge_factory": edge_result,
            "validation": validation_result,
            "shadow_regime": shadow_regime_result,
            "derivatives_spine": derivatives_spine_result,
            "derivatives_regime": derivatives_regime_result,
            "feedback_before": feedback_before,
            "kpi_before": kpi_before,
            "overlap": overlap_result,
            "order_pre": order_pre,
            "control": control_result,
            "canary": canary_result,
            "feedback_after": feedback_after,
            "kpi_after": kpi_after,
            "ablation": ablation_result,
            "paid_api_readiness": readiness_result,
            "order_post": order_post,
            "legacy_counts": after,
        }
        self.event("INFO", "runtime_v11_cycle", payload)
        return payload

    def run(self) -> None:
        self.event("INFO", "runtime_v11_started", {
            "started": self.started,
            "allow_canary": self.allow_canary,
            "legacy_runner_demoted_to_adapter": True,
            "single_final_authority": True,
            "paid_api_required": False,
        })
        while True:
            try:
                self.cycle_once()
            except Exception as e:
                self.event("ERROR", "runtime_v11_cycle_failed", {
                    "cycle": self.cycles,
                    "error": repr(e),
                    "trace": traceback.format_exc(limit=10),
                })
            time.sleep(max(float(getattr(CFG, "loop_sleep_sec", 30)), 60.0))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--audit-once", action="store_true")
    args = parser.parse_args()
    rt = InstitutionalRuntimeV11(allow_canary=not args.audit_once)
    if args.once or args.audit_once:
        print(json.dumps(rt.cycle_once(), indent=2, sort_keys=True, default=str))
    else:
        rt.run()


if __name__ == "__main__":
    main()
