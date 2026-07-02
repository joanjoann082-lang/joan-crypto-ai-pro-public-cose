from __future__ import annotations

import argparse
import hashlib
import json
from typing import Any, Dict, List, Tuple

from joanbot.storage.db import get_db
from joanbot.utils import utc_now_iso

VERSION = "INSTITUTIONAL_DECISION_ORDER_V11_3_SLIM_PERSISTENCE"

DROP_KEYS = {
    "payload",
    "ordered_contract_json",
    "decision_contract_json",
    "contract_json",
    "raw_payload",
    "raw",
    "data_json",
}


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


def digest(obj: Any) -> str:
    return hashlib.sha256(js(obj).encode("utf-8")).hexdigest()[:24]


def slim_value(x: Any, depth: int = 0) -> Any:
    if depth > 3:
        return "__DEPTH_LIMIT__"
    if isinstance(x, dict):
        out = {}
        for k, v in x.items():
            if str(k) in DROP_KEYS:
                out[str(k)] = "__DROPPED_HEAVY_FIELD__"
            else:
                out[str(k)] = slim_value(v, depth + 1)
        return out
    if isinstance(x, list):
        return [slim_value(v, depth + 1) for v in x[:20]]
    if isinstance(x, str):
        return x if len(x) <= 800 else x[:800] + "__TRUNCATED__"
    return x


def slim_row(row: Dict[str, Any]) -> Dict[str, Any]:
    return slim_value(row) if isinstance(row, dict) else {}


class InstitutionalDecisionOrderV11:
    ORDER: Tuple[Tuple[int, str], ...] = (
        (0, "SYSTEM_SAFETY"),
        (10, "MARKET_ADAPTER"),
        (20, "ALPHA_SHADOW"),
        (30, "EDGE_FACTORY"),
        (40, "ROBUSTNESS_VALIDATOR"),
        (50, "SHADOW_REGIME"),
        (60, "DERIVATIVES_DATA"),
        (70, "DERIVATIVES_REGIME"),
        (80, "FEEDBACK_KPI"),
        (90, "OVERLAP_GUARD"),
        (100, "CONTROL_PLANE"),
        (110, "EXECUTION_BRIDGE"),
        (120, "POST_TRADE_FEEDBACK"),
        (130, "PAID_API_READINESS"),
    )

    def __init__(self, db=None) -> None:
        self.db = db or get_db()

    def ensure_schema(self) -> None:
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS institutional_decision_order_v11 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                version TEXT NOT NULL,
                phase TEXT NOT NULL,
                flow_hash TEXT NOT NULL,
                selected_symbol TEXT,
                selected_side TEXT,
                selected_setup TEXT,
                selected_profile TEXT,
                selected_horizon_min INTEGER NOT NULL,
                source_edge_id INTEGER NOT NULL,
                ordered_stage_count INTEGER NOT NULL,
                missing_stage_count INTEGER NOT NULL,
                hard_vetoes TEXT NOT NULL,
                ordered_contract_json TEXT NOT NULL,
                payload TEXT NOT NULL
            );
        """)
        self.db.execute("DROP VIEW IF EXISTS latest_institutional_decision_order_v11;")
        self.db.execute("""
            CREATE VIEW latest_institutional_decision_order_v11 AS
            SELECT * FROM institutional_decision_order_v11
            ORDER BY id DESC LIMIT 1;
        """)

    def q1(self, sql: str, params=()) -> Dict[str, Any]:
        try:
            rows = self.db.query(sql, params)
            return slim_row(dict(rows[0])) if rows else {}
        except Exception:
            return {}

    def stage_payloads(self) -> Dict[str, Any]:
        edge = self.q1("""
            SELECT * FROM latest_edge_robustness_validator_v9
            ORDER BY canary_permission DESC, robustness_score DESC, lcb_r DESC, avg_r DESC, n DESC LIMIT 1;
        """)
        symbol = str(edge.get("symbol") or "")

        market = self.q1(
            "SELECT * FROM market_snapshots WHERE symbol=? ORDER BY id DESC LIMIT 1;",
            (symbol,)
        ) if symbol else {}

        control = self.q1("""
            SELECT
              id, ts, version, flow_hash, global_state, decision_tier,
              control_score, confidence_score, allow_paper_micro_canary,
              max_size_usd, edge_symbol, edge_side, derivatives_state,
              shadow_regime_state, feedback_state, kpi_state, overlap_state,
              hard_vetoes, reasons
            FROM latest_institutional_control_plane_v11;
        """)

        return {
            "SYSTEM_SAFETY": {
                "paper_only": True,
                "legacy_direct_open_forbidden": True,
                "standard_open_forbidden": True,
            },
            "MARKET_ADAPTER": self.summarize("MARKET_ADAPTER", market),
            "ALPHA_SHADOW": self.q1("SELECT COUNT(*) AS n FROM universal_shadow_cases_v2;"),
            "EDGE_FACTORY": self.q1("SELECT * FROM latest_institutional_edge_factory_v8 WHERE symbol=? LIMIT 1;", (symbol,)) if symbol else {},
            "ROBUSTNESS_VALIDATOR": edge,
            "SHADOW_REGIME": self.q1("SELECT * FROM latest_regime_adaptive_router_v6;"),
            "DERIVATIVES_DATA": self.q1("SELECT * FROM latest_derivatives_data_spine_v10 WHERE symbol=? LIMIT 1;", (symbol,)) if symbol else {},
            "DERIVATIVES_REGIME": self.q1("SELECT * FROM latest_derivatives_regime_v10;"),
            "FEEDBACK_KPI": {
                "feedback": self.q1("SELECT * FROM latest_micro_canary_outcome_feedback_v11;"),
                "kpi": self.q1("SELECT * FROM latest_micro_canary_kpi_v11;"),
            },
            "OVERLAP_GUARD": self.q1("SELECT * FROM latest_overlap_guard_v11;"),
            "CONTROL_PLANE": control,
            "EXECUTION_BRIDGE": self.q1("SELECT * FROM paper_micro_canary_positions_v11 ORDER BY id DESC LIMIT 1;"),
            "POST_TRADE_FEEDBACK": {
                "feedback": self.q1("SELECT * FROM latest_micro_canary_outcome_feedback_v11;"),
                "kpi": self.q1("SELECT * FROM latest_micro_canary_kpi_v11;"),
            },
            "PAID_API_READINESS": self.q1("""
                SELECT id, ts, version, readiness_state, paid_api_allowed,
                       closed_canaries, profit_factor, expectancy_r,
                       max_drawdown_r, ablation_state, hard_vetoes
                FROM latest_paid_api_readiness_gate_v11;
            """),
        }

    def refresh(self, phase: str = "PRE_CONTROL") -> Dict[str, Any]:
        self.ensure_schema()
        payloads = self.stage_payloads()

        ordered: List[Dict[str, Any]] = []
        missing: List[str] = []

        for idx, name in self.ORDER:
            data = payloads.get(name) or {}
            present = bool(data)
            if not present and name not in {"CONTROL_PLANE", "EXECUTION_BRIDGE", "PAID_API_READINESS"}:
                missing.append(name)
            ordered.append({
                "stage": idx,
                "name": name,
                "present": present,
                "hash": digest(data),
                "summary": self.summarize(name, data),
            })

        edge = payloads.get("ROBUSTNESS_VALIDATOR") or {}
        hard_vetoes: List[str] = []

        if missing:
            hard_vetoes.append("MISSING_UPSTREAM_STAGES:" + ",".join(missing))
        if not edge:
            hard_vetoes.append("NO_SELECTED_EDGE_IN_ORDER_CONTRACT")

        contract = {
            "version": VERSION,
            "phase": phase,
            "canonical_order": [{"stage": s, "name": n} for s, n in self.ORDER],
            "ordered": ordered,
            "hard_vetoes": hard_vetoes,
            "paper_only": True,
            "paid_api_required": False,
            "slim_persistence": True,
        }

        flow_hash = digest(contract)
        slim_payload = {
            name: self.summarize(name, data)
            for name, data in payloads.items()
        }

        self.db.execute("""
            INSERT INTO institutional_decision_order_v11 (
                ts, version, phase, flow_hash, selected_symbol, selected_side, selected_setup,
                selected_profile, selected_horizon_min, source_edge_id,
                ordered_stage_count, missing_stage_count, hard_vetoes, ordered_contract_json, payload
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """, (
            utc_now_iso(), VERSION, phase, flow_hash,
            edge.get("symbol"), edge.get("side"), edge.get("setup"), edge.get("profile"),
            inum(edge.get("horizon_min")), inum(edge.get("source_edge_id")),
            len(ordered), len(missing), js(hard_vetoes), js(contract), js(slim_payload),
        ))

        return {
            "version": VERSION,
            "phase": phase,
            "flow_hash": flow_hash,
            "selected_symbol": edge.get("symbol"),
            "selected_side": edge.get("side"),
            "source_edge_id": inum(edge.get("source_edge_id")),
            "ordered_stage_count": len(ordered),
            "missing_stage_count": len(missing),
            "hard_vetoes": hard_vetoes,
        }

    def summarize(self, name: str, data: Dict[str, Any]) -> Dict[str, Any]:
        if not data:
            return {}

        if name == "MARKET_ADAPTER":
            return {
                "id": data.get("id"),
                "ts": data.get("ts"),
                "symbol": data.get("symbol"),
                "price": data.get("price") or data.get("last_price") or data.get("close"),
                "source": data.get("source"),
            }

        if name == "ROBUSTNESS_VALIDATOR":
            return {
                "state": data.get("validation_state"),
                "canary_permission": data.get("canary_permission"),
                "symbol": data.get("symbol"),
                "side": data.get("side"),
                "setup": data.get("setup"),
                "profile": data.get("profile"),
                "horizon_min": data.get("horizon_min"),
                "n": data.get("n"),
                "avg_r": data.get("avg_r"),
                "lcb_r": data.get("lcb_r"),
                "recent20_avg_r": data.get("recent20_avg_r"),
                "recent50_lcb_r": data.get("recent50_lcb_r"),
                "winrate": data.get("winrate"),
                "robustness_score": data.get("robustness_score"),
                "hard_vetoes": data.get("hard_vetoes"),
            }

        if name == "DERIVATIVES_REGIME":
            return {
                "state": data.get("derivatives_state"),
                "symbol": data.get("symbol"),
                "selected_side": data.get("selected_side"),
                "selected_score": data.get("selected_score"),
                "opposite_score": data.get("opposite_score"),
                "confidence_score": data.get("confidence_score"),
                "contradiction_index": data.get("contradiction_index"),
                "allow_v11_canary": data.get("allow_v11_canary"),
                "reduce_size": data.get("reduce_size"),
                "veto_canary": data.get("veto_canary"),
            }

        if name == "SHADOW_REGIME":
            return {
                "state": data.get("regime_state"),
                "score": data.get("regime_score"),
                "hard_vetoes": data.get("hard_vetoes"),
            }

        if name == "CONTROL_PLANE":
            return {
                "state": data.get("global_state"),
                "tier": data.get("decision_tier"),
                "allow_micro": data.get("allow_paper_micro_canary"),
                "max_size": data.get("max_size_usd"),
                "control_score": data.get("control_score"),
                "hard_vetoes": data.get("hard_vetoes"),
            }

        if name in {"FEEDBACK_KPI", "POST_TRADE_FEEDBACK"}:
            return slim_row(data)

        return slim_row({k: data.get(k) for k in list(data.keys())[:16]})


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--phase", default="MANUAL")
    args = parser.parse_args()

    engine = InstitutionalDecisionOrderV11()
    if args.refresh:
        print(json.dumps(engine.refresh(args.phase), indent=2, sort_keys=True, default=str))
    else:
        engine.ensure_schema()
        print(json.dumps({"version": VERSION, "schema": "ok"}, indent=2))


if __name__ == "__main__":
    main()
