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
from joanbot.execution.micro_canary_outcome_feedback_v10 import MicroCanaryOutcomeFeedbackV10
from joanbot.analytics.micro_canary_kpi_v10 import MicroCanaryKPIEngineV10
from joanbot.control.control_plane_v10 import InstitutionalControlPlaneV10
from joanbot.execution.paper_micro_canary_bridge_v10 import PaperMicroCanaryBridgeV10
from joanbot.control.api_readiness_gate_v10 import PaidApiReadinessGateV10
from joanbot.utils import utc_now_iso

VERSION = "INSTITUTIONAL_RUNTIME_V10_PRE_API"


class InstitutionalRuntimeV10:
    """
    Pre-paid-API institutional runtime.

    The old Runner is demoted to a market/context/shadow adapter only.

    Pipeline:
    market/context/shadow -> cluster -> exact edge -> robustness -> shadow regime
    -> free Binance derivatives spine -> derivatives regime -> V10 feedback/KPI
    -> V10 control -> V10 paper micro-canary -> paid API readiness gate.
    """

    def __init__(self, allow_canary: bool = True) -> None:
        self.adapter = Runner()
        self.db = self.adapter.db
        self.cycles = 0
        self.started = utc_now_iso()
        self.allow_canary = allow_canary

        self.cluster = AlphaClusterAggregatorV6(self.db)
        self.regime = RegimeAdaptiveRouterV6(self.db)
        self.edge = InstitutionalEdgeFactoryV8(self.db)
        self.validator = EdgeRobustnessValidatorV9(self.db)
        self.derivatives_spine = DerivativesDataSpineV10(self.db)
        self.derivatives_regime = DerivativesRegimeV10(self.db)
        self.feedback = MicroCanaryOutcomeFeedbackV10(self.db)
        self.kpi = MicroCanaryKPIEngineV10(self.db)
        self.control = InstitutionalControlPlaneV10(self.db)
        self.canary = PaperMicroCanaryBridgeV10(self.db)
        self.readiness = PaidApiReadinessGateV10(self.db)

        self.disable_legacy_path()

    def disable_legacy_path(self) -> None:
        def forbidden(*args, **kwargs):
            raise RuntimeError("LEGACY_TRADING_PATH_FORBIDDEN_BY_RUNTIME_V10")

        self.adapter.step_decisions = forbidden
        self.adapter.step_positions = forbidden
        if hasattr(self.adapter, "broker") and hasattr(self.adapter.broker, "open_from_decision"):
            self.adapter.broker.open_from_decision = forbidden

    def event(self, level: str, message: str, payload: Dict[str, Any]) -> None:
        try:
            self.db.runtime_event("runtime_v10", level, message, {"version": VERSION, **payload})
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
            raise RuntimeError(f"LEGACY_TRADING_TABLES_CHANGED:{changed}")

    def prices(self) -> Dict[str, Any]:
        p = getattr(self.adapter, "prices", {})
        return p if isinstance(p, dict) else {}

    def cycle_once(self) -> Dict[str, Any]:
        self.cycles += 1
        before = self.legacy_counts()

        self.adapter.step_market()
        self.adapter.step_context()
        self.adapter.step_alpha_shadow()

        cluster_result = self.cluster.refresh()
        edge_result = self.edge.refresh()
        validation_result = self.validator.refresh()
        shadow_regime_result = self.regime.refresh()
        derivatives_spine_result = self.derivatives_spine.refresh()
        derivatives_regime_result = self.derivatives_regime.refresh()
        feedback_result = self.feedback.refresh()
        kpi_before = self.kpi.refresh()
        control_result = self.control.refresh()
        canary_result = self.canary.refresh(self.prices(), allow_open=self.allow_canary)
        kpi_after = self.kpi.refresh()
        readiness_result = self.readiness.refresh()

        self.adapter.step_forward()
        self.adapter.step_retention()
        self.adapter.write_state()

        after = self.legacy_counts()
        self.assert_legacy_unchanged(before, after)

        payload = {
            "cycle": self.cycles,
            "mode": VERSION,
            "allow_canary": self.allow_canary,
            "cluster": cluster_result,
            "edge": edge_result,
            "validation": validation_result,
            "shadow_regime": shadow_regime_result,
            "derivatives_spine": derivatives_spine_result,
            "derivatives_regime": derivatives_regime_result,
            "feedback": feedback_result,
            "kpi_before": kpi_before,
            "control": control_result,
            "canary": canary_result,
            "kpi_after": kpi_after,
            "paid_api_readiness": readiness_result,
            "legacy_counts": after,
        }
        self.event("INFO", "runtime_v10_cycle", payload)
        return payload

    def run(self) -> None:
        self.event("INFO", "runtime_v10_started", {
            "started": self.started,
            "allow_canary": self.allow_canary,
            "legacy_runner_demoted_to_adapter": True,
            "paid_api_required": False,
        })
        while True:
            try:
                self.cycle_once()
            except Exception as e:
                self.event("ERROR", "runtime_v10_cycle_failed", {
                    "cycle": self.cycles,
                    "error": repr(e),
                    "trace": traceback.format_exc(limit=8),
                })
            time.sleep(float(getattr(CFG, "loop_sleep_sec", 30)))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--audit-once", action="store_true")
    args = parser.parse_args()
    rt = InstitutionalRuntimeV10(allow_canary=not args.audit_once)
    if args.once or args.audit_once:
        print(json.dumps(rt.cycle_once(), indent=2, sort_keys=True, default=str))
    else:
        rt.run()


if __name__ == "__main__":
    main()
