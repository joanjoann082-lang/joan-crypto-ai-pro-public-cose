from __future__ import annotations

import argparse
import json
import math
from typing import Any, Dict, List

from joanbot.storage.db import get_db
from joanbot.utils import utc_now_iso

VERSION = "ALPHA_META_GOVERNANCE_V5_INSTITUTIONAL"

MAX_META_ROWS = 1800
MAX_AUDIT_ROWS = 300

MIN_PROMOTION_N = 90
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


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(x)))


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


class AlphaMetaGovernanceV5:
    """
    Institutional meta-governance layer.

    Reads:
    - latest_alpha_bayesian_posterior_v5

    Writes:
    - alpha_meta_governance_v5
    - latest_alpha_meta_governance_v5
    - alpha_meta_governance_audit_v5

    Does not mutate:
    - decisions
    - positions
    - trades
    - forward_cases
    - forward_results
    - runner / risk / execution
    """

    def __init__(self, db=None):
        self.db = db or get_db()

    def ensure_schema(self) -> None:
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS alpha_meta_governance_v5 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                version TEXT NOT NULL,

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

                n INTEGER NOT NULL,
                effective_n REAL NOT NULL,

                posterior_mean_r REAL NOT NULL,
                posterior_lcb_r REAL NOT NULL,
                posterior_ucb_r REAL NOT NULL,
                posterior_score REAL NOT NULL,

                prob_edge_gt_zero REAL NOT NULL,
                prob_edge_gt_min REAL NOT NULL,
                prob_loss_gt_025r REAL NOT NULL,
                prob_loss_gt_050r REAL NOT NULL,
                prob_tail_event REAL NOT NULL,

                tensor_quality REAL NOT NULL,
                tensor_validation_r REAL NOT NULL,
                tensor_lcb_r REAL NOT NULL,

                cluster_rank INTEGER NOT NULL,
                is_cluster_leader INTEGER NOT NULL,
                cluster_size INTEGER NOT NULL,
                duplicate_penalty REAL NOT NULL,

                edge_quality REAL NOT NULL,
                probability_quality REAL NOT NULL,
                safety_quality REAL NOT NULL,
                context_quality REAL NOT NULL,
                sample_quality REAL NOT NULL,
                posterior_quality REAL NOT NULL,
                cluster_quality REAL NOT NULL,

                meta_score_raw REAL NOT NULL,
                meta_score REAL NOT NULL,

                meta_state TEXT NOT NULL,
                allowed_promotion_contract INTEGER NOT NULL,
                allowed_direct_open INTEGER NOT NULL,

                size_cap_usd REAL NOT NULL,
                max_daily_per_alpha INTEGER NOT NULL,
                max_daily_global INTEGER NOT NULL,

                recommendation TEXT NOT NULL,
                next_requirement TEXT NOT NULL,
                reasons TEXT NOT NULL,
                payload TEXT NOT NULL
            );
        """)

        self.db.execute("""
            CREATE INDEX IF NOT EXISTS idx_alpha_meta_governance_v5_alpha
            ON alpha_meta_governance_v5(alpha_key, id);
        """)

        self.db.execute("""
            CREATE INDEX IF NOT EXISTS idx_alpha_meta_governance_v5_cluster
            ON alpha_meta_governance_v5(cluster_key, id);
        """)

        self.db.execute("""
            CREATE TABLE IF NOT EXISTS alpha_meta_governance_audit_v5 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                version TEXT NOT NULL,
                event TEXT NOT NULL,
                level TEXT NOT NULL,
                message TEXT NOT NULL,
                payload TEXT NOT NULL
            );
        """)

        self.db.execute("DROP VIEW IF EXISTS latest_alpha_meta_governance_v5;")
        self.db.execute("""
            CREATE VIEW latest_alpha_meta_governance_v5 AS
            SELECT m.*
            FROM alpha_meta_governance_v5 m
            JOIN (
                SELECT alpha_key, MAX(id) AS max_id
                FROM alpha_meta_governance_v5
                GROUP BY alpha_key
            ) x ON x.max_id = m.id;
        """)

    def audit(self, event: str, level: str, message: str, payload: Dict[str, Any]) -> None:
        self.db.execute("""
            INSERT INTO alpha_meta_governance_audit_v5 (
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

    def posterior_rows(self) -> List[Dict[str, Any]]:
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
                posterior_ucb_r,
                posterior_score,
                prob_edge_gt_zero,
                prob_edge_gt_min,
                prob_loss_gt_025r,
                prob_loss_gt_050r,
                prob_tail_event,
                tensor_quality,
                tensor_validation_r,
                tensor_lcb_r,
                posterior_state,
                allowed_meta_governance,
                recommended_next_action,
                payload
            FROM latest_alpha_bayesian_posterior_v5;
        """)

    def cluster_ranked(self, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        clusters: Dict[str, List[Dict[str, Any]]] = {}

        for r in rows:
            clusters.setdefault(str(r.get("cluster_key")), []).append(r)

        out: List[Dict[str, Any]] = []

        for _, members in clusters.items():
            members.sort(
                key=lambda r: (
                    inum(r.get("allowed_meta_governance")),
                    fnum(r.get("posterior_score")),
                    fnum(r.get("posterior_lcb_r")),
                    fnum(r.get("posterior_mean_r")),
                    fnum(r.get("prob_edge_gt_min")),
                    fnum(r.get("n")),
                    fnum(r.get("tensor_quality")),
                ),
                reverse=True,
            )

            size = len(members)
            for idx, r in enumerate(members, start=1):
                rr = dict(r)
                rr["cluster_rank"] = idx
                rr["is_cluster_leader"] = 1 if idx == 1 else 0
                rr["cluster_size"] = size
                out.append(rr)

        return out

    def quality(self, r: Dict[str, Any]) -> Dict[str, float]:
        n = inum(r.get("n"))
        eff_n = fnum(r.get("effective_n"))

        posterior_mean = fnum(r.get("posterior_mean_r"))
        posterior_lcb = fnum(r.get("posterior_lcb_r"))
        posterior_score = fnum(r.get("posterior_score"))

        p_gt_0 = fnum(r.get("prob_edge_gt_zero"))
        p_gt_min = fnum(r.get("prob_edge_gt_min"))
        p_loss_025 = fnum(r.get("prob_loss_gt_025r"))
        p_tail = fnum(r.get("prob_tail_event"))

        ctx_fit = fnum(r.get("current_context_fit"))
        tensor_q = fnum(r.get("tensor_quality"))
        tensor_val = fnum(r.get("tensor_validation_r"))
        tensor_lcb = fnum(r.get("tensor_lcb_r"))

        is_leader = inum(r.get("is_cluster_leader"))
        cluster_rank = inum(r.get("cluster_rank"))
        cluster_size = max(1, inum(r.get("cluster_size")))

        sample_q = clamp(math.sqrt(max(0.0, eff_n) / 120.0), 0.0, 1.0)

        edge_q = (
            0.42 * clamp(posterior_mean / 0.08, 0.0, 1.0)
            + 0.28 * clamp((posterior_lcb + 0.01) / 0.06, 0.0, 1.0)
            + 0.18 * clamp(tensor_val / 0.08, 0.0, 1.0)
            + 0.12 * clamp(tensor_lcb / 0.06, 0.0, 1.0)
        )

        prob_q = (
            0.55 * clamp(p_gt_min, 0.0, 1.0)
            + 0.30 * clamp(p_gt_0, 0.0, 1.0)
            + 0.15 * clamp(posterior_score / 100.0, 0.0, 1.0)
        )

        safety_q = (
            0.46 * (1.0 - clamp(p_loss_025 / 0.35, 0.0, 1.0))
            + 0.36 * (1.0 - clamp(p_tail / 0.50, 0.0, 1.0))
            + 0.18 * clamp(tensor_q / 100.0, 0.0, 1.0)
        )

        context_q = clamp(ctx_fit, 0.0, 1.0)
        posterior_q = clamp(posterior_score / 100.0, 0.0, 1.0)

        if is_leader:
            cluster_q = 1.0
            duplicate_penalty = 0.0
        else:
            cluster_q = clamp(0.45 / max(1, cluster_rank), 0.05, 0.35)
            duplicate_penalty = clamp(0.18 + (cluster_rank - 2) * 0.04, 0.18, 0.42)

        raw = 100.0 * (
            0.24 * edge_q
            + 0.20 * prob_q
            + 0.18 * safety_q
            + 0.13 * context_q
            + 0.12 * sample_q
            + 0.08 * posterior_q
            + 0.05 * cluster_q
        )

        score = raw * (1.0 - duplicate_penalty)

        caps = [100.0]

        if n < 30:
            caps.append(25.0)
        elif n < 60:
            caps.append(45.0)
        elif n < 90:
            caps.append(64.0)
        elif n < 150:
            caps.append(84.0)

        if posterior_lcb <= 0:
            caps.append(70.0)
        if tensor_val <= 0:
            caps.append(62.0)
        if p_loss_025 > MAX_PROB_LOSS_025:
            caps.append(68.0)
        if p_tail > MAX_PROB_TAIL:
            caps.append(64.0)
        if ctx_fit < MIN_CONTEXT_FIT:
            caps.append(68.0)
        if tensor_q < MIN_TENSOR_QUALITY:
            caps.append(70.0)
        if not is_leader:
            caps.append(48.0)

        score = min(score, min(caps))

        return {
            "edge_quality": round(edge_q, 8),
            "probability_quality": round(prob_q, 8),
            "safety_quality": round(safety_q, 8),
            "context_quality": round(context_q, 8),
            "sample_quality": round(sample_q, 8),
            "posterior_quality": round(posterior_q, 8),
            "cluster_quality": round(cluster_q, 8),
            "duplicate_penalty": round(duplicate_penalty, 8),
            "meta_score_raw": round(raw, 8),
            "meta_score": round(score, 8),
            "caps": caps,
        }

    def state(self, r: Dict[str, Any], q: Dict[str, float]) -> Dict[str, Any]:
        n = inum(r.get("n"))
        posterior_mean = fnum(r.get("posterior_mean_r"))
        posterior_lcb = fnum(r.get("posterior_lcb_r"))
        posterior_score = fnum(r.get("posterior_score"))
        p_gt_0 = fnum(r.get("prob_edge_gt_zero"))
        p_gt_min = fnum(r.get("prob_edge_gt_min"))
        p_loss_025 = fnum(r.get("prob_loss_gt_025r"))
        p_tail = fnum(r.get("prob_tail_event"))
        ctx_fit = fnum(r.get("current_context_fit"))
        tensor_q = fnum(r.get("tensor_quality"))
        tensor_val = fnum(r.get("tensor_validation_r"))
        is_leader = inum(r.get("is_cluster_leader"))
        meta_score = fnum(q.get("meta_score"))

        reasons: List[str] = []

        if n < 30:
            meta_state = "META_DISCOVERY"
            reasons.append("SAMPLE_TOO_SMALL")
        elif posterior_mean <= 0:
            meta_state = "META_REJECTED_NO_POSTERIOR_EDGE"
            reasons.append("POSTERIOR_MEAN_NOT_POSITIVE")
        elif p_tail > 0.50:
            meta_state = "META_REJECTED_TAIL_RISK"
            reasons.append("TAIL_PROBABILITY_TOO_HIGH")
        elif not is_leader:
            meta_state = "META_REJECTED_DUPLICATE_CLUSTER"
            reasons.append("NON_LEADER_DUPLICATE_ALPHA")
        elif posterior_score >= 55 and posterior_mean > 0:
            meta_state = "META_RESEARCH_READY"
            reasons.append("META_RESEARCH_READY")
        else:
            meta_state = "META_WATCHLIST"
            reasons.append("ACCUMULATE_MORE_EVIDENCE")

        missing: List[str] = []
        if n < MIN_PROMOTION_N:
            missing.append(f"n>={MIN_PROMOTION_N}")
        if posterior_score < MIN_POSTERIOR_SCORE:
            missing.append(f"posterior_score>={MIN_POSTERIOR_SCORE}")
        if posterior_mean < MIN_POSTERIOR_MEAN_R:
            missing.append(f"posterior_mean_r>={MIN_POSTERIOR_MEAN_R}")
        if posterior_lcb <= MIN_POSTERIOR_LCB_R:
            missing.append("posterior_lcb_r>0")
        if p_gt_0 < MIN_PROB_EDGE_GT_ZERO:
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
        if meta_score < MIN_META_SCORE:
            missing.append(f"meta_score>={MIN_META_SCORE}")

        allowed = (
            n >= MIN_PROMOTION_N
            and posterior_score >= MIN_POSTERIOR_SCORE
            and posterior_mean >= MIN_POSTERIOR_MEAN_R
            and posterior_lcb > MIN_POSTERIOR_LCB_R
            and p_gt_0 >= MIN_PROB_EDGE_GT_ZERO
            and p_gt_min >= MIN_PROB_EDGE_GT_MIN
            and p_loss_025 <= MAX_PROB_LOSS_025
            and p_tail <= MAX_PROB_TAIL
            and ctx_fit >= MIN_CONTEXT_FIT
            and tensor_q >= MIN_TENSOR_QUALITY
            and tensor_val > 0
            and bool(is_leader)
            and meta_score >= MIN_META_SCORE
        )

        size_cap = 0.0
        max_alpha = 0
        max_global = 0

        if allowed:
            meta_state = "META_PROMOTION_CONTRACT_READY"
            reasons.append("READY_FOR_PROMOTION_CONTRACT_V5")
            recommendation = "PROMOTION_CONTRACT_V5"
            size_cap = 50.0
            if meta_score >= 88 and n >= 150:
                size_cap = 100.0
            max_alpha = 1
            max_global = 2
        else:
            recommendation = "NEEDS_" + "_".join(missing[:5]) if missing else "WATCHLIST"

        next_requirement = "Ready for Promotion Contract V5." if allowed else "Needs: " + ", ".join(missing)

        return {
            "meta_state": meta_state,
            "allowed": allowed,
            "size_cap_usd": size_cap,
            "max_daily_per_alpha": max_alpha,
            "max_daily_global": max_global,
            "recommendation": recommendation,
            "next_requirement": next_requirement,
            "reasons": reasons,
            "missing": missing,
        }

    def build_rows(self) -> List[Dict[str, Any]]:
        rows = self.cluster_ranked(self.posterior_rows())
        out: List[Dict[str, Any]] = []

        for r in rows:
            q = self.quality(r)
            st = self.state(r, q)

            payload = {
                "source": VERSION,
                "posterior_payload": r.get("payload"),
                "quality_components": q,
                "missing_requirements": st["missing"],
                "no_execution": True,
                "allowed_direct_open": False,
            }

            out.append({
                "ts": utc_now_iso(),
                "version": VERSION,

                "alpha_key": r["alpha_key"],
                "cluster_key": r["cluster_key"],

                "symbol": r["symbol"],
                "side": r["side"],
                "setup": r["setup"],
                "profile": r["profile"],
                "horizon_min": inum(r["horizon_min"]),

                "learned_context_bucket": r["learned_context_bucket"],
                "current_context_bucket": r["current_context_bucket"],
                "current_context_fit": round(fnum(r["current_context_fit"]), 8),

                "n": inum(r["n"]),
                "effective_n": round(fnum(r["effective_n"]), 8),

                "posterior_mean_r": round(fnum(r["posterior_mean_r"]), 8),
                "posterior_lcb_r": round(fnum(r["posterior_lcb_r"]), 8),
                "posterior_ucb_r": round(fnum(r["posterior_ucb_r"]), 8),
                "posterior_score": round(fnum(r["posterior_score"]), 8),

                "prob_edge_gt_zero": round(fnum(r["prob_edge_gt_zero"]), 8),
                "prob_edge_gt_min": round(fnum(r["prob_edge_gt_min"]), 8),
                "prob_loss_gt_025r": round(fnum(r["prob_loss_gt_025r"]), 8),
                "prob_loss_gt_050r": round(fnum(r["prob_loss_gt_050r"]), 8),
                "prob_tail_event": round(fnum(r["prob_tail_event"]), 8),

                "tensor_quality": round(fnum(r["tensor_quality"]), 8),
                "tensor_validation_r": round(fnum(r["tensor_validation_r"]), 8),
                "tensor_lcb_r": round(fnum(r["tensor_lcb_r"]), 8),

                "cluster_rank": inum(r["cluster_rank"]),
                "is_cluster_leader": inum(r["is_cluster_leader"]),
                "cluster_size": inum(r["cluster_size"]),
                "duplicate_penalty": q["duplicate_penalty"],

                "edge_quality": q["edge_quality"],
                "probability_quality": q["probability_quality"],
                "safety_quality": q["safety_quality"],
                "context_quality": q["context_quality"],
                "sample_quality": q["sample_quality"],
                "posterior_quality": q["posterior_quality"],
                "cluster_quality": q["cluster_quality"],

                "meta_score_raw": q["meta_score_raw"],
                "meta_score": q["meta_score"],

                "meta_state": st["meta_state"],
                "allowed_promotion_contract": 1 if st["allowed"] else 0,
                "allowed_direct_open": 0,

                "size_cap_usd": st["size_cap_usd"],
                "max_daily_per_alpha": st["max_daily_per_alpha"],
                "max_daily_global": st["max_daily_global"],

                "recommendation": st["recommendation"],
                "next_requirement": st["next_requirement"],
                "reasons": json.dumps(st["reasons"], separators=(",", ":"), ensure_ascii=False),
                "payload": json.dumps(payload, separators=(",", ":"), ensure_ascii=False),
            })

        out.sort(
            key=lambda x: (
                x["allowed_promotion_contract"],
                x["meta_score"],
                x["posterior_score"],
                x["posterior_mean_r"],
                x["n"],
            ),
            reverse=True,
        )

        return out

    def insert_row(self, row: Dict[str, Any]) -> None:
        cols = [
            "ts", "version",
            "alpha_key", "cluster_key",
            "symbol", "side", "setup", "profile", "horizon_min",
            "learned_context_bucket", "current_context_bucket", "current_context_fit",
            "n", "effective_n",
            "posterior_mean_r", "posterior_lcb_r", "posterior_ucb_r", "posterior_score",
            "prob_edge_gt_zero", "prob_edge_gt_min",
            "prob_loss_gt_025r", "prob_loss_gt_050r", "prob_tail_event",
            "tensor_quality", "tensor_validation_r", "tensor_lcb_r",
            "cluster_rank", "is_cluster_leader", "cluster_size", "duplicate_penalty",
            "edge_quality", "probability_quality", "safety_quality", "context_quality",
            "sample_quality", "posterior_quality", "cluster_quality",
            "meta_score_raw", "meta_score",
            "meta_state", "allowed_promotion_contract", "allowed_direct_open",
            "size_cap_usd", "max_daily_per_alpha", "max_daily_global",
            "recommendation", "next_requirement", "reasons", "payload",
        ]

        q = ",".join(["?"] * len(cols))
        self.db.execute(
            f"INSERT INTO alpha_meta_governance_v5 ({','.join(cols)}) VALUES ({q});",
            tuple(row[c] for c in cols),
        )

    def retention(self) -> None:
        self.db.execute("""
            DELETE FROM alpha_meta_governance_v5
            WHERE id NOT IN (
                SELECT id FROM alpha_meta_governance_v5
                ORDER BY id DESC
                LIMIT ?
            );
        """, (MAX_META_ROWS,))

        self.db.execute("""
            DELETE FROM alpha_meta_governance_audit_v5
            WHERE id NOT IN (
                SELECT id FROM alpha_meta_governance_audit_v5
                ORDER BY id DESC
                LIMIT ?
            );
        """, (MAX_AUDIT_ROWS,))

    def refresh(self) -> Dict[str, Any]:
        self.ensure_schema()

        rows = self.build_rows()
        for row in rows:
            self.insert_row(row)

        self.retention()

        states: Dict[str, int] = {}
        for r in rows:
            states[r["meta_state"]] = states.get(r["meta_state"], 0) + 1

        result = {
            "version": VERSION,
            "input_posterior_rows": len(rows),
            "meta_rows_created": len(rows),
            "states": states,
            "promotion_contract_ready": states.get("META_PROMOTION_CONTRACT_READY", 0),
            "no_execution": True,
        }

        self.audit("REFRESH", "INFO", "Alpha Meta Governance V5 refreshed", result)
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
                ROUND(effective_n,2) AS eff_n,
                ROUND(posterior_mean_r,4) AS post_mean,
                ROUND(posterior_lcb_r,4) AS post_lcb,
                ROUND(prob_edge_gt_zero,3) AS p_gt_0,
                ROUND(prob_edge_gt_min,3) AS p_gt_min,
                ROUND(prob_loss_gt_025r,3) AS p_loss_025,
                ROUND(prob_tail_event,3) AS p_tail,
                ROUND(tensor_quality,2) AS tensor_q,
                ROUND(current_context_fit,2) AS ctx_fit,
                cluster_rank,
                cluster_size,
                ROUND(meta_score,2) AS meta_score,
                meta_state,
                allowed_promotion_contract,
                ROUND(size_cap_usd,2) AS cap,
                recommendation
            FROM latest_alpha_meta_governance_v5
            ORDER BY allowed_promotion_contract DESC, meta_score DESC, posterior_score DESC, posterior_mean_r DESC, n DESC
            LIMIT ?;
        """, (limit,))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--latest", action="store_true")
    args = parser.parse_args()

    engine = AlphaMetaGovernanceV5()

    if args.refresh:
        print(json.dumps(engine.refresh(), indent=2, sort_keys=True))

    if args.latest:
        for r in engine.latest():
            print(r)


if __name__ == "__main__":
    main()
