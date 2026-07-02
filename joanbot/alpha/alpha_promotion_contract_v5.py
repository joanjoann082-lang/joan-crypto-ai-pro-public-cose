from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from joanbot.storage.db import get_db
from joanbot.utils import utc_now_iso

VERSION = "ALPHA_PROMOTION_CONTRACT_V5_INSTITUTIONAL"

MAX_CONTRACT_ROWS = 1800
MAX_AUDIT_ROWS = 300

MIN_N = 90
MIN_META_SCORE = 78.0
MIN_POSTERIOR_SCORE = 75.0
MIN_POSTERIOR_MEAN_R = 0.035
MIN_POSTERIOR_LCB_R = 0.0
MIN_PROB_EDGE_GT_ZERO = 0.75
MIN_PROB_EDGE_GT_MIN = 0.65
MAX_PROB_LOSS_025 = 0.25
MAX_PROB_TAIL = 0.35
MIN_CONTEXT_FIT = 0.60
MIN_TENSOR_QUALITY = 60.0

BASE_SIZE_CAP_USD = 50.0
STRONG_SIZE_CAP_USD = 100.0


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


def now_dt() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


class AlphaPromotionContractV5:
    """
    Institutional promotion contract layer.

    Reads:
    - latest_alpha_meta_governance_v5

    Writes:
    - alpha_promotion_contract_v5
    - latest_alpha_promotion_contract_v5
    - alpha_promotion_contract_audit_v5

    Does not mutate:
    - decisions
    - risk
    - broker
    - execution
    - forward_cases
    - forward_results
    - positions
    - trades
    """

    def __init__(self, db=None):
        self.db = db or get_db()

    def ensure_schema(self) -> None:
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS alpha_promotion_contract_v5 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                version TEXT NOT NULL,

                contract_id TEXT NOT NULL,
                alpha_key TEXT NOT NULL,
                cluster_key TEXT NOT NULL,

                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                setup TEXT NOT NULL,
                profile TEXT NOT NULL,
                horizon_min INTEGER NOT NULL,

                learned_context_bucket TEXT NOT NULL,
                current_context_bucket TEXT NOT NULL,
                current_context_fit REAL NOT NULL,

                valid_from TEXT NOT NULL,
                expires_at TEXT NOT NULL,

                n INTEGER NOT NULL,
                effective_n REAL NOT NULL,

                meta_score REAL NOT NULL,
                posterior_score REAL NOT NULL,
                posterior_mean_r REAL NOT NULL,
                posterior_lcb_r REAL NOT NULL,

                prob_edge_gt_zero REAL NOT NULL,
                prob_edge_gt_min REAL NOT NULL,
                prob_loss_gt_025r REAL NOT NULL,
                prob_tail_event REAL NOT NULL,

                tensor_quality REAL NOT NULL,
                tensor_validation_r REAL NOT NULL,

                cluster_rank INTEGER NOT NULL,
                is_cluster_leader INTEGER NOT NULL,
                cluster_size INTEGER NOT NULL,

                allowed_paper_micro_canary INTEGER NOT NULL,
                allowed_direct_open INTEGER NOT NULL,
                required_execution_mode TEXT NOT NULL,

                size_cap_usd REAL NOT NULL,
                size_multiplier_cap REAL NOT NULL,
                max_daily_per_alpha INTEGER NOT NULL,
                max_daily_global INTEGER NOT NULL,

                contract_state TEXT NOT NULL,
                recommendation TEXT NOT NULL,
                next_requirement TEXT NOT NULL,
                reasons TEXT NOT NULL,
                payload TEXT NOT NULL
            );
        """)

        self.db.execute("""
            CREATE INDEX IF NOT EXISTS idx_alpha_promotion_contract_v5_alpha
            ON alpha_promotion_contract_v5(alpha_key, id);
        """)

        self.db.execute("""
            CREATE INDEX IF NOT EXISTS idx_alpha_promotion_contract_v5_contract
            ON alpha_promotion_contract_v5(contract_id, id);
        """)

        self.db.execute("""
            CREATE TABLE IF NOT EXISTS alpha_promotion_contract_audit_v5 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                version TEXT NOT NULL,
                event TEXT NOT NULL,
                level TEXT NOT NULL,
                message TEXT NOT NULL,
                payload TEXT NOT NULL
            );
        """)

        self.db.execute("DROP VIEW IF EXISTS latest_alpha_promotion_contract_v5;")
        self.db.execute("""
            CREATE VIEW latest_alpha_promotion_contract_v5 AS
            SELECT c.*
            FROM alpha_promotion_contract_v5 c
            JOIN (
                SELECT alpha_key, MAX(id) AS max_id
                FROM alpha_promotion_contract_v5
                GROUP BY alpha_key
            ) x ON x.max_id = c.id;
        """)

    def audit(self, event: str, level: str, message: str, payload: Dict[str, Any]) -> None:
        self.db.execute("""
            INSERT INTO alpha_promotion_contract_audit_v5 (
                ts, version, event, level, message, payload
            )
            VALUES (?, ?, ?, ?, ?, ?);
        """, (
            utc_now_iso(),
            VERSION,
            event,
            level,
            message[:500],
            json.dumps(payload, separators=(",", ":"), ensure_ascii=False),
        ))

    def meta_rows(self) -> List[Dict[str, Any]]:
        return self.db.query("""
            SELECT
                alpha_key,
                cluster_key,
                symbol,
                side,
                setup,
                profile,
                horizon_min,
                learned_context_bucket,
                current_context_bucket,
                current_context_fit,
                n,
                effective_n,
                posterior_mean_r,
                posterior_lcb_r,
                posterior_score,
                prob_edge_gt_zero,
                prob_edge_gt_min,
                prob_loss_gt_025r,
                prob_tail_event,
                tensor_quality,
                tensor_validation_r,
                cluster_rank,
                is_cluster_leader,
                cluster_size,
                meta_score,
                meta_state,
                allowed_promotion_contract,
                allowed_direct_open,
                size_cap_usd,
                max_daily_per_alpha,
                max_daily_global,
                recommendation,
                next_requirement,
                reasons,
                payload
            FROM latest_alpha_meta_governance_v5;
        """)

    def contract_id(self, r: Dict[str, Any], valid_from: str) -> str:
        raw = "|".join([
            VERSION,
            str(r.get("alpha_key")),
            str(r.get("current_context_bucket")),
            str(r.get("meta_score")),
            valid_from[:13],
        ])
        return "apcv5_" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:18]

    def build_contract(self, r: Dict[str, Any]) -> Dict[str, Any]:
        n = inum(r.get("n"))
        meta_score = fnum(r.get("meta_score"))
        posterior_score = fnum(r.get("posterior_score"))
        posterior_mean = fnum(r.get("posterior_mean_r"))
        posterior_lcb = fnum(r.get("posterior_lcb_r"))
        p_gt_zero = fnum(r.get("prob_edge_gt_zero"))
        p_gt_min = fnum(r.get("prob_edge_gt_min"))
        p_loss_025 = fnum(r.get("prob_loss_gt_025r"))
        p_tail = fnum(r.get("prob_tail_event"))
        ctx_fit = fnum(r.get("current_context_fit"))
        tensor_q = fnum(r.get("tensor_quality"))
        tensor_val = fnum(r.get("tensor_validation_r"))
        is_leader = inum(r.get("is_cluster_leader"))
        meta_allowed = inum(r.get("allowed_promotion_contract"))
        direct_open = inum(r.get("allowed_direct_open"))

        reasons: List[str] = []
        missing: List[str] = []

        if n < MIN_N:
            missing.append(f"n>={MIN_N}")
        if meta_score < MIN_META_SCORE:
            missing.append(f"meta_score>={MIN_META_SCORE}")
        if posterior_score < MIN_POSTERIOR_SCORE:
            missing.append(f"posterior_score>={MIN_POSTERIOR_SCORE}")
        if posterior_mean < MIN_POSTERIOR_MEAN_R:
            missing.append(f"posterior_mean_r>={MIN_POSTERIOR_MEAN_R}")
        if posterior_lcb <= MIN_POSTERIOR_LCB_R:
            missing.append("posterior_lcb_r>0")
        if p_gt_zero < MIN_PROB_EDGE_GT_ZERO:
            missing.append(f"prob_edge_gt_zero>={MIN_PROB_EDGE_GT_ZERO}")
        if p_gt_min < MIN_PROB_EDGE_GT_MIN:
            missing.append(f"prob_edge_gt_min>={MIN_PROB_EDGE_GT_MIN}")
        if p_loss_025 > MAX_PROB_LOSS_025:
            missing.append(f"prob_loss_gt_025r<={MAX_PROB_LOSS_025}")
        if p_tail > MAX_PROB_TAIL:
            missing.append(f"prob_tail_event<={MAX_PROB_TAIL}")
        if ctx_fit < MIN_CONTEXT_FIT:
            missing.append(f"context_fit>={MIN_CONTEXT_FIT}")
        if tensor_q < MIN_TENSOR_QUALITY:
            missing.append(f"tensor_quality>={MIN_TENSOR_QUALITY}")
        if tensor_val <= 0:
            missing.append("tensor_validation_r>0")
        if not is_leader:
            missing.append("cluster_leader")
        if not meta_allowed:
            missing.append("meta_governance_allowed")
        if direct_open != 0:
            missing.append("direct_open_must_be_false")

        allowed = (
            n >= MIN_N
            and meta_score >= MIN_META_SCORE
            and posterior_score >= MIN_POSTERIOR_SCORE
            and posterior_mean >= MIN_POSTERIOR_MEAN_R
            and posterior_lcb > MIN_POSTERIOR_LCB_R
            and p_gt_zero >= MIN_PROB_EDGE_GT_ZERO
            and p_gt_min >= MIN_PROB_EDGE_GT_MIN
            and p_loss_025 <= MAX_PROB_LOSS_025
            and p_tail <= MAX_PROB_TAIL
            and ctx_fit >= MIN_CONTEXT_FIT
            and tensor_q >= MIN_TENSOR_QUALITY
            and tensor_val > 0
            and bool(is_leader)
            and bool(meta_allowed)
            and direct_open == 0
        )

        valid_from_dt = now_dt()
        horizon = max(15, inum(r.get("horizon_min"), 60))
        ttl_min = min(360, max(60, horizon * 2))
        expires_at_dt = valid_from_dt + timedelta(minutes=ttl_min)

        valid_from = iso(valid_from_dt)
        expires_at = iso(expires_at_dt)

        if allowed:
            state = "PAPER_MICRO_CANARY_CONTRACT_READY"
            recommendation = "PAPER_MICRO_CANARY_BRIDGE_ELIGIBLE"
            reasons.append("ALL_PROMOTION_CONTRACT_REQUIREMENTS_MET")
            size_cap = BASE_SIZE_CAP_USD
            if meta_score >= 88 and n >= 150:
                size_cap = STRONG_SIZE_CAP_USD
            size_mult_cap = 0.01
            max_alpha = 1
            max_global = 2
            mode = "PAPER_MICRO_CANARY"
        else:
            state = "CONTRACT_NOT_READY"
            recommendation = "DO_NOT_TRADE"
            reasons.append("PROMOTION_CONTRACT_REQUIREMENTS_NOT_MET")
            size_cap = 0.0
            size_mult_cap = 0.0
            max_alpha = 0
            max_global = 0
            mode = "NONE"

        next_requirement = "Ready for future Paper Micro-Canary Bridge." if allowed else "Needs: " + ", ".join(missing)

        payload = {
            "source": VERSION,
            "meta_state": r.get("meta_state"),
            "meta_recommendation": r.get("recommendation"),
            "meta_payload": r.get("payload"),
            "missing_requirements": missing,
            "no_execution": True,
            "allowed_direct_open": False,
            "future_bridge_only": True,
        }

        row = {
            "ts": utc_now_iso(),
            "version": VERSION,
            "contract_id": "",
            "alpha_key": r["alpha_key"],
            "cluster_key": r["cluster_key"],
            "symbol": r["symbol"],
            "side": r["side"],
            "setup": r["setup"],
            "profile": r["profile"],
            "horizon_min": horizon,
            "learned_context_bucket": r["learned_context_bucket"],
            "current_context_bucket": r["current_context_bucket"],
            "current_context_fit": round(ctx_fit, 8),
            "valid_from": valid_from,
            "expires_at": expires_at,
            "n": n,
            "effective_n": round(fnum(r.get("effective_n")), 8),
            "meta_score": round(meta_score, 8),
            "posterior_score": round(posterior_score, 8),
            "posterior_mean_r": round(posterior_mean, 8),
            "posterior_lcb_r": round(posterior_lcb, 8),
            "prob_edge_gt_zero": round(p_gt_zero, 8),
            "prob_edge_gt_min": round(p_gt_min, 8),
            "prob_loss_gt_025r": round(p_loss_025, 8),
            "prob_tail_event": round(p_tail, 8),
            "tensor_quality": round(tensor_q, 8),
            "tensor_validation_r": round(tensor_val, 8),
            "cluster_rank": inum(r.get("cluster_rank")),
            "is_cluster_leader": is_leader,
            "cluster_size": inum(r.get("cluster_size")),
            "allowed_paper_micro_canary": 1 if allowed else 0,
            "allowed_direct_open": 0,
            "required_execution_mode": mode,
            "size_cap_usd": round(size_cap, 2),
            "size_multiplier_cap": round(size_mult_cap, 8),
            "max_daily_per_alpha": max_alpha,
            "max_daily_global": max_global,
            "contract_state": state,
            "recommendation": recommendation,
            "next_requirement": next_requirement,
            "reasons": json.dumps(reasons, separators=(",", ":"), ensure_ascii=False),
            "payload": json.dumps(payload, separators=(",", ":"), ensure_ascii=False),
        }

        row["contract_id"] = self.contract_id(row, valid_from)
        return row

    def insert_row(self, row: Dict[str, Any]) -> None:
        cols = [
            "ts", "version",
            "contract_id", "alpha_key", "cluster_key",
            "symbol", "side", "setup", "profile", "horizon_min",
            "learned_context_bucket", "current_context_bucket", "current_context_fit",
            "valid_from", "expires_at",
            "n", "effective_n",
            "meta_score", "posterior_score", "posterior_mean_r", "posterior_lcb_r",
            "prob_edge_gt_zero", "prob_edge_gt_min", "prob_loss_gt_025r", "prob_tail_event",
            "tensor_quality", "tensor_validation_r",
            "cluster_rank", "is_cluster_leader", "cluster_size",
            "allowed_paper_micro_canary", "allowed_direct_open", "required_execution_mode",
            "size_cap_usd", "size_multiplier_cap",
            "max_daily_per_alpha", "max_daily_global",
            "contract_state", "recommendation", "next_requirement",
            "reasons", "payload",
        ]

        q = ",".join(["?"] * len(cols))
        self.db.execute(
            f"INSERT INTO alpha_promotion_contract_v5 ({','.join(cols)}) VALUES ({q});",
            tuple(row[c] for c in cols),
        )

    def retention(self) -> None:
        self.db.execute("""
            DELETE FROM alpha_promotion_contract_v5
            WHERE id NOT IN (
                SELECT id FROM alpha_promotion_contract_v5
                ORDER BY id DESC
                LIMIT ?
            );
        """, (MAX_CONTRACT_ROWS,))

        self.db.execute("""
            DELETE FROM alpha_promotion_contract_audit_v5
            WHERE id NOT IN (
                SELECT id FROM alpha_promotion_contract_audit_v5
                ORDER BY id DESC
                LIMIT ?
            );
        """, (MAX_AUDIT_ROWS,))

    def refresh(self) -> Dict[str, Any]:
        self.ensure_schema()

        rows = [self.build_contract(r) for r in self.meta_rows()]
        for row in rows:
            self.insert_row(row)

        self.retention()

        states: Dict[str, int] = {}
        for r in rows:
            states[r["contract_state"]] = states.get(r["contract_state"], 0) + 1

        result = {
            "version": VERSION,
            "input_meta_rows": len(rows),
            "contract_rows_created": len(rows),
            "states": states,
            "paper_micro_canary_contract_ready": states.get("PAPER_MICRO_CANARY_CONTRACT_READY", 0),
            "no_execution": True,
        }

        self.audit("REFRESH", "INFO", "Alpha Promotion Contract V5 refreshed", result)
        return result

    def latest(self, limit: int = 60) -> List[Dict[str, Any]]:
        self.ensure_schema()
        return self.db.query("""
            SELECT
                symbol,
                side,
                setup,
                profile,
                horizon_min,
                n,
                ROUND(meta_score,2) AS meta_score,
                ROUND(posterior_score,2) AS posterior_score,
                ROUND(posterior_mean_r,4) AS post_mean,
                ROUND(posterior_lcb_r,4) AS post_lcb,
                ROUND(prob_edge_gt_zero,3) AS p_gt_0,
                ROUND(prob_edge_gt_min,3) AS p_gt_min,
                ROUND(prob_loss_gt_025r,3) AS p_loss_025,
                ROUND(prob_tail_event,3) AS p_tail,
                ROUND(tensor_quality,2) AS tensor_q,
                ROUND(current_context_fit,2) AS ctx_fit,
                cluster_rank,
                contract_state,
                allowed_paper_micro_canary,
                allowed_direct_open,
                required_execution_mode,
                ROUND(size_cap_usd,2) AS cap,
                expires_at,
                recommendation
            FROM latest_alpha_promotion_contract_v5
            ORDER BY allowed_paper_micro_canary DESC, meta_score DESC, posterior_score DESC, posterior_mean_r DESC, n DESC
            LIMIT ?;
        """, (limit,))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--latest", action="store_true")
    args = parser.parse_args()

    engine = AlphaPromotionContractV5()

    if args.refresh:
        print(json.dumps(engine.refresh(), indent=2, sort_keys=True))

    if args.latest:
        for r in engine.latest():
            print(r)


if __name__ == "__main__":
    main()
