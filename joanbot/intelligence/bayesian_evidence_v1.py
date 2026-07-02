from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

VERSION = "BAYESIAN_EVIDENCE_PROMOTION_V1_INSTITUTIONAL"

DB_PATH = Path(os.environ.get("JOANBOT_DB", "data/joanbot_v14.sqlite"))

MIN_FORWARD_N = 100
MIN_CLEAN_EXEC_N_FOR_OPEN = 20
MIN_CLEAN_EXEC_N_FOR_PROBE = 3
MIN_EFFECTIVE_N_PROBE = 35
MIN_EFFECTIVE_N_OPEN = 120

FORWARD_WEIGHT_CAP = 0.45
CLEAN_EXEC_WEIGHT = 1.00

PRIOR_EXP_R = 0.0
PRIOR_N = 30.0

MIN_SHRUNK_EXP_PROBE = 0.035
MIN_SHRUNK_EXP_OPEN = 0.080
MIN_PF_PROBE = 1.10
MIN_PF_OPEN = 1.25
MAX_AVG_MAE_R_PROBE = 0.75
MAX_AVG_MAE_R_OPEN = 0.60

MAX_DIVERGENCE_PENALTY = 0.65


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def fnum(x: Any, default: float = 0.0) -> float:
    try:
        if x is None:
            return default
        return float(x)
    except Exception:
        return default


def txt(x: Any) -> str:
    return "" if x is None else str(x)


def safe_div(a: float, b: float, default: float = 0.0) -> float:
    try:
        if b == 0:
            return default
        return a / b
    except Exception:
        return default


@dataclass
class EvidenceScore:
    ts: str
    version: str
    symbol: str
    side: str
    setup: str

    forward_n: int
    forward_exp_r: float
    forward_pf: Optional[float]
    forward_winrate: float
    forward_avg_mfe_r: float
    forward_avg_mae_r: float

    clean_exec_n: int
    clean_exec_exp_usd: float
    clean_exec_pnl_usd: float
    clean_exec_winrate: float

    excluded_exec_n: int
    excluded_pnl_usd: float

    effective_n: float
    raw_combined_exp: float
    shrunk_exp_r: float
    confidence: float
    robustness_score: float
    divergence_penalty: float
    quality_score: float

    status: str
    allow_open: int
    allow_probe: int
    size_multiplier_cap: float

    reasons: List[str]
    payload: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class BayesianEvidenceV1:
    """
    Institutional Bayesian evidence layer.

    Owns:
    - combines clean execution evidence with forward evidence
    - applies shrinkage
    - applies divergence penalty
    - produces promotion status
    - stores bounded evidence summaries

    Does not own:
    - order execution
    - broker writes
    - risk sizing implementation
    - final decision execution
    - dashboard rendering

    Critical rule:
    Forward-only evidence cannot become direct OPEN.
    """

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path

    def connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(str(self.db_path), timeout=10)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA busy_timeout=10000;")
        return con

    def ensure_schema(self, con: sqlite3.Connection) -> None:
        con.execute("""
            CREATE TABLE IF NOT EXISTS bayesian_evidence_scores_v1 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                version TEXT NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                setup TEXT NOT NULL,

                forward_n INTEGER NOT NULL,
                forward_exp_r REAL NOT NULL,
                forward_pf REAL,
                forward_winrate REAL NOT NULL,
                forward_avg_mfe_r REAL NOT NULL,
                forward_avg_mae_r REAL NOT NULL,

                clean_exec_n INTEGER NOT NULL,
                clean_exec_exp_usd REAL NOT NULL,
                clean_exec_pnl_usd REAL NOT NULL,
                clean_exec_winrate REAL NOT NULL,

                excluded_exec_n INTEGER NOT NULL,
                excluded_pnl_usd REAL NOT NULL,

                effective_n REAL NOT NULL,
                raw_combined_exp REAL NOT NULL,
                shrunk_exp_r REAL NOT NULL,
                confidence REAL NOT NULL,
                robustness_score REAL NOT NULL,
                divergence_penalty REAL NOT NULL,
                quality_score REAL NOT NULL,

                status TEXT NOT NULL,
                allow_open INTEGER NOT NULL,
                allow_probe INTEGER NOT NULL,
                size_multiplier_cap REAL NOT NULL,

                reasons TEXT NOT NULL,
                payload TEXT NOT NULL
            );
        """)

        con.execute("""
            CREATE INDEX IF NOT EXISTS idx_bayesian_evidence_scores_v1_key
            ON bayesian_evidence_scores_v1(symbol, side, setup, id);
        """)

        con.execute("DROP VIEW IF EXISTS latest_bayesian_evidence_v1;")
        con.execute("""
            CREATE VIEW latest_bayesian_evidence_v1 AS
            SELECT s.*
            FROM bayesian_evidence_scores_v1 s
            JOIN (
                SELECT symbol, side, setup, MAX(id) AS max_id
                FROM bayesian_evidence_scores_v1
                GROUP BY symbol, side, setup
            ) x
              ON x.max_id = s.id;
        """)

    def table_exists(self, con: sqlite3.Connection, name: str) -> bool:
        row = con.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','view') AND name=?",
            (name,),
        ).fetchone()
        return bool(row)

    def forward_rows(self, con: sqlite3.Connection) -> List[sqlite3.Row]:
        return list(con.execute("""
            WITH joined AS (
                SELECT
                    fc.action,
                    COALESCE(fr.symbol, fc.symbol) AS symbol,
                    fc.side,
                    fc.setup,
                    fr.result_r,
                    fr.mfe_r,
                    fr.mae_r
                FROM forward_results fr
                JOIN forward_cases fc ON fr.case_id = fc.id
                WHERE fr.result_r IS NOT NULL
            )
            SELECT
                symbol,
                side,
                setup,
                COUNT(*) AS n,
                AVG(result_r) AS exp_r,
                SUM(CASE WHEN result_r > 0 THEN 1 ELSE 0 END)*1.0/COUNT(*) AS winrate,
                SUM(CASE WHEN result_r > 0 THEN result_r ELSE 0 END) AS gross_win_r,
                ABS(SUM(CASE WHEN result_r < 0 THEN result_r ELSE 0 END)) AS gross_loss_r,
                AVG(mfe_r) AS avg_mfe_r,
                AVG(mae_r) AS avg_mae_r
            FROM joined
            GROUP BY symbol, side, setup
            HAVING COUNT(*) >= 30;
        """).fetchall())

    def clean_exec_map(self, con: sqlite3.Connection) -> Dict[tuple, Dict[str, Any]]:
        if not self.table_exists(con, "evidence_clean_positions_v1"):
            return {}

        rows = con.execute("""
            SELECT
                symbol,
                side,
                setup,
                COUNT(*) AS n,
                SUM(pnl_usd) AS pnl_usd,
                AVG(pnl_usd) AS exp_usd,
                SUM(CASE WHEN pnl_usd > 0 THEN 1 ELSE 0 END)*1.0/COUNT(*) AS winrate
            FROM evidence_clean_positions_v1
            WHERE status='CLOSED'
            GROUP BY symbol, side, setup;
        """).fetchall()

        out = {}
        for r in rows:
            out[(txt(r["symbol"]), txt(r["side"]), txt(r["setup"]))] = dict(r)
        return out

    def excluded_exec_map(self, con: sqlite3.Connection) -> Dict[tuple, Dict[str, Any]]:
        if not self.table_exists(con, "evidence_positions_with_provenance_v1"):
            return {}

        rows = con.execute("""
            SELECT
                symbol,
                side,
                setup,
                COUNT(*) AS n,
                SUM(pnl_usd) AS pnl_usd
            FROM evidence_positions_with_provenance_v1
            WHERE status='CLOSED'
              AND COALESCE(clean_for_evidence,0)=0
            GROUP BY symbol, side, setup;
        """).fetchall()

        out = {}
        for r in rows:
            out[(txt(r["symbol"]), txt(r["side"]), txt(r["setup"]))] = dict(r)
        return out

    def score_one(self, fw: sqlite3.Row, clean_map: Dict[tuple, Dict[str, Any]], excl_map: Dict[tuple, Dict[str, Any]]) -> EvidenceScore:
        symbol = txt(fw["symbol"])
        side = txt(fw["side"])
        setup = txt(fw["setup"])
        key = (symbol, side, setup)

        forward_n = int(fnum(fw["n"], 0))
        forward_exp = fnum(fw["exp_r"], 0.0)
        gross_win = fnum(fw["gross_win_r"], 0.0)
        gross_loss = fnum(fw["gross_loss_r"], 0.0)
        forward_pf = safe_div(gross_win, gross_loss, default=None) if gross_loss > 0 else None
        forward_winrate = fnum(fw["winrate"], 0.0)
        forward_mfe = fnum(fw["avg_mfe_r"], 0.0)
        forward_mae = fnum(fw["avg_mae_r"], 0.0)

        clean = clean_map.get(key, {})
        clean_n = int(fnum(clean.get("n"), 0))
        clean_pnl = fnum(clean.get("pnl_usd"), 0.0)
        clean_exp_usd = fnum(clean.get("exp_usd"), 0.0)
        clean_winrate = fnum(clean.get("winrate"), 0.0)

        excluded = excl_map.get(key, {})
        excluded_n = int(fnum(excluded.get("n"), 0))
        excluded_pnl = fnum(excluded.get("pnl_usd"), 0.0)

        # Forward evidence is useful, but capped.
        # Clean execution evidence is more important, but in this DB it is USD-based,
        # so it is used primarily for provenance/confidence/divergence, not raw R promotion.
        forward_eff_n = min(float(forward_n) * FORWARD_WEIGHT_CAP, 90.0)
        clean_eff_n = float(clean_n) * CLEAN_EXEC_WEIGHT * 8.0
        effective_n = forward_eff_n + clean_eff_n

        # Divergence penalty:
        # If forward is strongly positive but clean execution sample is absent/low,
        # do not allow direct OPEN.
        divergence_penalty = 0.0

        if forward_exp > 0.15 and clean_n == 0:
            divergence_penalty += 0.35
        elif forward_exp > 0.15 and clean_n < MIN_CLEAN_EXEC_N_FOR_PROBE:
            divergence_penalty += 0.25

        if clean_n > 0 and clean_exp_usd < 0 and forward_exp > 0:
            divergence_penalty += 0.35

        if excluded_n > 0 and excluded_pnl < 0 and clean_n == 0:
            divergence_penalty += 0.10

        divergence_penalty = min(MAX_DIVERGENCE_PENALTY, divergence_penalty)

        raw_combined_exp = forward_exp * (1.0 - divergence_penalty)

        shrunk_exp = (
            (raw_combined_exp * effective_n) + (PRIOR_EXP_R * PRIOR_N)
        ) / max(1.0, effective_n + PRIOR_N)

        confidence = min(1.0, effective_n / MIN_EFFECTIVE_N_OPEN)

        pf_score = 0.0
        if forward_pf is not None:
            pf_score = min(1.0, max(0.0, (forward_pf - 1.0) / 1.0))

        exp_score = min(1.0, max(0.0, shrunk_exp / 0.15))
        sample_score = min(1.0, effective_n / MIN_EFFECTIVE_N_OPEN)
        mae_penalty = min(0.35, abs(min(0.0, forward_mae)) / 2.0)

        robustness = max(0.0, min(1.0, 0.40 * exp_score + 0.30 * pf_score + 0.30 * sample_score - mae_penalty))
        quality_score = max(0.0, min(100.0, 100.0 * robustness * (1.0 - divergence_penalty)))

        reasons: List[str] = []

        if forward_n < MIN_FORWARD_N:
            reasons.append("FORWARD_SAMPLE_TOO_LOW")
        else:
            reasons.append("FORWARD_SAMPLE_OK")

        if forward_exp > 0:
            reasons.append("FORWARD_EXPECTANCY_POSITIVE")
        else:
            reasons.append("FORWARD_EXPECTANCY_NOT_POSITIVE")

        if forward_pf is not None and forward_pf >= MIN_PF_PROBE:
            reasons.append("FORWARD_PF_ACCEPTABLE")
        else:
            reasons.append("FORWARD_PF_NOT_ENOUGH_OR_UNDEFINED")

        if clean_n >= MIN_CLEAN_EXEC_N_FOR_PROBE:
            reasons.append("CLEAN_EXECUTION_SAMPLE_PROBE_OK")
        else:
            reasons.append("INSUFFICIENT_CLEAN_EXECUTION_SAMPLE")

        if clean_n >= MIN_CLEAN_EXEC_N_FOR_OPEN:
            reasons.append("CLEAN_EXECUTION_SAMPLE_OPEN_OK")
        else:
            reasons.append("NO_DIRECT_OPEN_WITHOUT_CLEAN_EXECUTION_SAMPLE")

        if divergence_penalty > 0:
            reasons.append("DIVERGENCE_PENALTY_APPLIED")

        if abs(forward_mae) > MAX_AVG_MAE_R_PROBE:
            reasons.append("MAE_TOO_HIGH_FOR_PROBE")

        allow_open = 0
        allow_probe = 0
        size_cap = 0.0

        status = "REJECTED_NO_EDGE"

        if forward_exp > 0 and forward_n >= MIN_FORWARD_N:
            status = "RESEARCH_CANDIDATE"

        if (
            forward_n >= MIN_FORWARD_N
            and effective_n >= MIN_EFFECTIVE_N_PROBE
            and shrunk_exp >= MIN_SHRUNK_EXP_PROBE
            and (forward_pf is not None and forward_pf >= MIN_PF_PROBE)
            and abs(forward_mae) <= MAX_AVG_MAE_R_PROBE
        ):
            # Probe can be allowed with low/zero clean sample only if it remains research-grade.
            # This does NOT mean direct OPEN.
            allow_probe = 1
            size_cap = 0.08
            status = "MICRO_PROBE_CANDIDATE"

        if clean_n < MIN_CLEAN_EXEC_N_FOR_PROBE:
            # Institutional safety: forward-only candidates cannot probe automatically
            # unless a later canary policy explicitly activates them.
            allow_probe = 0
            size_cap = 0.0
            if status == "MICRO_PROBE_CANDIDATE":
                status = "RESEARCH_CANDIDATE_PENDING_CLEAN_SAMPLE"
                reasons.append("MICRO_PROBE_BLOCKED_UNTIL_CLEAN_SAMPLE_POLICY")

        if (
            clean_n >= MIN_CLEAN_EXEC_N_FOR_OPEN
            and effective_n >= MIN_EFFECTIVE_N_OPEN
            and shrunk_exp >= MIN_SHRUNK_EXP_OPEN
            and (forward_pf is not None and forward_pf >= MIN_PF_OPEN)
            and abs(forward_mae) <= MAX_AVG_MAE_R_OPEN
            and divergence_penalty <= 0.15
        ):
            allow_open = 1
            allow_probe = 1
            size_cap = 0.25
            status = "OPEN_ELIGIBLE"

        if status.startswith("RESEARCH"):
            reasons.append("NO_DIRECT_OPEN")

        return EvidenceScore(
            ts=utc_now_iso(),
            version=VERSION,
            symbol=symbol,
            side=side,
            setup=setup,
            forward_n=forward_n,
            forward_exp_r=round(forward_exp, 8),
            forward_pf=round(forward_pf, 6) if forward_pf is not None else None,
            forward_winrate=round(forward_winrate, 8),
            forward_avg_mfe_r=round(forward_mfe, 8),
            forward_avg_mae_r=round(forward_mae, 8),
            clean_exec_n=clean_n,
            clean_exec_exp_usd=round(clean_exp_usd, 8),
            clean_exec_pnl_usd=round(clean_pnl, 8),
            clean_exec_winrate=round(clean_winrate, 8),
            excluded_exec_n=excluded_n,
            excluded_pnl_usd=round(excluded_pnl, 8),
            effective_n=round(effective_n, 4),
            raw_combined_exp=round(raw_combined_exp, 8),
            shrunk_exp_r=round(shrunk_exp, 8),
            confidence=round(confidence, 6),
            robustness_score=round(robustness, 6),
            divergence_penalty=round(divergence_penalty, 6),
            quality_score=round(quality_score, 4),
            status=status,
            allow_open=allow_open,
            allow_probe=allow_probe,
            size_multiplier_cap=round(size_cap, 6),
            reasons=reasons,
            payload={
                "rules": {
                    "forward_weight_cap": FORWARD_WEIGHT_CAP,
                    "prior_exp_r": PRIOR_EXP_R,
                    "prior_n": PRIOR_N,
                    "min_forward_n": MIN_FORWARD_N,
                    "min_clean_exec_n_for_probe": MIN_CLEAN_EXEC_N_FOR_PROBE,
                    "min_clean_exec_n_for_open": MIN_CLEAN_EXEC_N_FOR_OPEN,
                    "forward_only_direct_open": False,
                    "risk_changed": False,
                    "execution_changed": False,
                }
            },
        )

    def refresh(self) -> Dict[str, Any]:
        with self.connect() as con:
            self.ensure_schema(con)

            # Registry must exist before this layer is trusted.
            required = ["evidence_clean_positions_v1", "evidence_positions_with_provenance_v1"]
            missing = [x for x in required if not self.table_exists(con, x)]
            if missing:
                raise RuntimeError(f"Missing required evidence registry views: {missing}")

            fw_rows = self.forward_rows(con)
            clean_map = self.clean_exec_map(con)
            excl_map = self.excluded_exec_map(con)

            scores = [self.score_one(row, clean_map, excl_map) for row in fw_rows]

            for s in scores:
                self.insert_score(con, s)

            self.apply_retention(con)
            con.commit()

            return {
                "version": VERSION,
                "scores": len(scores),
                "open_eligible": sum(1 for s in scores if s.allow_open),
                "probe_eligible": sum(1 for s in scores if s.allow_probe),
                "research_candidates": sum(1 for s in scores if "RESEARCH" in s.status),
                "statuses": self.status_counts(scores),
            }

    def insert_score(self, con: sqlite3.Connection, s: EvidenceScore) -> None:
        con.execute("""
            INSERT INTO bayesian_evidence_scores_v1 (
                ts, version, symbol, side, setup,
                forward_n, forward_exp_r, forward_pf, forward_winrate, forward_avg_mfe_r, forward_avg_mae_r,
                clean_exec_n, clean_exec_exp_usd, clean_exec_pnl_usd, clean_exec_winrate,
                excluded_exec_n, excluded_pnl_usd,
                effective_n, raw_combined_exp, shrunk_exp_r, confidence, robustness_score, divergence_penalty, quality_score,
                status, allow_open, allow_probe, size_multiplier_cap,
                reasons, payload
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """, (
            s.ts, s.version, s.symbol, s.side, s.setup,
            s.forward_n, s.forward_exp_r, s.forward_pf, s.forward_winrate, s.forward_avg_mfe_r, s.forward_avg_mae_r,
            s.clean_exec_n, s.clean_exec_exp_usd, s.clean_exec_pnl_usd, s.clean_exec_winrate,
            s.excluded_exec_n, s.excluded_pnl_usd,
            s.effective_n, s.raw_combined_exp, s.shrunk_exp_r, s.confidence, s.robustness_score, s.divergence_penalty, s.quality_score,
            s.status, s.allow_open, s.allow_probe, s.size_multiplier_cap,
            json.dumps(s.reasons, separators=(",", ":"), ensure_ascii=False),
            json.dumps(s.payload, separators=(",", ":"), ensure_ascii=False),
        ))

    def apply_retention(self, con: sqlite3.Connection) -> None:
        con.execute("""
            DELETE FROM bayesian_evidence_scores_v1
            WHERE id NOT IN (
                SELECT id
                FROM bayesian_evidence_scores_v1
                ORDER BY id DESC
                LIMIT 500
            );
        """)

    def status_counts(self, scores: List[EvidenceScore]) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for s in scores:
            out[s.status] = out.get(s.status, 0) + 1
        return out

    def latest(self) -> List[Dict[str, Any]]:
        with self.connect() as con:
            self.ensure_schema(con)
            rows = con.execute("""
                SELECT
                    symbol, side, setup,
                    forward_n, forward_exp_r, forward_pf,
                    clean_exec_n, excluded_exec_n,
                    effective_n, shrunk_exp_r, confidence,
                    divergence_penalty, quality_score,
                    status, allow_open, allow_probe, size_multiplier_cap,
                    reasons
                FROM latest_bayesian_evidence_v1
                ORDER BY quality_score DESC, shrunk_exp_r DESC;
            """).fetchall()
            return [dict(r) for r in rows]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--latest", action="store_true")
    args = parser.parse_args()

    engine = BayesianEvidenceV1()

    if args.refresh:
        print(json.dumps(engine.refresh(), indent=2, sort_keys=True))

    if args.latest:
        for row in engine.latest():
            print(row)


if __name__ == "__main__":
    main()
