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
from joanbot.control.control_plane_v7 import InstitutionalControlPlaneV7
from joanbot.utils import utc_now_iso

VERSION = "INSTITUTIONAL_ALPHA_RUNTIME_V7"


def js(x: Any) -> str:
    try:
        return json.dumps(x, separators=(",", ":"), ensure_ascii=False, default=str)
    except Exception:
        return "{}"


class InstitutionalAlphaRuntimeV7:
    """
    Institutional alpha runtime.

    The old Runner is demoted to a data/alpha adapter.
    Trading methods are explicitly blocked.

    Allowed:
    - market refresh
    - context refresh
    - alpha shadow
    - alpha contracts
    - cluster aggregation
    - regime routing
    - control plane
    - forward learning maintenance

    Forbidden:
    - legacy decisions
    - legacy position management
    - broker open
    - order placement
    """

    def __init__(self) -> None:
        self.adapter = Runner()
        self.db = self.adapter.db
        self.cycles = 0
        self.started = utc_now_iso()

        self.cluster = AlphaClusterAggregatorV6(self.db)
        self.regime = RegimeAdaptiveRouterV6(self.db)
        self.control = InstitutionalControlPlaneV7(self.db)

        self._disable_legacy_trading_path()

    def _disable_legacy_trading_path(self) -> None:
        def forbidden(*args, **kwargs):
            raise RuntimeError("LEGACY_TRADING_PATH_FORBIDDEN_BY_ALPHA_RUNTIME_V7")

        self.adapter.step_decisions = forbidden
        self.adapter.step_positions = forbidden

        if hasattr(self.adapter, "broker") and hasattr(self.adapter.broker, "open_from_decision"):
            self.adapter.broker.open_from_decision = forbidden

    def event(self, level: str, message: str, payload: Dict[str, Any]) -> None:
        try:
            self.db.runtime_event(
                "alpha_runtime_v7",
                level,
                message,
                {
                    "version": VERSION,
                    **payload,
                },
            )
        except Exception:
            pass

    def count_table(self, table: str) -> int:
        try:
            rows = self.db.query(f"SELECT COUNT(*) AS n FROM {table};")
            return int(dict(rows[0]).get("n") or 0) if rows else 0
        except Exception:
            return 0

    def protected_counts(self) -> Dict[str, int]:
        return {
            "decisions": self.count_table("decisions"),
            "positions": self.count_table("positions"),
            "trades": self.count_table("trades"),
        }

    def assert_no_trading_mutation(self, before: Dict[str, int], after: Dict[str, int]) -> None:
        changed = {k: (before[k], after[k]) for k in before if before[k] != after[k]}
        if changed:
            raise RuntimeError(f"PROTECTED_TRADING_TABLES_CHANGED:{changed}")

    def cycle_once(self) -> Dict[str, Any]:
        self.cycles += 1
        before = self.protected_counts()

        self.adapter.step_market()
        self.adapter.step_context()

        # This refreshes universal alpha shadow + tensor/posterior/meta/contracts/control v6 if present.
        self.adapter.step_alpha_shadow()

        cluster_result = self.cluster.refresh()
        regime_result = self.regime.refresh()
        control_result = self.control.refresh()

        self.adapter.step_forward()
        self.adapter.step_retention()
        self.adapter.write_state()

        after = self.protected_counts()
        self.assert_no_trading_mutation(before, after)

        payload = {
            "cycle": self.cycles,
            "started": self.started,
            "mode": "ALPHA_RUNTIME_CONTROL_ONLY",
            "symbols": list(getattr(CFG, "symbols", [])),
            "cluster": cluster_result,
            "regime": regime_result,
            "control": control_result,
            "protected_counts": after,
            "forbidden_legacy_path": True,
        }

        self.event("INFO", "alpha_runtime_v7_cycle", payload)
        return payload

    def run(self) -> None:
        self.event(
            "INFO",
            "alpha_runtime_v7_started",
            {
                "started": self.started,
                "mode": "ALPHA_RUNTIME_CONTROL_ONLY",
                "legacy_runner_demoted_to_adapter": True,
                "new_positions_allowed": False,
            },
        )

        while True:
            try:
                self.cycle_once()
            except Exception as e:
                self.event(
                    "ERROR",
                    "alpha_runtime_v7_cycle_failed",
                    {
                        "cycle": self.cycles,
                        "error": repr(e),
                        "trace": traceback.format_exc(limit=8),
                    },
                )
            time.sleep(float(getattr(CFG, "loop_sleep_sec", 30)))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    rt = InstitutionalAlphaRuntimeV7()

    if args.once:
        print(json.dumps(rt.cycle_once(), indent=2, sort_keys=True, default=str))
    else:
        rt.run()


if __name__ == "__main__":
    main()
