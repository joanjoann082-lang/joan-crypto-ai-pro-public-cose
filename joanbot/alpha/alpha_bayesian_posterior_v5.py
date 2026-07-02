from __future__ import annotations

import argparse
import json
import math
from typing import Any, Dict, List

from joanbot.storage.db import get_db
from joanbot.utils import utc_now_iso, fnum

VERSION = "ALPHA_BAYESIAN_POSTERIOR_V5_INSTITUTIONAL"

MAX_POSTERIOR_ROWS = 1800
MAX_AUDIT_ROWS = 300

MIN_META_N = 90
MIN_POSTERIOR_MEAN_R = 0.035
MIN_POSTERIOR_LCB_R = 0.0
MIN_PROB_EDGE_GT_MIN = 0.65
MIN_PROB_EDGE_GT_ZERO = 0.75
MAX_PROB_LOSS_GT_025 = 0.25
MIN_CONTEXT_FIT = 0.60
MIN_TENSOR_QUALITY = 60.0
MIN_POSTERIOR_SCORE = 75.0
EDGE_MIN_R = 0.03


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(x)))


def safe_float(x: Any, default: float = 0.0) -> float:
    try:
        if x is None:
            return default
        return float(x)
    except Exception:
        return default


def safe_int(x: Any, default: int = 0) -> int:
    try:
        if x is None:
            return default
        return int(float(x))
    except Exception:
        return default


def normal_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def prob_gt(threshold: float, mean: float, std: float) -> float:
    std = max(abs(std), 1e-9)
    return 1.0 - normal_cdf((threshold - mean) / std)


def prob_lt(threshold: float, mean: float, std: float) -> float:
    std = max(abs(std), 1e-9)
    return normal_cdf((threshold - mean) / std)


class AlphaBayesianPosteriorV5:
    """
    Bayesian posterior layer for institutional alpha intelligence.

    Input:
    - latest_alpha_evidence_tensor_v5

    Output:
    - alpha_bayesian_posterior_v5
    - latest_alpha_bayesian_posterior_v5
    - alpha_bayesian_posterior_audit_v5

    This module does not mutate trading path or execution tables.
    """

    def __init__(self, db=None):
        self.db = db or get_db()

    def ensure_schema(self) -> None:
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS alpha_bayesian_posterior_v5 (
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

                tensor_expectancy_r REAL NOT NULL,
                tensor_shrunk_r REAL NOT NULL,
                tensor_lcb_r REAL NOT NULL,
                tensor_validation_r REAL NOT NULL,
                tensor_recent_r REAL NOT NULL,
                tensor_older_r REAL NOT NULL,

                tensor_std_r REAL NOT NULL,
                tensor_pf_cap REAL NOT NULL,
                tensor_quality REAL NOT NULL,

                posterior_mean_r REAL NOT NULL,
                posterior_std_r REAL NOT NULL,
                posterior_lcb_r REAL NOT NULL,
                posterior_ucb_r REAL NOT NULL,

                prob_edge_gt_zero REAL NOT NULL,
                prob_edge_gt_min REAL NOT NULL,
                prob_loss_gt_025r REAL NOT NULL,
                prob_loss_gt_050r REAL NOT NULL,
                prob_tail_event REAL NOT NULL,

                sample_quality REAL NOT NULL,
                validation_quality REAL NOT NULL,
                context_quality REAL NOT NULL,
                decay_quality REAL NOT NULL,
                tail_quality REAL NOT NULL,
                fold_quality REAL NOT NULL,
                posterior_quality REAL NOT NULL,

                posterior_score_raw REAL NOT NULL,
                posterior_score REAL NOT NULL,

                posterior_state TEXT NOT NULL,
                allowed_meta_governance INTEGER NOT NULL,
                allowed_direct_open INTEGER NOT NULL,

                recommended_next_action TEXT NOT NULL,
                reasons TEXT NOT NULL,
                payload TEXT NOT NULL
            );
        """)

        self.db.execute("""
            CREATE INDEX IF NOT EXISTS idx_alpha_bayesian_posterior_v5_alpha
            ON alpha_bayesian_posterior_v5(alpha_key, id);
        """)

        self.db.execute("""
            CREATE INDEX IF NOT EXISTS idx_alpha_bayesian_posterior_v5_cluster
            ON alpha_bayesian_posterior_v5(cluster_key, id);
        """)

        self.db.execute("""
            CREATE TABLE IF NOT EXISTS alpha_bayesian_posterior_audit_v5 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                version TEXT NOT NULL,
                event TEXT NOT NULL,
                level TEXT NOT NULL,
                message TEXT NOT NULL,
                payload TEXT NOT NULL
            );
        """)

        self.db.execute("DROP VIEW IF EXISTS latest_alpha_bayesian_posterior_v5;")
        self.db.execute("""
            CREATE VIEW latest_alpha_bayesian_posterior_v5 AS
            SELECT p.*
            FROM alpha_bayesian_posterior_v5 p
            JOIN (
                SELECT alpha_key, MAX(id) AS max_id
                FROM alpha_bayesian_posterior_v5
                GROUP BY alpha_key
            ) x ON x.max_id = p.id;
        """)

    def audit(self, event: str, level: str, message: str, payload: Dict[str, Any]) -> None:
        self.db.execute("""
            INSERT INTO alpha_bayesian_posterior_audit_v5 (
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

    def tensor_rows(self) -> List[Dict[str, Any]]:
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
                expectancy_r,
                shrunk_expectancy_r,
                lcb_expectancy_r,
                validation_exp_r,
                recent_exp_r,
                older_exp_r,
                std_r,
                profit_factor_capped,
                tensor_quality,
                sample_quality,
                stability_quality,
                context_quality,
                fold_positive_n,
                fold_pass,
                decay_state,
                tail_risk_state,
                p05_r,
                p10_r,
                worst_r,
                avg_mae_r,
                path_quality,
                payload
            FROM latest_alpha_evidence_tensor_v5;
        """)

    def quality_components(self, r: Dict[str, Any]) -> Dict[str, float]:
        n = safe_int(r.get("n"))

        sample_q = clamp(math.sqrt(max(0, n) / 180.0), 0.0, 1.0)

        validation_r = safe_float(r.get("validation_exp_r"))
        validation_q = clamp((validation_r + 0.02) / 0.12, 0.0, 1.0)

        context_q = clamp(safe_float(r.get("current_context_fit")), 0.0, 1.0)

        decay_state = str(r.get("decay_state") or "UNKNOWN")
        decay_q = {
            "IMPROVING": 1.0,
            "STABLE": 0.82,
            "TOO_EARLY": 0.38,
            "DECAYING": 0.0,
        }.get(decay_state, 0.35)

        tail_state = str(r.get("tail_risk_state") or "UNKNOWN")
        tail_q = {
            "TAIL_RISK_OK": 1.0,
            "TAIL_RISK_MEDIUM": 0.42,
            "TAIL_RISK_HIGH": 0.0,
        }.get(tail_state, 0.35)

        fold_pos = safe_int(r.get("fold_positive_n"))
        fold_pass = safe_int(r.get("fold_pass"))
        fold_q = 1.0 if fold_pass == 1 else clamp(fold_pos / 4.0, 0.0, 0.72)

        posterior_q = (
            0.24 * sample_q
            + 0.18 * validation_q
            + 0.16 * context_q
            + 0.16 * decay_q
            + 0.16 * tail_q
            + 0.10 * fold_q
        )

        return {
            "sample_quality": round(sample_q, 8),
            "validation_quality": round(validation_q, 8),
            "context_quality": round(context_q, 8),
            "decay_quality": round(decay_q, 8),
            "tail_quality": round(tail_q, 8),
            "fold_quality": round(fold_q, 8),
            "posterior_quality": round(clamp(posterior_q, 0.0, 1.0), 8),
        }

    def effective_n(self, r: Dict[str, Any], q: Dict[str, float]) -> float:
        n = safe_float(r.get("n"))
        eff = n
        eff *= max(0.10, q["sample_quality"])
        eff *= max(0.15, q["validation_quality"])
        eff *= max(0.20, q["context_quality"])
        eff *= max(0.10, q["decay_quality"])
        eff *= max(0.10, q["tail_quality"])
        eff *= max(0.20, q["fold_quality"])
        return round(clamp(eff, 1.0, n if n > 0 else 1.0), 8)

    def prior_std(self, r: Dict[str, Any]) -> float:
        n = safe_int(r.get("n"))
        if n < 30:
            return 0.08
        if n < 90:
            return 0.10
        return 0.12

    def observation_mean(self, r: Dict[str, Any]) -> float:
        shrunk = safe_float(r.get("shrunk_expectancy_r"))
        validation = safe_float(r.get("validation_exp_r"))
        lcb = safe_float(r.get("lcb_expectancy_r"))
        recent = safe_float(r.get("recent_exp_r"))
        older = safe_float(r.get("older_exp_r"))

        trend_confirm = 0.0
        if recent > older:
            trend_confirm = min(0.02, recent - older)
        elif recent < older:
            trend_confirm = max(-0.03, recent - older)

        return (
            0.48 * shrunk
            + 0.26 * validation
            + 0.18 * lcb
            + 0.08 * recent
            + trend_confirm
        )

    def posterior_for_row(self, r: Dict[str, Any]) -> Dict[str, Any]:
        q = self.quality_components(r)
        n = safe_int(r.get("n"))
        eff_n = self.effective_n(r, q)

        obs_mean = self.observation_mean(r)

        observed_std = max(
            safe_float(r.get("std_r")),
            0.22 if n < 30 else 0.16 if n < 90 else 0.11,
        )
        obs_se = observed_std / math.sqrt(max(eff_n, 1.0))

        prior_mean = 0.0
        prior_std = self.prior_std(r)
        prior_var = prior_std ** 2
        obs_var = max(obs_se ** 2, 1e-8)

        post_var = 1.0 / ((1.0 / prior_var) + (1.0 / obs_var))
        post_mean = post_var * ((prior_mean / prior_var) + (obs_mean / obs_var))
        post_std = math.sqrt(post_var)

        post_lcb = post_mean - 1.65 * post_std
        post_ucb = post_mean + 1.65 * post_std

        p_edge_zero = prob_gt(0.0, post_mean, post_std)
        p_edge_min = prob_gt(EDGE_MIN_R, post_mean, post_std)
        p_loss_025 = prob_lt(-0.25, post_mean, post_std)
        p_loss_050 = prob_lt(-0.50, post_mean, post_std)

        tail_state = str(r.get("tail_risk_state") or "UNKNOWN")
        p05 = safe_float(r.get("p05_r"))
        p10 = safe_float(r.get("p10_r"))
        worst = safe_float(r.get("worst_r"))
        avg_mae = safe_float(r.get("avg_mae_r"))

        tail_base = {
            "TAIL_RISK_OK": 0.08,
            "TAIL_RISK_MEDIUM": 0.26,
            "TAIL_RISK_HIGH": 0.58,
        }.get(tail_state, 0.30)

        tail_penalty = 0.0
        if p05 <= -0.75:
            tail_penalty += 0.10
        if p10 <= -0.50:
            tail_penalty += 0.08
        if worst <= -1.25:
            tail_penalty += 0.10
        if avg_mae <= -0.75:
            tail_penalty += 0.08

        p_tail = clamp(tail_base + tail_penalty + p_loss_050 * 0.25, 0.0, 0.95)

        tensor_quality = safe_float(r.get("tensor_quality"))
        tensor_q = clamp(tensor_quality / 100.0, 0.0, 1.0)

        mean_q = clamp(post_mean / 0.08, 0.0, 1.0)
        lcb_q = clamp((post_lcb + 0.02) / 0.08, 0.0, 1.0)
        edge_prob_q = clamp(p_edge_min, 0.0, 1.0)
        loss_q = 1.0 - clamp(p_loss_025 / 0.35, 0.0, 1.0)
        tail_q = 1.0 - p_tail

        raw_score = 100.0 * (
            0.24 * edge_prob_q
            + 0.18 * mean_q
            + 0.16 * lcb_q
            + 0.14 * q["posterior_quality"]
            + 0.12 * tensor_q
            + 0.08 * loss_q
            + 0.08 * tail_q
        )

        score = raw_score

        caps = [100.0]
        if n < 10:
            caps.append(18.0)
        elif n < 30:
            caps.append(32.0)
        elif n < 60:
            caps.append(50.0)
        elif n < 90:
            caps.append(68.0)
        elif n < 150:
            caps.append(84.0)

        if post_lcb <= 0:
            caps.append(70.0)
        if safe_float(r.get("validation_exp_r")) <= 0:
            caps.append(62.0)
        if tail_state == "TAIL_RISK_HIGH":
            caps.append(42.0)
        elif tail_state == "TAIL_RISK_MEDIUM":
            caps.append(72.0)
        if str(r.get("decay_state")) == "DECAYING":
            caps.append(45.0)
        if safe_float(r.get("current_context_fit")) < MIN_CONTEXT_FIT:
            caps.append(68.0)
        if safe_int(r.get("fold_pass")) != 1:
            caps.append(76.0)

        score = round(min(score, min(caps)), 8)

        reasons: List[str] = []

        if n < 30:
            state = "POSTERIOR_DISCOVERY"
            reasons.append("SAMPLE_TOO_SMALL_FOR_POSTERIOR_ACTION")
        elif safe_float(r.get("shrunk_expectancy_r")) <= 0:
            state = "POSTERIOR_REJECTED_NO_EDGE"
            reasons.append("SHRUNK_EXPECTANCY_NOT_POSITIVE")
        elif tail_state == "TAIL_RISK_HIGH":
            state = "POSTERIOR_REJECTED_TAIL_RISK"
            reasons.append("TAIL_RISK_HIGH")
        elif str(r.get("decay_state")) == "DECAYING":
            state = "POSTERIOR_REJECTED_DECAYING"
            reasons.append("ALPHA_DECAYING")
        elif post_mean > 0 and p_edge_zero >= 0.55:
            state = "POSTERIOR_RESEARCH_READY"
            reasons.append("POSTERIOR_EDGE_RESEARCH_READY")
        else:
            state = "POSTERIOR_WATCHLIST"
            reasons.append("ACCUMULATE_MORE_EVIDENCE")

        missing = []
        if n < MIN_META_N:
            missing.append(f"n>={MIN_META_N}")
        if post_mean < MIN_POSTERIOR_MEAN_R:
            missing.append(f"posterior_mean_r>={MIN_POSTERIOR_MEAN_R}")
        if post_lcb <= MIN_POSTERIOR_LCB_R:
            missing.append("posterior_lcb_r>0")
        if p_edge_min < MIN_PROB_EDGE_GT_MIN:
            missing.append(f"prob_edge_gt_{EDGE_MIN_R}R>={MIN_PROB_EDGE_GT_MIN}")
        if p_edge_zero < MIN_PROB_EDGE_GT_ZERO:
            missing.append(f"prob_edge_gt_0>={MIN_PROB_EDGE_GT_ZERO}")
        if p_loss_025 > MAX_PROB_LOSS_GT_025:
            missing.append(f"prob_loss_gt_0.25R<={MAX_PROB_LOSS_GT_025}")
        if tail_state != "TAIL_RISK_OK":
            missing.append("tail_risk_ok")
        if str(r.get("decay_state")) == "DECAYING":
            missing.append("not_decaying")
        if safe_float(r.get("current_context_fit")) < MIN_CONTEXT_FIT:
            missing.append(f"context_fit>={MIN_CONTEXT_FIT}")
        if safe_float(r.get("tensor_quality")) < MIN_TENSOR_QUALITY:
            missing.append(f"tensor_quality>={MIN_TENSOR_QUALITY}")
        if score < MIN_POSTERIOR_SCORE:
            missing.append(f"posterior_score>={MIN_POSTERIOR_SCORE}")
        if safe_float(r.get("validation_exp_r")) <= 0:
            missing.append("validation_exp_r>0")
        if safe_int(r.get("fold_pass")) != 1:
            missing.append("fold_pass=1")

        allowed_meta = (
            n >= MIN_META_N
            and post_mean >= MIN_POSTERIOR_MEAN_R
            and post_lcb > MIN_POSTERIOR_LCB_R
            and p_edge_min >= MIN_PROB_EDGE_GT_MIN
            and p_edge_zero >= MIN_PROB_EDGE_GT_ZERO
            and p_loss_025 <= MAX_PROB_LOSS_GT_025
            and tail_state == "TAIL_RISK_OK"
            and str(r.get("decay_state")) != "DECAYING"
            and safe_float(r.get("current_context_fit")) >= MIN_CONTEXT_FIT
            and safe_float(r.get("tensor_quality")) >= MIN_TENSOR_QUALITY
            and score >= MIN_POSTERIOR_SCORE
            and safe_float(r.get("validation_exp_r")) > 0
            and safe_int(r.get("fold_pass")) == 1
        )

        if allowed_meta:
            state = "POSTERIOR_META_GOVERNANCE_READY"
            reasons.append("POSTERIOR_READY_FOR_META_GOVERNANCE")
            next_action = "META_GOVERNANCE_V5_REVIEW"
        else:
            next_action = "NEEDS_" + "_".join(missing[:4]) if missing else "WATCHLIST"

        payload = {
            "source": VERSION,
            "edge_min_r": EDGE_MIN_R,
            "observation_mean_r": round(obs_mean, 8),
            "observed_std_r": round(observed_std, 8),
            "obs_se": round(obs_se, 8),
            "prior_mean_r": prior_mean,
            "prior_std_r": prior_std,
            "quality_caps": caps,
            "missing_requirements": missing,
            "no_execution": True,
            "allowed_direct_open": False,
            "tensor_payload": r.get("payload"),
        }

        return {
            "ts": utc_now_iso(),
            "version": VERSION,

            "alpha_key": r["alpha_key"],
            "cluster_key": r["cluster_key"],

            "symbol": r["symbol"],
            "side": r["side"],
            "setup": r["setup"],
            "profile": r["profile"],
            "horizon_min": safe_int(r["horizon_min"]),

            "learned_context_bucket": r["learned_context_bucket"],
            "current_context_bucket": r["current_context_bucket"],
            "current_context_fit": round(safe_float(r["current_context_fit"]), 8),

            "n": n,
            "effective_n": eff_n,

            "tensor_expectancy_r": round(safe_float(r["expectancy_r"]), 8),
            "tensor_shrunk_r": round(safe_float(r["shrunk_expectancy_r"]), 8),
            "tensor_lcb_r": round(safe_float(r["lcb_expectancy_r"]), 8),
            "tensor_validation_r": round(safe_float(r["validation_exp_r"]), 8),
            "tensor_recent_r": round(safe_float(r["recent_exp_r"]), 8),
            "tensor_older_r": round(safe_float(r["older_exp_r"]), 8),

            "tensor_std_r": round(safe_float(r["std_r"]), 8),
            "tensor_pf_cap": round(safe_float(r["profit_factor_capped"]), 8),
            "tensor_quality": round(tensor_quality, 8),

            "posterior_mean_r": round(post_mean, 8),
            "posterior_std_r": round(post_std, 8),
            "posterior_lcb_r": round(post_lcb, 8),
            "posterior_ucb_r": round(post_ucb, 8),

            "prob_edge_gt_zero": round(p_edge_zero, 8),
            "prob_edge_gt_min": round(p_edge_min, 8),
            "prob_loss_gt_025r": round(p_loss_025, 8),
            "prob_loss_gt_050r": round(p_loss_050, 8),
            "prob_tail_event": round(p_tail, 8),

            "sample_quality": q["sample_quality"],
            "validation_quality": q["validation_quality"],
            "context_quality": q["context_quality"],
            "decay_quality": q["decay_quality"],
            "tail_quality": q["tail_quality"],
            "fold_quality": q["fold_quality"],
            "posterior_quality": q["posterior_quality"],

            "posterior_score_raw": round(raw_score, 8),
            "posterior_score": score,

            "posterior_state": state,
            "allowed_meta_governance": 1 if allowed_meta else 0,
            "allowed_direct_open": 0,

            "recommended_next_action": next_action,
            "reasons": json.dumps(reasons, separators=(",", ":"), ensure_ascii=False),
            "payload": json.dumps(payload, separators=(",", ":"), ensure_ascii=False),
        }

    def insert_row(self, row: Dict[str, Any]) -> None:
        cols = [
            "ts", "version",
            "alpha_key", "cluster_key",
            "symbol", "side", "setup", "profile", "horizon_min",
            "learned_context_bucket", "current_context_bucket", "current_context_fit",
            "n", "effective_n",
            "tensor_expectancy_r", "tensor_shrunk_r", "tensor_lcb_r",
            "tensor_validation_r", "tensor_recent_r", "tensor_older_r",
            "tensor_std_r", "tensor_pf_cap", "tensor_quality",
            "posterior_mean_r", "posterior_std_r", "posterior_lcb_r", "posterior_ucb_r",
            "prob_edge_gt_zero", "prob_edge_gt_min",
            "prob_loss_gt_025r", "prob_loss_gt_050r", "prob_tail_event",
            "sample_quality", "validation_quality", "context_quality",
            "decay_quality", "tail_quality", "fold_quality", "posterior_quality",
            "posterior_score_raw", "posterior_score",
            "posterior_state", "allowed_meta_governance", "allowed_direct_open",
            "recommended_next_action", "reasons", "payload",
        ]

        q = ",".join(["?"] * len(cols))
        self.db.execute(
            f"INSERT INTO alpha_bayesian_posterior_v5 ({','.join(cols)}) VALUES ({q});",
            tuple(row[c] for c in cols),
        )

    def retention(self) -> None:
        self.db.execute("""
            DELETE FROM alpha_bayesian_posterior_v5
            WHERE id NOT IN (
                SELECT id FROM alpha_bayesian_posterior_v5
                ORDER BY id DESC
                LIMIT ?
            );
        """, (MAX_POSTERIOR_ROWS,))

        self.db.execute("""
            DELETE FROM alpha_bayesian_posterior_audit_v5
            WHERE id NOT IN (
                SELECT id FROM alpha_bayesian_posterior_audit_v5
                ORDER BY id DESC
                LIMIT ?
            );
        """, (MAX_AUDIT_ROWS,))

    def refresh(self) -> Dict[str, Any]:
        self.ensure_schema()

        rows = self.tensor_rows()
        posterior_rows = [self.posterior_for_row(r) for r in rows]

        for row in posterior_rows:
            self.insert_row(row)

        self.retention()

        states: Dict[str, int] = {}
        for r in posterior_rows:
            s = r["posterior_state"]
            states[s] = states.get(s, 0) + 1

        result = {
            "version": VERSION,
            "input_tensor_rows": len(rows),
            "posterior_rows_created": len(posterior_rows),
            "states": states,
            "meta_governance_ready": states.get("POSTERIOR_META_GOVERNANCE_READY", 0),
            "no_execution": True,
        }

        self.audit("REFRESH", "INFO", "Bayesian Alpha Posterior V5 refreshed", result)
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
                ROUND(tensor_shrunk_r,4) AS tensor_shrunk,
                ROUND(tensor_lcb_r,4) AS tensor_lcb,
                ROUND(tensor_validation_r,4) AS val_r,
                ROUND(posterior_mean_r,4) AS post_mean,
                ROUND(posterior_lcb_r,4) AS post_lcb,
                ROUND(prob_edge_gt_zero,3) AS p_gt_0,
                ROUND(prob_edge_gt_min,3) AS p_gt_min,
                ROUND(prob_loss_gt_025r,3) AS p_loss_025,
                ROUND(prob_tail_event,3) AS p_tail,
                ROUND(tensor_quality,2) AS tensor_q,
                ROUND(posterior_score,2) AS post_score,
                posterior_state,
                allowed_meta_governance,
                recommended_next_action
            FROM latest_alpha_bayesian_posterior_v5
            ORDER BY allowed_meta_governance DESC, posterior_score DESC, posterior_mean_r DESC, n DESC
            LIMIT ?;
        """, (limit,))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--latest", action="store_true")
    args = parser.parse_args()

    engine = AlphaBayesianPosteriorV5()

    if args.refresh:
        print(json.dumps(engine.refresh(), indent=2, sort_keys=True))

    if args.latest:
        for r in engine.latest():
            print(r)


if __name__ == "__main__":
    main()
