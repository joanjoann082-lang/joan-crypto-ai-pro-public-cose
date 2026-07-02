from __future__ import annotations

import math
from statistics import median
from typing import Any, Dict, List

from joanbot.alpha.contracts import AlphaEvidence, AlphaIdentity
from joanbot.storage.db import get_db
from joanbot.utils import fnum

VERSION = "ALPHA_LABEL_STORE_V1_INSTITUTIONAL"


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
    return ys[lo] * (1 - w) + ys[hi] * w


def safe_div(a: float, b: float, default: float = 0.0) -> float:
    return default if abs(b) < 1e-12 else a / b


def pf_cap(n: int) -> float:
    if n < 10:
        return 1.50
    if n < 30:
        return 2.00
    if n < 60:
        return 2.75
    if n < 90:
        return 3.50
    if n < 150:
        return 5.00
    return 8.00


class AlphaLabelStoreV1:
    """
    Converts resolved universal shadow cases into institutional AlphaEvidence.
    Read-only. No trading mutation.
    """

    def __init__(self, db=None):
        self.db = db or get_db()

    def resolved_rows(self) -> List[Dict[str, Any]]:
        return self.db.query("""
            SELECT
                c.id AS case_id,
                c.created_at,
                c.symbol,
                c.side,
                c.setup,
                c.profile,
                c.horizon_min,
                c.context_bucket,
                c.context_score,
                c.thesis,
                c.counter_thesis,
                c.invalidation,
                r.outcome,
                r.result_r,
                r.mfe_r,
                r.mae_r,
                r.bars_seen,
                r.exit_price
            FROM universal_shadow_results_v2 r
            JOIN universal_shadow_cases_v2 c ON c.id = r.case_id
            WHERE r.result_r IS NOT NULL
            ORDER BY c.created_at ASC;
        """)

    def identity_from_row(self, r: Dict[str, Any]) -> AlphaIdentity:
        return AlphaIdentity(
            symbol=str(r.get("symbol") or "").upper(),
            side=str(r.get("side") or "").upper(),
            setup=str(r.get("setup") or "").upper(),
            profile=str(r.get("profile") or "").upper(),
            horizon_min=int(float(r.get("horizon_min") or 0)),
            context_bucket=str(r.get("context_bucket") or "").upper(),
        )

    def grouped(self) -> Dict[str, List[Dict[str, Any]]]:
        out: Dict[str, List[Dict[str, Any]]] = {}
        for r in self.resolved_rows():
            ident = self.identity_from_row(r)
            out.setdefault(ident.key(), []).append(r)
        return out

    def build_evidence(self, rows: List[Dict[str, Any]]) -> AlphaEvidence:
        vals = [fnum(r.get("result_r"), 0.0) for r in rows]
        mfes = [fnum(r.get("mfe_r"), 0.0) for r in rows]
        maes = [fnum(r.get("mae_r"), 0.0) for r in rows]

        n = len(vals)
        wins = [x for x in vals if x > 0]
        losses = [x for x in vals if x < 0]

        gross_win = sum(wins)
        gross_loss = abs(sum(losses))
        pf = 99.0 if gross_loss <= 0 and gross_win > 0 else safe_div(gross_win, gross_loss, 0.0)

        avg = mean(vals)
        half = max(1, n // 2)
        train = mean(vals[:half])
        validation = mean(vals[half:]) if vals[half:] else 0.0

        cut = max(1, int(n * 0.70))
        older = mean(vals[:cut]) if vals[:cut] else 0.0
        recent = mean(vals[cut:]) if vals[cut:] else avg

        fold_values = []
        fold_positive = 0
        for i in range(4):
            a = int(i * n / 4)
            b = int((i + 1) * n / 4)
            chunk = vals[a:b]
            if chunk:
                fr = mean(chunk)
                fold_values.append(fr)
                if fr > 0:
                    fold_positive += 1

        fold_pass = (
            n >= 32
            and fold_positive >= 3
            and min(fold_values or [0]) > -0.05
            and (fold_values[-1] if fold_values else 0) > 0
        )

        if n < 20:
            decay = "TOO_EARLY"
        elif recent <= -0.02 and older > 0:
            decay = "DECAYING"
        elif recent < older - 0.12:
            decay = "DECAYING"
        elif recent > older + 0.05:
            decay = "IMPROVING"
        else:
            decay = "STABLE"

        p10 = percentile(vals, 0.10)
        worst = min(vals) if vals else 0.0
        avg_mae = mean(maes)

        if p10 <= -0.80 or avg_mae <= -1.25 or worst <= -1.50:
            tail = "TAIL_RISK_HIGH"
        elif p10 <= -0.45 or avg_mae <= -0.75:
            tail = "TAIL_RISK_MEDIUM"
        else:
            tail = "TAIL_RISK_OK"

        prior_n = 120.0 if n < 90 else 80.0
        shrunk = avg * (n / (n + prior_n))

        path_quality = min(1.0, max(0.0, (mean(mfes) / max(abs(avg_mae), 0.05)) / 3.0))
        stability = min(1.0, max(0.0, 1.0 - abs(train - validation) / max(0.05, abs(avg))))

        return AlphaEvidence(
            n=n,
            mean_r=round(avg, 8),
            median_r=round(median(vals), 8) if vals else 0.0,
            shrunk_expectancy_r=round(shrunk, 8),
            winrate=round(safe_div(len(wins), n, 0.0), 8),
            profit_factor=round(pf, 8),
            calibrated_pf=round(min(pf, pf_cap(n)), 8),
            avg_mfe_r=round(mean(mfes), 8),
            avg_mae_r=round(avg_mae, 8),
            p10_r=round(p10, 8),
            worst_r=round(worst, 8),
            std_r=round(std(vals), 8),
            train_exp_r=round(train, 8),
            validation_exp_r=round(validation, 8),
            recent_exp_r=round(recent, 8),
            older_exp_r=round(older, 8),
            fold_positive_n=fold_positive,
            fold_pass=bool(fold_pass),
            decay_state=decay,
            tail_risk_state=tail,
            path_quality_score=round(path_quality, 8),
            stability_score=round(stability, 8),
            source=VERSION,
            payload={
                "fold_values": fold_values,
                "gross_win": gross_win,
                "gross_loss": gross_loss,
                "no_execution": True
            },
        )

    def all_evidence(self) -> List[Dict[str, Any]]:
        out = []
        for key, rows in self.grouped().items():
            identity = self.identity_from_row(rows[0])
            evidence = self.build_evidence(rows)
            out.append({
                "alpha_key": key,
                "identity": identity,
                "evidence": evidence,
                "rows": rows,
            })
        return out
