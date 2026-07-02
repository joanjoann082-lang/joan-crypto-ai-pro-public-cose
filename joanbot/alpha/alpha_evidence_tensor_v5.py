from __future__ import annotations

import argparse
import json
import math
from statistics import median
from typing import Any, Dict, List, Tuple

from joanbot.alpha.alpha_feature_store_v1 import AlphaFeatureStoreV1
from joanbot.alpha.alpha_label_store_v1 import AlphaLabelStoreV1
from joanbot.storage.db import get_db
from joanbot.utils import utc_now_iso, fnum

VERSION = "ALPHA_EVIDENCE_TENSOR_V5_INSTITUTIONAL"

MAX_TENSOR_ROWS = 2400
MAX_AUDIT_ROWS = 300


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(x)))


def mean(xs: List[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def std(xs: List[float]) -> float:
    if len(xs) < 2:
        return 0.0
    m = mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def percentile(xs: List[float], q: float) -> float:
    if not xs:
        return 0.0
    ys = sorted(xs)
    if len(ys) == 1:
        return ys[0]
    pos = (len(ys) - 1) * q
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return ys[lo]
    w = pos - lo
    return ys[lo] * (1.0 - w) + ys[hi] * w


def safe_div(a: float, b: float, default: float = 0.0) -> float:
    if abs(b) < 1e-12:
        return default
    return a / b


def safe_json(x: Any) -> Dict[str, Any]:
    if isinstance(x, dict):
        return x
    if not x:
        return {}
    try:
        y = json.loads(str(x))
        return y if isinstance(y, dict) else {}
    except Exception:
        return {}


class AlphaEvidenceTensorV5:
    """
    Institutional evidence tensor for alpha intelligence.

    Reads:
    - AlphaLabelStoreV1
    - AlphaFeatureStoreV1
    - universal_shadow_cases_v2 / results_v2 through label store

    Writes:
    - alpha_evidence_tensor_v5
    - latest_alpha_evidence_tensor_v5
    - alpha_evidence_tensor_audit_v5

    Does not mutate trading path or execution tables.
    """

    def __init__(self, db=None):
        self.db = db or get_db()
        self.labels = AlphaLabelStoreV1(self.db)
        self.features = AlphaFeatureStoreV1(self.db)

    def ensure_schema(self) -> None:
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS alpha_evidence_tensor_v5 (
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
                n_recent INTEGER NOT NULL,
                n_older INTEGER NOT NULL,

                mean_r REAL NOT NULL,
                median_r REAL NOT NULL,
                std_r REAL NOT NULL,
                winrate REAL NOT NULL,
                profit_factor REAL NOT NULL,
                profit_factor_capped REAL NOT NULL,

                expectancy_r REAL NOT NULL,
                shrunk_expectancy_r REAL NOT NULL,
                lcb_expectancy_r REAL NOT NULL,

                train_exp_r REAL NOT NULL,
                validation_exp_r REAL NOT NULL,
                recent_exp_r REAL NOT NULL,
                older_exp_r REAL NOT NULL,

                p05_r REAL NOT NULL,
                p10_r REAL NOT NULL,
                worst_r REAL NOT NULL,
                best_r REAL NOT NULL,

                avg_mfe_r REAL NOT NULL,
                avg_mae_r REAL NOT NULL,
                mfe_mae_efficiency REAL NOT NULL,

                fold_1_r REAL NOT NULL,
                fold_2_r REAL NOT NULL,
                fold_3_r REAL NOT NULL,
                fold_4_r REAL NOT NULL,
                fold_positive_n INTEGER NOT NULL,
                fold_min_r REAL NOT NULL,
                fold_pass INTEGER NOT NULL,

                decay_slope REAL NOT NULL,
                decay_state TEXT NOT NULL,
                tail_risk_state TEXT NOT NULL,

                sample_quality REAL NOT NULL,
                path_quality REAL NOT NULL,
                stability_quality REAL NOT NULL,
                context_quality REAL NOT NULL,
                tensor_quality REAL NOT NULL,

                raw_cases_json TEXT NOT NULL,
                payload TEXT NOT NULL
            );
        """)

        self.db.execute("""
            CREATE INDEX IF NOT EXISTS idx_alpha_evidence_tensor_v5_alpha
            ON alpha_evidence_tensor_v5(alpha_key, id);
        """)

        self.db.execute("""
            CREATE INDEX IF NOT EXISTS idx_alpha_evidence_tensor_v5_cluster
            ON alpha_evidence_tensor_v5(cluster_key, id);
        """)

        self.db.execute("""
            CREATE TABLE IF NOT EXISTS alpha_evidence_tensor_audit_v5 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                version TEXT NOT NULL,
                event TEXT NOT NULL,
                level TEXT NOT NULL,
                message TEXT NOT NULL,
                payload TEXT NOT NULL
            );
        """)

        self.db.execute("DROP VIEW IF EXISTS latest_alpha_evidence_tensor_v5;")
        self.db.execute("""
            CREATE VIEW latest_alpha_evidence_tensor_v5 AS
            SELECT t.*
            FROM alpha_evidence_tensor_v5 t
            JOIN (
                SELECT alpha_key, MAX(id) AS max_id
                FROM alpha_evidence_tensor_v5
                GROUP BY alpha_key
            ) x ON x.max_id = t.id;
        """)

    def audit(self, event: str, level: str, message: str, payload: Dict[str, Any]) -> None:
        self.db.execute("""
            INSERT INTO alpha_evidence_tensor_audit_v5 (
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

    def pf_cap(self, n: int) -> float:
        if n < 10:
            return 1.5
        if n < 30:
            return 2.0
        if n < 60:
            return 2.75
        if n < 90:
            return 3.5
        if n < 150:
            return 5.0
        return 8.0

    def folds(self, vals: List[float]) -> Tuple[List[float], int, float, int]:
        n = len(vals)
        out = []

        for i in range(4):
            a = int(i * n / 4)
            b = int((i + 1) * n / 4)
            chunk = vals[a:b]
            out.append(mean(chunk) if chunk else 0.0)

        pos = sum(1 for x in out if x > 0)
        mn = min(out) if out else 0.0
        fold_pass = 1 if n >= 32 and pos >= 3 and mn > -0.05 and out[-1] > 0 else 0

        return out, pos, mn, fold_pass

    def decay(self, vals: List[float]) -> Tuple[float, str, float, float]:
        n = len(vals)
        if n < 20:
            return 0.0, "TOO_EARLY", 0.0, mean(vals)

        cut = max(1, int(n * 0.70))
        older = vals[:cut]
        recent = vals[cut:]

        older_r = mean(older)
        recent_r = mean(recent)
        slope = recent_r - older_r

        if recent_r <= -0.02 and older_r > 0:
            return slope, "DECAYING", older_r, recent_r
        if slope < -0.12:
            return slope, "DECAYING", older_r, recent_r
        if slope > 0.05:
            return slope, "IMPROVING", older_r, recent_r
        return slope, "STABLE", older_r, recent_r

    def tail_state(self, p10: float, p05: float, worst: float, avg_mae: float) -> str:
        if p05 <= -1.0 or p10 <= -0.80 or worst <= -1.50 or avg_mae <= -1.25:
            return "TAIL_RISK_HIGH"
        if p05 <= -0.65 or p10 <= -0.45 or worst <= -1.00 or avg_mae <= -0.75:
            return "TAIL_RISK_MEDIUM"
        return "TAIL_RISK_OK"

    def lcb(self, mean_r: float, std_r: float, n: int) -> float:
        if n <= 1:
            return mean_r - 1.0
        return mean_r - 1.65 * (std_r / math.sqrt(n))

    def sample_quality(self, n: int) -> float:
        return clamp(math.sqrt(max(0, n) / 180.0), 0.0, 1.0)

    def tensor_for_group(self, item: Dict[str, Any], current_buckets: Dict[str, str]) -> Dict[str, Any]:
        identity = item["identity"]
        rows = item["rows"]

        vals = [fnum(r.get("result_r"), 0.0) for r in rows]
        mfes = [fnum(r.get("mfe_r"), 0.0) for r in rows]
        maes = [fnum(r.get("mae_r"), 0.0) for r in rows]

        n = len(vals)
        wins = [x for x in vals if x > 0]
        losses = [x for x in vals if x < 0]

        gross_win = sum(wins)
        gross_loss = abs(sum(losses))
        pf = 99.0 if gross_loss <= 0 and gross_win > 0 else safe_div(gross_win, gross_loss, 0.0)
        pf_capped = min(pf, self.pf_cap(n))

        avg = mean(vals)
        med = median(vals) if vals else 0.0
        sd = std(vals)

        half = max(1, n // 2)
        train_r = mean(vals[:half])
        val_r = mean(vals[half:]) if vals[half:] else 0.0

        slope, decay_state, older_r, recent_r = self.decay(vals)
        recent_n = len(vals[max(1, int(n * 0.70)):]) if n else 0
        older_n = max(0, n - recent_n)

        p05 = percentile(vals, 0.05)
        p10 = percentile(vals, 0.10)
        worst = min(vals) if vals else 0.0
        best = max(vals) if vals else 0.0

        avg_mfe = mean(mfes)
        avg_mae = mean(maes)
        efficiency = safe_div(avg_mfe, max(abs(avg_mae), 0.05), 0.0)

        fold_vals, fold_pos, fold_min, fold_pass = self.folds(vals)

        prior_n = 140.0 if n < 90 else 90.0
        shrunk = avg * (n / (n + prior_n))
        lcb_r = self.lcb(avg, sd, n)

        current_bucket = current_buckets.get(identity.symbol, "UNKNOWN")
        context_fit = self.features.context_fit(identity.context_bucket, current_bucket)

        tail = self.tail_state(p10, p05, worst, avg_mae)

        sq = self.sample_quality(n)
        path_q = clamp(efficiency / 3.0, 0.0, 1.0)
        stability_q = clamp(1.0 - abs(train_r - val_r) / max(abs(avg), 0.05), 0.0, 1.0)
        context_q = clamp(context_fit, 0.0, 1.0)

        tail_q = 1.0 if tail == "TAIL_RISK_OK" else 0.45 if tail == "TAIL_RISK_MEDIUM" else 0.0
        decay_q = 1.0 if decay_state == "IMPROVING" else 0.80 if decay_state == "STABLE" else 0.45 if decay_state == "TOO_EARLY" else 0.0
        pf_q = clamp((pf_capped - 1.0) / 2.0, 0.0, 1.0)
        exp_q = clamp(shrunk / 0.08, 0.0, 1.0)
        val_q = clamp(val_r / 0.08, 0.0, 1.0)
        fold_q = 1.0 if fold_pass else clamp(fold_pos / 4.0, 0.0, 0.75)

        tensor_quality = 100.0 * (
            0.16 * sq
            + 0.18 * exp_q
            + 0.14 * val_q
            + 0.10 * pf_q
            + 0.12 * fold_q
            + 0.10 * decay_q
            + 0.10 * tail_q
            + 0.06 * path_q
            + 0.06 * stability_q
            + 0.08 * context_q
        )

        # Institutional quality caps.
        # Raw tensor score is useful for ranking, but final tensor_quality must not
        # overstate small-sample, unstable, tail-heavy, or validation-negative alphas.
        raw_tensor_quality = tensor_quality

        sample_cap = 100.0
        if n < 10:
            sample_cap = 28.0
        elif n < 30:
            sample_cap = 42.0
        elif n < 60:
            sample_cap = 58.0
        elif n < 90:
            sample_cap = 70.0
        elif n < 150:
            sample_cap = 84.0

        structural_caps = [sample_cap]

        if tail == "TAIL_RISK_HIGH":
            structural_caps.append(52.0)
        elif tail == "TAIL_RISK_MEDIUM":
            structural_caps.append(74.0)

        if decay_state == "DECAYING":
            structural_caps.append(45.0)
        elif decay_state == "TOO_EARLY":
            structural_caps.append(57.0)

        if fold_pass == 0:
            structural_caps.append(72.0)

        if val_r <= 0:
            structural_caps.append(60.0)

        if lcb_r <= 0:
            structural_caps.append(68.0)

        if context_fit < 0.45:
            structural_caps.append(70.0)

        tensor_quality = min(tensor_quality, min(structural_caps))

        raw_case_refs = [
            {
                "case_id": r.get("case_id"),
                "created_at": r.get("created_at"),
                "result_r": r.get("result_r"),
                "mfe_r": r.get("mfe_r"),
                "mae_r": r.get("mae_r"),
                "outcome": r.get("outcome"),
            }
            for r in rows[-30:]
        ]

        return {
            "alpha_key": identity.key(),
            "cluster_key": identity.cluster_key(),
            "symbol": identity.symbol,
            "side": identity.side,
            "setup": identity.setup,
            "profile": identity.profile,
            "horizon_min": int(identity.horizon_min),
            "learned_context_bucket": identity.context_bucket,
            "current_context_bucket": current_bucket,
            "current_context_fit": round(context_fit, 8),

            "n": n,
            "n_recent": recent_n,
            "n_older": older_n,

            "mean_r": round(avg, 8),
            "median_r": round(med, 8),
            "std_r": round(sd, 8),
            "winrate": round(safe_div(len(wins), n, 0.0), 8),
            "profit_factor": round(pf, 8),
            "profit_factor_capped": round(pf_capped, 8),

            "expectancy_r": round(avg, 8),
            "shrunk_expectancy_r": round(shrunk, 8),
            "lcb_expectancy_r": round(lcb_r, 8),

            "train_exp_r": round(train_r, 8),
            "validation_exp_r": round(val_r, 8),
            "recent_exp_r": round(recent_r, 8),
            "older_exp_r": round(older_r, 8),

            "p05_r": round(p05, 8),
            "p10_r": round(p10, 8),
            "worst_r": round(worst, 8),
            "best_r": round(best, 8),

            "avg_mfe_r": round(avg_mfe, 8),
            "avg_mae_r": round(avg_mae, 8),
            "mfe_mae_efficiency": round(efficiency, 8),

            "fold_1_r": round(fold_vals[0], 8),
            "fold_2_r": round(fold_vals[1], 8),
            "fold_3_r": round(fold_vals[2], 8),
            "fold_4_r": round(fold_vals[3], 8),
            "fold_positive_n": fold_pos,
            "fold_min_r": round(fold_min, 8),
            "fold_pass": fold_pass,

            "decay_slope": round(slope, 8),
            "decay_state": decay_state,
            "tail_risk_state": tail,

            "sample_quality": round(sq, 8),
            "path_quality": round(path_q, 8),
            "stability_quality": round(stability_q, 8),
            "context_quality": round(context_q, 8),
            "tensor_quality": round(tensor_quality, 8),

            "raw_cases_json": json.dumps(raw_case_refs, separators=(",", ":"), ensure_ascii=False),
            "payload": json.dumps({
                "version": VERSION,
                "gross_win": gross_win,
                "gross_loss": gross_loss,
                "fold_values": fold_vals,
                "no_execution": True,
                "source": "AlphaLabelStoreV1+AlphaFeatureStoreV1",
            }, separators=(",", ":"), ensure_ascii=False),
        }

    def insert_tensor(self, t: Dict[str, Any]) -> None:
        cols = [
            "ts", "version",
            "alpha_key", "cluster_key",
            "symbol", "side", "setup", "profile", "horizon_min",
            "learned_context_bucket", "current_context_bucket", "current_context_fit",
            "n", "n_recent", "n_older",
            "mean_r", "median_r", "std_r", "winrate", "profit_factor", "profit_factor_capped",
            "expectancy_r", "shrunk_expectancy_r", "lcb_expectancy_r",
            "train_exp_r", "validation_exp_r", "recent_exp_r", "older_exp_r",
            "p05_r", "p10_r", "worst_r", "best_r",
            "avg_mfe_r", "avg_mae_r", "mfe_mae_efficiency",
            "fold_1_r", "fold_2_r", "fold_3_r", "fold_4_r",
            "fold_positive_n", "fold_min_r", "fold_pass",
            "decay_slope", "decay_state", "tail_risk_state",
            "sample_quality", "path_quality", "stability_quality", "context_quality", "tensor_quality",
            "raw_cases_json", "payload",
        ]

        row = dict(t)
        row["ts"] = utc_now_iso()
        row["version"] = VERSION

        q = ",".join(["?"] * len(cols))
        self.db.execute(
            f"INSERT INTO alpha_evidence_tensor_v5 ({','.join(cols)}) VALUES ({q});",
            tuple(row[c] for c in cols),
        )

    def retention(self) -> None:
        self.db.execute("""
            DELETE FROM alpha_evidence_tensor_v5
            WHERE id NOT IN (
                SELECT id FROM alpha_evidence_tensor_v5 ORDER BY id DESC LIMIT ?
            );
        """, (MAX_TENSOR_ROWS,))

        self.db.execute("""
            DELETE FROM alpha_evidence_tensor_audit_v5
            WHERE id NOT IN (
                SELECT id FROM alpha_evidence_tensor_audit_v5 ORDER BY id DESC LIMIT ?
            );
        """, (MAX_AUDIT_ROWS,))

    def refresh(self) -> Dict[str, Any]:
        self.ensure_schema()

        current_buckets = self.features.current_buckets()
        evidence = self.labels.all_evidence()

        tensors = []
        for item in evidence:
            tensors.append(self.tensor_for_group(item, current_buckets))

        for t in tensors:
            self.insert_tensor(t)

        self.retention()

        result = {
            "version": VERSION,
            "evidence_groups": len(evidence),
            "tensor_rows_created": len(tensors),
            "current_buckets": current_buckets,
            "no_execution": True,
        }

        self.audit("REFRESH", "INFO", "Alpha evidence tensor V5 refreshed", result)
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
                learned_context_bucket,
                current_context_bucket,
                ROUND(current_context_fit,2) AS ctx_fit,
                n,
                ROUND(expectancy_r,4) AS exp_r,
                ROUND(shrunk_expectancy_r,4) AS shrunk_r,
                ROUND(lcb_expectancy_r,4) AS lcb_r,
                ROUND(validation_exp_r,4) AS val_r,
                ROUND(profit_factor_capped,3) AS pf_cap,
                ROUND(avg_mae_r,4) AS mae_r,
                ROUND(mfe_mae_efficiency,3) AS efficiency,
                fold_positive_n,
                fold_pass,
                decay_state,
                tail_risk_state,
                ROUND(tensor_quality,2) AS quality
            FROM latest_alpha_evidence_tensor_v5
            ORDER BY tensor_quality DESC, shrunk_expectancy_r DESC, n DESC
            LIMIT ?;
        """, (limit,))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--latest", action="store_true")
    args = parser.parse_args()

    engine = AlphaEvidenceTensorV5()

    if args.refresh:
        print(json.dumps(engine.refresh(), indent=2, sort_keys=True))

    if args.latest:
        for r in engine.latest():
            print(r)


if __name__ == "__main__":
    main()
