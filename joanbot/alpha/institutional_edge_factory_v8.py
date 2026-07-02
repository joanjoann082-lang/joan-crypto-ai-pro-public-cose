from __future__ import annotations

import argparse
import json
import math
import time
from typing import Any, Dict, List, Tuple

from joanbot.storage.db import get_db
from joanbot.utils import utc_now_iso

VERSION = "INSTITUTIONAL_EDGE_FACTORY_V8"
MAX_REFRESHES = 400


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
    return json.dumps(x, separators=(",", ":"), ensure_ascii=False, default=str)


def family_of(setup: str) -> str:
    s = str(setup or "").upper()
    if any(k in s for k in ("REBOUND", "SQUEEZE", "PULLBACK")):
        return "REBOUND_PULLBACK_FAMILY"
    if any(k in s for k in ("BOUNCE", "FADE", "CONTINUATION")):
        return "BOUNCE_FADE_TREND_FAMILY"
    return "OTHER"


class InstitutionalEdgeFactoryV8:
    """
    Exact sub-edge factory.

    Reads:
    - universal_shadow_cases_v2
    - universal_shadow_results_v2

    Groups by:
    - symbol, side, family, setup, profile, horizon_min

    Writes:
    - institutional_edge_factory_v8
    - latest_institutional_edge_factory_v8
    - institutional_edge_factory_audit_v8

    Never mutates legacy trading tables.
    """

    def __init__(self, db=None) -> None:
        self.db = db or get_db()

    def ensure_schema(self) -> None:
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS institutional_edge_factory_v8 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                refresh_id INTEGER NOT NULL,
                ts TEXT NOT NULL,
                version TEXT NOT NULL,

                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                family_name TEXT NOT NULL,
                setup TEXT NOT NULL,
                profile TEXT NOT NULL,
                horizon_min INTEGER NOT NULL,

                n INTEGER NOT NULL,
                avg_r REAL NOT NULL,
                winrate REAL NOT NULL,
                worst_r REAL NOT NULL,
                best_r REAL NOT NULL,
                std_r REAL NOT NULL,
                lcb_r REAL NOT NULL,
                ucb_r REAL NOT NULL,

                recent_n INTEGER NOT NULL,
                recent_avg_r REAL NOT NULL,
                older_avg_r REAL NOT NULL,
                decay_r REAL NOT NULL,

                tail_loss_rate REAL NOT NULL,
                positive_tail_rate REAL NOT NULL,

                sample_quality REAL NOT NULL,
                edge_quality REAL NOT NULL,
                recency_quality REAL NOT NULL,
                tail_quality REAL NOT NULL,
                edge_score REAL NOT NULL,

                edge_state TEXT NOT NULL,
                micro_canary_eligible INTEGER NOT NULL,

                hard_vetoes TEXT NOT NULL,
                payload TEXT NOT NULL
            );
        """)

        self.db.execute("""
            CREATE TABLE IF NOT EXISTS institutional_edge_factory_audit_v8 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                version TEXT NOT NULL,
                event TEXT NOT NULL,
                level TEXT NOT NULL,
                message TEXT NOT NULL,
                payload TEXT NOT NULL
            );
        """)

        self.db.execute("DROP VIEW IF EXISTS latest_institutional_edge_factory_v8;")
        self.db.execute("""
            CREATE VIEW latest_institutional_edge_factory_v8 AS
            SELECT *
            FROM institutional_edge_factory_v8
            WHERE refresh_id = (
                SELECT MAX(refresh_id)
                FROM institutional_edge_factory_v8
            );
        """)

    def audit(self, event: str, level: str, message: str, payload: Dict[str, Any]) -> None:
        self.db.execute("""
            INSERT INTO institutional_edge_factory_audit_v8
            (ts, version, event, level, message, payload)
            VALUES (?, ?, ?, ?, ?, ?);
        """, (utc_now_iso(), VERSION, event, level, message[:500], js(payload)))

    def qmany(self, sql: str) -> List[Dict[str, Any]]:
        try:
            return [dict(r) for r in self.db.query(sql)]
        except Exception as e:
            self.audit("QUERY_FAILED", "ERROR", repr(e), {"sql": sql[:500]})
            return []

    def table_cols(self, table: str) -> set[str]:
        try:
            return {str(dict(r).get("name")) for r in self.db.query(f"PRAGMA table_info({table});")}
        except Exception:
            return set()

    def source_rows(self) -> List[Dict[str, Any]]:
        case_cols = self.table_cols("universal_shadow_cases_v2")

        profile_expr = "COALESCE(c.profile, 'UNKNOWN') AS profile" if "profile" in case_cols else "'UNKNOWN' AS profile"
        horizon_expr = "COALESCE(c.horizon_min, 0) AS horizon_min" if "horizon_min" in case_cols else "0 AS horizon_min"

        sql = f"""
            SELECT
              r.id AS result_id,
              c.symbol,
              c.side,
              c.setup,
              {profile_expr},
              {horizon_expr},
              r.result_r
            FROM universal_shadow_results_v2 r
            JOIN universal_shadow_cases_v2 c ON c.id = r.case_id
            WHERE r.result_r IS NOT NULL
            ORDER BY r.id DESC
            LIMIT 1500;
        """
        return self.qmany(sql)

    def compute_edges(self) -> List[Dict[str, Any]]:
        groups: Dict[Tuple[str, str, str, str, str, int], List[float]] = {}

        for r in self.source_rows():
            setup = str(r.get("setup") or "UNKNOWN")
            key = (
                str(r.get("symbol") or "UNKNOWN"),
                str(r.get("side") or "UNKNOWN"),
                family_of(setup),
                setup,
                str(r.get("profile") or "UNKNOWN"),
                inum(r.get("horizon_min")),
            )
            groups.setdefault(key, []).append(fnum(r.get("result_r")))

        edges: List[Dict[str, Any]] = []

        for (symbol, side, family, setup, profile, horizon), vals in groups.items():
            n = len(vals)
            if n < 8:
                continue

            avg = sum(vals) / n
            winrate = sum(1 for x in vals if x > 0) * 100.0 / n
            worst = min(vals)
            best = max(vals)

            variance = sum((x - avg) ** 2 for x in vals) / max(1, n - 1)
            std = math.sqrt(max(0.0, variance))
            se = std / math.sqrt(max(1, n))
            lcb = avg - 1.64 * se
            ucb = avg + 1.64 * se

            recent_n = min(30, n)
            recent_vals = vals[:recent_n]
            older_vals = vals[recent_n:] or vals

            recent_avg = sum(recent_vals) / len(recent_vals)
            older_avg = sum(older_vals) / len(older_vals)
            decay = recent_avg - older_avg

            tail_loss_rate = sum(1 for x in vals if x <= -0.75) * 100.0 / n
            positive_tail_rate = sum(1 for x in vals if x >= 0.75) * 100.0 / n

            sample_quality = min(100.0, n * 100.0 / 50.0)
            edge_quality = max(0.0, min(100.0, (avg / 0.18) * 35.0 + (max(0.0, lcb) / 0.08) * 40.0 + (winrate - 50.0) * 1.25))
            recency_quality = max(0.0, min(100.0, 50.0 + recent_avg * 140.0 + decay * 80.0))
            tail_quality = max(0.0, min(100.0, 100.0 - tail_loss_rate * 7.5 - max(0.0, -worst - 0.75) * 50.0))
            edge_score = round(sample_quality * 0.25 + edge_quality * 0.40 + recency_quality * 0.20 + tail_quality * 0.15, 4)

            vetoes: List[str] = []

            if n < 25:
                vetoes.append("N_LT_25")
            if lcb <= 0:
                vetoes.append("LCB_NOT_POSITIVE")
            if avg < 0.06:
                vetoes.append("AVG_LT_006R")
            if winrate < 55:
                vetoes.append("WINRATE_LT_55")
            if recent_avg < 0:
                vetoes.append("RECENT_AVG_NEGATIVE")
            if decay < -0.08:
                vetoes.append("DECAY_TOO_NEGATIVE")
            if tail_loss_rate > 12:
                vetoes.append("TAIL_LOSS_RATE_GT_12")
            if worst <= -1.0:
                vetoes.append("WORST_GE_1R_LOSS")

            if not vetoes:
                edge_state = "EDGE_READY_FOR_CANARY"
                micro = 1
            elif n >= 15 and avg >= 0.04 and winrate >= 52 and recent_avg >= -0.03:
                edge_state = "EDGE_CANDIDATE_NEEDS_SAMPLE"
                micro = 0
            elif avg < 0 or recent_avg < -0.04 or worst <= -1.0:
                edge_state = "EDGE_QUARANTINE"
                micro = 0
            else:
                edge_state = "EDGE_OBSERVE"
                micro = 0

            edges.append({
                "symbol": symbol,
                "side": side,
                "family_name": family,
                "setup": setup,
                "profile": profile,
                "horizon_min": horizon,
                "n": n,
                "avg_r": avg,
                "winrate": winrate,
                "worst_r": worst,
                "best_r": best,
                "std_r": std,
                "lcb_r": lcb,
                "ucb_r": ucb,
                "recent_n": recent_n,
                "recent_avg_r": recent_avg,
                "older_avg_r": older_avg,
                "decay_r": decay,
                "tail_loss_rate": tail_loss_rate,
                "positive_tail_rate": positive_tail_rate,
                "sample_quality": sample_quality,
                "edge_quality": edge_quality,
                "recency_quality": recency_quality,
                "tail_quality": tail_quality,
                "edge_score": edge_score,
                "edge_state": edge_state,
                "micro_canary_eligible": micro,
                "hard_vetoes": vetoes,
            })

        edges.sort(
            key=lambda x: (
                x["micro_canary_eligible"],
                x["edge_score"],
                x["lcb_r"],
                x["avg_r"],
                x["n"],
            ),
            reverse=True,
        )

        return edges

    def refresh(self) -> Dict[str, Any]:
        self.ensure_schema()

        refresh_id = int(time.time() * 1000)
        ts = utc_now_iso()
        edges = self.compute_edges()

        for e in edges:
            self.db.execute("""
                INSERT INTO institutional_edge_factory_v8 (
                    refresh_id, ts, version,
                    symbol, side, family_name, setup, profile, horizon_min,
                    n, avg_r, winrate, worst_r, best_r, std_r, lcb_r, ucb_r,
                    recent_n, recent_avg_r, older_avg_r, decay_r,
                    tail_loss_rate, positive_tail_rate,
                    sample_quality, edge_quality, recency_quality, tail_quality, edge_score,
                    edge_state, micro_canary_eligible,
                    hard_vetoes, payload
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """, (
                refresh_id, ts, VERSION,
                e["symbol"], e["side"], e["family_name"], e["setup"], e["profile"], e["horizon_min"],
                e["n"], e["avg_r"], e["winrate"], e["worst_r"], e["best_r"], e["std_r"], e["lcb_r"], e["ucb_r"],
                e["recent_n"], e["recent_avg_r"], e["older_avg_r"], e["decay_r"],
                e["tail_loss_rate"], e["positive_tail_rate"],
                e["sample_quality"], e["edge_quality"], e["recency_quality"], e["tail_quality"], e["edge_score"],
                e["edge_state"], e["micro_canary_eligible"],
                js(e["hard_vetoes"]),
                js({"source": VERSION, "method": "exact_subedge_shadow_last_1500", "no_direct_execution": True}),
            ))

        self.db.execute("""
            DELETE FROM institutional_edge_factory_v8
            WHERE refresh_id NOT IN (
                SELECT DISTINCT refresh_id
                FROM institutional_edge_factory_v8
                ORDER BY refresh_id DESC
                LIMIT ?
            );
        """, (MAX_REFRESHES,))

        summary = {
            "version": VERSION,
            "refresh_id": refresh_id,
            "edges": len(edges),
            "ready": sum(1 for e in edges if e["edge_state"] == "EDGE_READY_FOR_CANARY"),
            "candidate": sum(1 for e in edges if e["edge_state"] == "EDGE_CANDIDATE_NEEDS_SAMPLE"),
            "quarantine": sum(1 for e in edges if e["edge_state"] == "EDGE_QUARANTINE"),
            "best": edges[0] if edges else None,
        }

        self.audit("REFRESH", "INFO", "Institutional Edge Factory V8 refreshed", summary)
        return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()

    engine = InstitutionalEdgeFactoryV8()

    if args.refresh:
        print(json.dumps(engine.refresh(), indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
