from __future__ import annotations

import argparse
import json
from typing import Any, Dict, List

from joanbot.storage.db import get_db
from joanbot.utils import utc_now_iso

VERSION = "INSTITUTIONAL_CONTROL_PLANE_V6"
MAX_ROWS = 1200
MAX_AUDIT_ROWS = 300


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


class InstitutionalControlPlaneV6:
    """
    Canonical institutional control plane.

    Reads:
    - latest_alpha_promotion_contract_v5
    - universal_shadow_results_v2 / universal_shadow_cases_v2
    - trades
    - positions
    - runtime_events

    Writes:
    - institutional_control_plane_v6
    - latest_institutional_control_plane_v6
    - institutional_control_plane_audit_v6

    Does not mutate:
    - decisions
    - positions
    - trades
    - forward_cases
    - forward_results
    - execution/broker tables

    This is a governance/control contract, not an execution bridge.
    """

    def __init__(self, db=None):
        self.db = db or get_db()

    def ensure_schema(self) -> None:
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS institutional_control_plane_v6 (
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
                max_daily_new_positions INTEGER NOT NULL,
                allowed_symbols TEXT NOT NULL,
                allowed_sides TEXT NOT NULL,

                required_execution_mode TEXT NOT NULL,
                recommended_action TEXT NOT NULL,
                next_required_build TEXT NOT NULL,

                contracts_n INTEGER NOT NULL,
                contract_state TEXT NOT NULL,
                micro_canary_ready INTEGER NOT NULL,

                max_meta_score REAL NOT NULL,
                max_posterior_score REAL NOT NULL,
                max_posterior_mean_r REAL NOT NULL,
                max_posterior_lcb_r REAL NOT NULL,
                max_prob_edge_gt_zero REAL NOT NULL,
                max_prob_edge_gt_min REAL NOT NULL,
                max_prob_loss REAL NOT NULL,
                max_prob_tail REAL NOT NULL,
                max_tensor_quality REAL NOT NULL,

                shadow_100_avg_r REAL NOT NULL,
                shadow_100_winrate REAL NOT NULL,
                shadow_300_avg_r REAL NOT NULL,
                shadow_300_winrate REAL NOT NULL,
                shadow_600_avg_r REAL NOT NULL,
                shadow_600_winrate REAL NOT NULL,

                best_family_symbol TEXT,
                best_family_side TEXT,
                best_family_name TEXT,
                best_family_horizon_min INTEGER NOT NULL,
                best_family_n INTEGER NOT NULL,
                best_family_avg_r REAL NOT NULL,
                best_family_winrate REAL NOT NULL,
                best_family_worst_r REAL NOT NULL,
                best_family_best_r REAL NOT NULL,

                last_trade_ts TEXT,
                last10_pnl_usd REAL NOT NULL,
                last25_pnl_usd REAL NOT NULL,
                last10_winrate_usd REAL NOT NULL,
                last25_winrate_usd REAL NOT NULL,
                open_positions INTEGER NOT NULL,

                alpha_runtime_errors INTEGER NOT NULL,
                hard_vetoes TEXT NOT NULL,
                reasons TEXT NOT NULL,
                control_contract_json TEXT NOT NULL,
                payload TEXT NOT NULL
            );
        """)

        self.db.execute("""
            CREATE INDEX IF NOT EXISTS idx_institutional_control_plane_v6_id
            ON institutional_control_plane_v6(id);
        """)

        self.db.execute("""
            CREATE TABLE IF NOT EXISTS institutional_control_plane_audit_v6 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                version TEXT NOT NULL,
                event TEXT NOT NULL,
                level TEXT NOT NULL,
                message TEXT NOT NULL,
                payload TEXT NOT NULL
            );
        """)

        self.db.execute("DROP VIEW IF EXISTS latest_institutional_control_plane_v6;")
        self.db.execute("""
            CREATE VIEW latest_institutional_control_plane_v6 AS
            SELECT *
            FROM institutional_control_plane_v6
            ORDER BY id DESC
            LIMIT 1;
        """)

    def q1(self, sql: str) -> Dict[str, Any]:
        try:
            rows = self.db.query(sql)
            return dict(rows[0]) if rows else {}
        except Exception as e:
            return {"_error": repr(e)}

    def qmany(self, sql: str) -> List[Dict[str, Any]]:
        try:
            return [dict(r) for r in self.db.query(sql)]
        except Exception:
            return []

    def audit(self, event: str, level: str, message: str, payload: Dict[str, Any]) -> None:
        self.db.execute("""
            INSERT INTO institutional_control_plane_audit_v6
            (ts, version, event, level, message, payload)
            VALUES (?, ?, ?, ?, ?, ?);
        """, (
            utc_now_iso(),
            VERSION,
            event,
            level,
            message[:500],
            js(payload),
        ))

    def contract_summary(self) -> Dict[str, Any]:
        return self.q1("""
            SELECT
              COALESCE(MAX(contract_state), 'NO_CONTRACTS') AS contract_state,
              COUNT(*) AS contracts_n,
              COALESCE(SUM(allowed_paper_micro_canary), 0) AS micro_canary_ready,
              COALESCE(MAX(meta_score), 0) AS max_meta_score,
              COALESCE(MAX(posterior_score), 0) AS max_posterior_score,
              COALESCE(MAX(posterior_mean_r), 0) AS max_posterior_mean_r,
              COALESCE(MAX(posterior_lcb_r), 0) AS max_posterior_lcb_r,
              COALESCE(MAX(prob_edge_gt_zero), 0) AS max_prob_edge_gt_zero,
              COALESCE(MAX(prob_edge_gt_min), 0) AS max_prob_edge_gt_min,
              COALESCE(MAX(prob_loss_gt_025r), 0) AS max_prob_loss,
              COALESCE(MAX(prob_tail_event), 0) AS max_prob_tail,
              COALESCE(MAX(tensor_quality), 0) AS max_tensor_quality
            FROM latest_alpha_promotion_contract_v5;
        """)

    def shadow_window(self, n: int) -> Dict[str, Any]:
        return self.q1(f"""
            SELECT
              COUNT(*) AS n,
              COALESCE(AVG(result_r), 0) AS avg_r,
              COALESCE(SUM(CASE WHEN result_r > 0 THEN 1 ELSE 0 END)*100.0/COUNT(*), 0) AS winrate,
              COALESCE(MIN(result_r), 0) AS worst_r,
              COALESCE(MAX(result_r), 0) AS best_r
            FROM (
              SELECT result_r
              FROM universal_shadow_results_v2
              WHERE result_r IS NOT NULL
              ORDER BY id DESC
              LIMIT {int(n)}
            );
        """)

    def best_family(self) -> Dict[str, Any]:
        rows = self.qmany("""
            SELECT
              symbol,
              side,
              family,
              horizon_min,
              COUNT(*) AS n,
              AVG(result_r) AS avg_r,
              SUM(CASE WHEN result_r > 0 THEN 1 ELSE 0 END)*100.0/COUNT(*) AS winrate,
              MIN(result_r) AS worst_r,
              MAX(result_r) AS best_r
            FROM (
              SELECT
                c.symbol,
                c.side,
                CASE
                  WHEN c.setup LIKE '%REBOUND%' OR c.setup LIKE '%SQUEEZE%' OR c.setup LIKE '%PULLBACK%' THEN 'REBOUND_PULLBACK_FAMILY'
                  WHEN c.setup LIKE '%BOUNCE%' OR c.setup LIKE '%FADE%' OR c.setup LIKE '%CONTINUATION%' THEN 'BOUNCE_FADE_TREND_FAMILY'
                  ELSE 'OTHER'
                END AS family,
                c.horizon_min,
                r.result_r
              FROM universal_shadow_results_v2 r
              JOIN universal_shadow_cases_v2 c ON c.id = r.case_id
              WHERE r.result_r IS NOT NULL
              ORDER BY r.id DESC
              LIMIT 600
            )
            GROUP BY symbol, side, family, horizon_min
            HAVING n >= 10
            ORDER BY avg_r DESC, winrate DESC, n DESC
            LIMIT 1;
        """)
        return rows[0] if rows else {}

    def trades_summary(self, n: int) -> Dict[str, Any]:
        return self.q1(f"""
            SELECT
              COUNT(*) AS n,
              COALESCE(SUM(pnl_usd), 0) AS pnl_usd,
              COALESCE(SUM(CASE WHEN pnl_usd > 0 THEN 1 ELSE 0 END)*100.0/COUNT(*), 0) AS winrate_usd,
              MAX(ts) AS last_trade_ts
            FROM (
              SELECT *
              FROM trades
              WHERE pnl_usd IS NOT NULL
              ORDER BY id DESC
              LIMIT {int(n)}
            );
        """)

    def open_positions(self) -> int:
        r = self.q1("SELECT COUNT(*) AS n FROM positions WHERE status='OPEN';")
        return inum(r.get("n"), 0)

    def alpha_runtime_errors(self) -> int:
        r = self.q1("""
            SELECT COUNT(*) AS n
            FROM runtime_events
            WHERE component IN ('alpha_shadow','alpha_operating_layer','control_plane')
              AND level='ERROR'
              AND ts >= datetime('now','-12 hours');
        """)
        return inum(r.get("n"), 0)

    def decide(
        self,
        c: Dict[str, Any],
        s100: Dict[str, Any],
        s300: Dict[str, Any],
        s600: Dict[str, Any],
        fam: Dict[str, Any],
        t10: Dict[str, Any],
        t25: Dict[str, Any],
        open_pos: int,
        alpha_errors: int,
    ) -> Dict[str, Any]:
        hard_vetoes: List[str] = []
        reasons: List[str] = []

        contracts_n = inum(c.get("contracts_n"))
        micro_ready = inum(c.get("micro_canary_ready"))
        max_meta = fnum(c.get("max_meta_score"))
        max_post = fnum(c.get("max_posterior_score"))
        max_mean = fnum(c.get("max_posterior_mean_r"))
        max_lcb = fnum(c.get("max_posterior_lcb_r"))
        max_p0 = fnum(c.get("max_prob_edge_gt_zero"))
        max_pmin = fnum(c.get("max_prob_edge_gt_min"))
        max_loss = fnum(c.get("max_prob_loss"))
        max_tail = fnum(c.get("max_prob_tail"))
        max_tensor = fnum(c.get("max_tensor_quality"))

        s100_avg = fnum(s100.get("avg_r"))
        s300_avg = fnum(s300.get("avg_r"))
        s600_avg = fnum(s600.get("avg_r"))

        fam_symbol = fam.get("symbol")
        fam_side = fam.get("side")
        fam_n = inum(fam.get("n"))
        fam_avg = fnum(fam.get("avg_r"))
        fam_wr = fnum(fam.get("winrate"))

        allow_standard_open = 0
        allow_direct_open = 0
        allow_micro = 0
        force_learning = 1
        veto_new_positions = 1
        max_size_usd = 0.0
        max_daily_new_positions = 0
        allowed_symbols: List[str] = []
        allowed_sides: List[str] = []
        required_mode = "NONE"

        if contracts_n <= 0:
            state = "SYSTEM_DEGRADED"
            action = "DO_NOT_OPEN_FIX_ALPHA_CONTRACT_INPUT"
            next_build = "CONTROL_PLANE_INPUT_REPAIR"
            hard_vetoes.append("NO_ALPHA_CONTRACT_INPUT")

        elif alpha_errors > 0:
            state = "SYSTEM_DEGRADED"
            action = "DO_NOT_OPEN_FIX_ALPHA_RUNTIME_ERRORS"
            next_build = "ALPHA_RUNTIME_ERROR_AUDIT"
            hard_vetoes.append("ALPHA_RUNTIME_ERRORS_LAST_12H")

        elif open_pos > 0:
            state = "MANAGE_ONLY"
            action = "MANAGE_EXISTING_POSITIONS_DO_NOT_OPEN_NEW"
            next_build = "POSITION_OUTCOME_FEEDBACK_V5"
            hard_vetoes.append("OPEN_POSITIONS_EXIST")

        elif (
            micro_ready > 0
            and max_meta >= 78
            and max_post >= 75
            and max_mean >= 0.035
            and max_lcb > 0
            and max_p0 >= 0.75
            and max_pmin >= 0.65
            and max_loss <= 0.25
            and max_tail <= 0.35
            and max_tensor >= 60
        ):
            state = "MICRO_CANARY_ELIGIBLE"
            action = "ALLOW_ONLY_APPROVED_PAPER_MICRO_CANARY"
            next_build = "PAPER_MICRO_CANARY_BRIDGE_V6"
            allow_micro = 1
            force_learning = 0
            veto_new_positions = 0
            max_size_usd = 50.0
            max_daily_new_positions = 1
            required_mode = "PAPER_MICRO_CANARY"
            reasons.append("PROMOTION_CONTRACT_READY")

        elif fam_n >= 30 and fam_avg >= 0.06 and fam_wr >= 55:
            state = "FAMILY_CLUSTER_REQUIRED"
            action = "BUILD_ALPHA_CLUSTER_AGGREGATOR_V6"
            next_build = "ALPHA_CLUSTER_AGGREGATOR_V6"
            hard_vetoes.append("INDIVIDUAL_ALPHAS_FRAGMENTED")
            reasons.append(f"BEST_FAMILY={fam_symbol}:{fam_side}:{fam.get('family')}:{fam.get('horizon_min')}")
            reasons.append(f"BEST_FAMILY_N={fam_n}")
            reasons.append(f"BEST_FAMILY_AVG_R={round(fam_avg,4)}")
            reasons.append(f"BEST_FAMILY_WINRATE={round(fam_wr,2)}")

        elif max_lcb > 0 and max_p0 >= 0.85 and max_pmin >= 0.75 and max_tensor < 60:
            state = "EVIDENCE_IMPROVING_NEEDS_SAMPLE"
            action = "KEEP_RUNNING_ACCUMULATE_SAMPLE"
            next_build = "ALPHA_CLUSTER_AGGREGATOR_V6"
            hard_vetoes.append("TENSOR_QUALITY_OR_SAMPLE_CAP")
            reasons.append("POSTERIOR_STRONG_BUT_NOT_INSTITUTIONAL_GRADE")

        elif s100_avg < 0 and s300_avg < 0:
            state = "DEFENSIVE_LEARNING_ONLY"
            action = "KEEP_THRESHOLDS_STRICT_DO_NOT_RAISE_RISK"
            next_build = "REGIME_ADAPTIVE_ROUTER_V6"
            hard_vetoes.append("RECENT_SHADOW_EDGE_NEGATIVE")

        else:
            state = "LEARNING_ONLY"
            action = "KEEP_RUNNING_NO_EXECUTION_CHANGE"
            next_build = "ALPHA_CLUSTER_AGGREGATOR_V6"
            hard_vetoes.append("NO_INSTITUTIONAL_CONTRACT_READY")

        if fam_symbol:
            allowed_symbols = [str(fam_symbol)]
        if fam_side:
            allowed_sides = [str(fam_side)]

        control_score = 0.0
        control_score += min(20.0, max_meta * 0.20)
        control_score += min(20.0, max_post * 0.20)
        control_score += min(20.0, max(0.0, max_lcb) * 500.0)
        control_score += min(15.0, max(0.0, s300_avg) * 120.0)
        control_score += min(25.0, max(0.0, fam_avg) * 80.0)

        return {
            "global_state": state,
            "control_score": round(control_score, 4),
            "allow_standard_open": allow_standard_open,
            "allow_direct_open": allow_direct_open,
            "allow_paper_micro_canary": allow_micro,
            "force_learning_only": force_learning,
            "veto_new_positions": veto_new_positions,
            "max_size_usd": max_size_usd,
            "max_daily_new_positions": max_daily_new_positions,
            "allowed_symbols": allowed_symbols,
            "allowed_sides": allowed_sides,
            "required_execution_mode": required_mode,
            "recommended_action": action,
            "next_required_build": next_build,
            "hard_vetoes": hard_vetoes,
            "reasons": reasons,
        }

    def build_row(self) -> Dict[str, Any]:
        c = self.contract_summary()
        s100 = self.shadow_window(100)
        s300 = self.shadow_window(300)
        s600 = self.shadow_window(600)
        fam = self.best_family()
        t10 = self.trades_summary(10)
        t25 = self.trades_summary(25)
        open_pos = self.open_positions()
        alpha_errors = self.alpha_runtime_errors()

        d = self.decide(c, s100, s300, s600, fam, t10, t25, open_pos, alpha_errors)

        contract = {
            "version": VERSION,
            "global_state": d["global_state"],
            "allow_standard_open": d["allow_standard_open"],
            "allow_direct_open": d["allow_direct_open"],
            "allow_paper_micro_canary": d["allow_paper_micro_canary"],
            "force_learning_only": d["force_learning_only"],
            "veto_new_positions": d["veto_new_positions"],
            "max_size_usd": d["max_size_usd"],
            "max_daily_new_positions": d["max_daily_new_positions"],
            "allowed_symbols": d["allowed_symbols"],
            "allowed_sides": d["allowed_sides"],
            "required_execution_mode": d["required_execution_mode"],
            "hard_vetoes": d["hard_vetoes"],
        }

        payload = {
            "contract_summary": c,
            "shadow_100": s100,
            "shadow_300": s300,
            "shadow_600": s600,
            "best_family": fam,
            "trades_10": t10,
            "trades_25": t25,
            "open_positions": open_pos,
            "alpha_runtime_errors": alpha_errors,
            "canonical_control_plane": True,
            "no_direct_open": True,
        }

        return {
            "ts": utc_now_iso(),
            "version": VERSION,

            "global_state": d["global_state"],
            "control_score": d["control_score"],

            "allow_standard_open": d["allow_standard_open"],
            "allow_direct_open": d["allow_direct_open"],
            "allow_paper_micro_canary": d["allow_paper_micro_canary"],
            "force_learning_only": d["force_learning_only"],
            "veto_new_positions": d["veto_new_positions"],

            "max_size_usd": d["max_size_usd"],
            "max_daily_new_positions": d["max_daily_new_positions"],
            "allowed_symbols": js(d["allowed_symbols"]),
            "allowed_sides": js(d["allowed_sides"]),

            "required_execution_mode": d["required_execution_mode"],
            "recommended_action": d["recommended_action"],
            "next_required_build": d["next_required_build"],

            "contracts_n": inum(c.get("contracts_n")),
            "contract_state": str(c.get("contract_state") or "UNKNOWN"),
            "micro_canary_ready": inum(c.get("micro_canary_ready")),

            "max_meta_score": fnum(c.get("max_meta_score")),
            "max_posterior_score": fnum(c.get("max_posterior_score")),
            "max_posterior_mean_r": fnum(c.get("max_posterior_mean_r")),
            "max_posterior_lcb_r": fnum(c.get("max_posterior_lcb_r")),
            "max_prob_edge_gt_zero": fnum(c.get("max_prob_edge_gt_zero")),
            "max_prob_edge_gt_min": fnum(c.get("max_prob_edge_gt_min")),
            "max_prob_loss": fnum(c.get("max_prob_loss")),
            "max_prob_tail": fnum(c.get("max_prob_tail")),
            "max_tensor_quality": fnum(c.get("max_tensor_quality")),

            "shadow_100_avg_r": fnum(s100.get("avg_r")),
            "shadow_100_winrate": fnum(s100.get("winrate")),
            "shadow_300_avg_r": fnum(s300.get("avg_r")),
            "shadow_300_winrate": fnum(s300.get("winrate")),
            "shadow_600_avg_r": fnum(s600.get("avg_r")),
            "shadow_600_winrate": fnum(s600.get("winrate")),

            "best_family_symbol": fam.get("symbol"),
            "best_family_side": fam.get("side"),
            "best_family_name": fam.get("family"),
            "best_family_horizon_min": inum(fam.get("horizon_min")),
            "best_family_n": inum(fam.get("n")),
            "best_family_avg_r": fnum(fam.get("avg_r")),
            "best_family_winrate": fnum(fam.get("winrate")),
            "best_family_worst_r": fnum(fam.get("worst_r")),
            "best_family_best_r": fnum(fam.get("best_r")),

            "last_trade_ts": t10.get("last_trade_ts"),
            "last10_pnl_usd": fnum(t10.get("pnl_usd")),
            "last25_pnl_usd": fnum(t25.get("pnl_usd")),
            "last10_winrate_usd": fnum(t10.get("winrate_usd")),
            "last25_winrate_usd": fnum(t25.get("winrate_usd")),
            "open_positions": open_pos,

            "alpha_runtime_errors": alpha_errors,
            "hard_vetoes": js(d["hard_vetoes"]),
            "reasons": js(d["reasons"]),
            "control_contract_json": js(contract),
            "payload": js(payload),
        }

    def insert_row(self, row: Dict[str, Any]) -> None:
        cols = list(row.keys())
        q = ",".join(["?"] * len(cols))
        self.db.execute(
            f"INSERT INTO institutional_control_plane_v6 ({','.join(cols)}) VALUES ({q});",
            tuple(row[c] for c in cols),
        )

    def retention(self) -> None:
        self.db.execute("""
            DELETE FROM institutional_control_plane_v6
            WHERE id NOT IN (
                SELECT id FROM institutional_control_plane_v6
                ORDER BY id DESC
                LIMIT ?
            );
        """, (MAX_ROWS,))

        self.db.execute("""
            DELETE FROM institutional_control_plane_audit_v6
            WHERE id NOT IN (
                SELECT id FROM institutional_control_plane_audit_v6
                ORDER BY id DESC
                LIMIT ?
            );
        """, (MAX_AUDIT_ROWS,))

    def refresh(self) -> Dict[str, Any]:
        self.ensure_schema()
        row = self.build_row()
        self.insert_row(row)
        self.retention()

        result = {
            "version": VERSION,
            "global_state": row["global_state"],
            "control_score": row["control_score"],
            "recommended_action": row["recommended_action"],
            "next_required_build": row["next_required_build"],
            "allow_standard_open": row["allow_standard_open"],
            "allow_direct_open": row["allow_direct_open"],
            "allow_paper_micro_canary": row["allow_paper_micro_canary"],
            "force_learning_only": row["force_learning_only"],
            "veto_new_positions": row["veto_new_positions"],
            "hard_vetoes": json.loads(row["hard_vetoes"]),
            "reasons": json.loads(row["reasons"]),
        }

        self.audit("REFRESH", "INFO", "Institutional Control Plane V6 refreshed", result)
        return result

    def latest(self) -> List[Dict[str, Any]]:
        self.ensure_schema()
        return self.db.query("""
            SELECT
              ts,
              global_state,
              control_score,
              recommended_action,
              next_required_build,
              allow_standard_open,
              allow_direct_open,
              allow_paper_micro_canary,
              force_learning_only,
              veto_new_positions,
              required_execution_mode,
              ROUND(max_size_usd,2) AS max_size_usd,
              max_daily_new_positions,
              allowed_symbols,
              allowed_sides,
              contracts_n,
              micro_canary_ready,
              ROUND(max_meta_score,2) AS max_meta,
              ROUND(max_posterior_score,2) AS max_post,
              ROUND(max_posterior_mean_r,4) AS max_post_mean,
              ROUND(max_posterior_lcb_r,4) AS max_post_lcb,
              ROUND(max_prob_edge_gt_zero,3) AS p_gt_0,
              ROUND(max_prob_edge_gt_min,3) AS p_gt_min,
              ROUND(max_tensor_quality,2) AS tensor_q,
              ROUND(shadow_100_avg_r,4) AS sh100,
              ROUND(shadow_300_avg_r,4) AS sh300,
              ROUND(shadow_600_avg_r,4) AS sh600,
              best_family_symbol,
              best_family_side,
              best_family_name,
              best_family_horizon_min,
              best_family_n,
              ROUND(best_family_avg_r,4) AS family_avg_r,
              ROUND(best_family_winrate,2) AS family_wr,
              alpha_runtime_errors,
              hard_vetoes,
              reasons
            FROM latest_institutional_control_plane_v6;
        """)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--latest", action="store_true")
    args = parser.parse_args()

    engine = InstitutionalControlPlaneV6()

    if args.refresh:
        print(json.dumps(engine.refresh(), indent=2, sort_keys=True))

    if args.latest:
        for r in engine.latest():
            print(dict(r))


if __name__ == "__main__":
    main()
