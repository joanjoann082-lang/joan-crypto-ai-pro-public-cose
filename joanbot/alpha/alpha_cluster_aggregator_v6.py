from __future__ import annotations

import argparse
import json
import math
import time
from typing import Any, Dict, List

from joanbot.storage.db import get_db
from joanbot.utils import utc_now_iso

VERSION = "ALPHA_CLUSTER_AGGREGATOR_V6"
MAX_REFRESHES = 300


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


def family_of(setup: str) -> str:
    s = str(setup or "").upper()
    if any(k in s for k in ["REBOUND", "SQUEEZE", "PULLBACK"]):
        return "REBOUND_PULLBACK_FAMILY"
    if any(k in s for k in ["BOUNCE", "FADE", "CONTINUATION"]):
        return "BOUNCE_FADE_TREND_FAMILY"
    return "OTHER"


class AlphaClusterAggregatorV6:
    """
    Converts fragmented alpha setups into statistically auditable alpha families.

    Reads:
    - universal_shadow_cases_v2
    - universal_shadow_results_v2

    Writes:
    - alpha_cluster_aggregator_v6
    - latest_alpha_cluster_aggregator_v6
    - alpha_cluster_aggregator_audit_v6

    Does not mutate execution/trading tables.
    """

    def __init__(self, db=None) -> None:
        self.db = db or get_db()

    def ensure_schema(self) -> None:
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS alpha_cluster_aggregator_v6 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                refresh_id INTEGER NOT NULL,
                ts TEXT NOT NULL,
                version TEXT NOT NULL,

                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                family_name TEXT NOT NULL,
                horizon_min INTEGER NOT NULL,

                n INTEGER NOT NULL,
                avg_r REAL NOT NULL,
                winrate REAL NOT NULL,
                worst_r REAL NOT NULL,
                best_r REAL NOT NULL,
                std_r REAL NOT NULL,
                lcb_r REAL NOT NULL,
                ucb_r REAL NOT NULL,

                sample_quality REAL NOT NULL,
                edge_quality REAL NOT NULL,
                stability_quality REAL NOT NULL,
                cluster_score REAL NOT NULL,

                cluster_state TEXT NOT NULL,
                hard_vetoes TEXT NOT NULL,
                payload TEXT NOT NULL
            );
        """)

        self.db.execute("""
            CREATE TABLE IF NOT EXISTS alpha_cluster_aggregator_audit_v6 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                version TEXT NOT NULL,
                event TEXT NOT NULL,
                level TEXT NOT NULL,
                message TEXT NOT NULL,
                payload TEXT NOT NULL
            );
        """)

        self.db.execute("DROP VIEW IF EXISTS latest_alpha_cluster_aggregator_v6;")
        self.db.execute("""
            CREATE VIEW latest_alpha_cluster_aggregator_v6 AS
            SELECT *
            FROM alpha_cluster_aggregator_v6
            WHERE refresh_id = (
                SELECT MAX(refresh_id)
                FROM alpha_cluster_aggregator_v6
            );
        """)

    def audit(self, event: str, level: str, message: str, payload: Dict[str, Any]) -> None:
        self.db.execute("""
            INSERT INTO alpha_cluster_aggregator_audit_v6
            (ts, version, event, level, message, payload)
            VALUES (?, ?, ?, ?, ?, ?);
        """, (utc_now_iso(), VERSION, event, level, message[:500], js(payload)))

    def qmany(self, sql: str) -> List[Dict[str, Any]]:
        try:
            return [dict(r) for r in self.db.query(sql)]
        except Exception as e:
            self.audit("QUERY_FAILED", "ERROR", repr(e), {"sql": sql[:500]})
            return []

    def raw_groups(self) -> List[Dict[str, Any]]:
        rows = self.qmany("""
            SELECT
              c.symbol,
              c.side,
              c.setup,
              c.horizon_min,
              r.result_r
            FROM universal_shadow_results_v2 r
            JOIN universal_shadow_cases_v2 c ON c.id = r.case_id
            WHERE r.result_r IS NOT NULL
            ORDER BY r.id DESC
            LIMIT 900;
        """)

        groups: Dict[tuple, List[float]] = {}

        for r in rows:
            key = (
                str(r.get("symbol")),
                str(r.get("side")),
                family_of(str(r.get("setup"))),
                inum(r.get("horizon_min")),
            )
            groups.setdefault(key, []).append(fnum(r.get("result_r")))

        out: List[Dict[str, Any]] = []

        for (symbol, side, family, horizon), vals in groups.items():
            n = len(vals)
            if n < 10:
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

            sample_quality = min(100.0, n * 100.0 / 90.0)
            edge_quality = max(0.0, min(100.0, (avg / 0.20) * 45.0 + (max(0.0, lcb) / 0.08) * 35.0 + (winrate - 45.0) * 1.2))
            stability_quality = max(0.0, min(100.0, 100.0 - abs(worst) * 35.0 - std * 25.0))
            cluster_score = round(sample_quality * 0.30 + edge_quality * 0.45 + stability_quality * 0.25, 4)

            hard_vetoes: List[str] = []

            if n < 30:
                hard_vetoes.append("N_LT_30")
            if lcb <= 0:
                hard_vetoes.append("LCB_NOT_POSITIVE")
            if winrate < 52:
                hard_vetoes.append("WINRATE_LT_52")
            if worst <= -1.0:
                hard_vetoes.append("TAIL_LOSS_GE_1R")

            if n >= 50 and avg >= 0.08 and lcb > 0 and winrate >= 55 and worst > -0.75:
                state = "CLUSTER_READY_FOR_REGIME"
            elif n >= 30 and avg >= 0.06 and winrate >= 55:
                state = "CLUSTER_CANDIDATE"
            elif avg < 0:
                state = "CLUSTER_NEGATIVE"
            else:
                state = "CLUSTER_OBSERVE"

            out.append({
                "symbol": symbol,
                "side": side,
                "family_name": family,
                "horizon_min": horizon,
                "n": n,
                "avg_r": avg,
                "winrate": winrate,
                "worst_r": worst,
                "best_r": best,
                "std_r": std,
                "lcb_r": lcb,
                "ucb_r": ucb,
                "sample_quality": sample_quality,
                "edge_quality": edge_quality,
                "stability_quality": stability_quality,
                "cluster_score": cluster_score,
                "cluster_state": state,
                "hard_vetoes": hard_vetoes,
            })

        out.sort(key=lambda x: (x["cluster_state"] == "CLUSTER_READY_FOR_REGIME", x["cluster_score"], x["lcb_r"], x["avg_r"]), reverse=True)
        return out

    def refresh(self) -> Dict[str, Any]:
        self.ensure_schema()
        refresh_id = int(time.time() * 1000)
        ts = utc_now_iso()

        rows = self.raw_groups()

        for r in rows:
            payload = {
                "source": VERSION,
                "method": "family_grouped_shadow_last_900",
                "institutional_note": "cluster evidence only, no execution permission",
            }

            self.db.execute("""
                INSERT INTO alpha_cluster_aggregator_v6 (
                    refresh_id, ts, version,
                    symbol, side, family_name, horizon_min,
                    n, avg_r, winrate, worst_r, best_r, std_r, lcb_r, ucb_r,
                    sample_quality, edge_quality, stability_quality, cluster_score,
                    cluster_state, hard_vetoes, payload
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """, (
                refresh_id, ts, VERSION,
                r["symbol"], r["side"], r["family_name"], r["horizon_min"],
                r["n"], r["avg_r"], r["winrate"], r["worst_r"], r["best_r"], r["std_r"], r["lcb_r"], r["ucb_r"],
                r["sample_quality"], r["edge_quality"], r["stability_quality"], r["cluster_score"],
                r["cluster_state"], js(r["hard_vetoes"]), js(payload),
            ))

        self.db.execute("""
            DELETE FROM alpha_cluster_aggregator_v6
            WHERE refresh_id NOT IN (
                SELECT DISTINCT refresh_id
                FROM alpha_cluster_aggregator_v6
                ORDER BY refresh_id DESC
                LIMIT ?
            );
        """, (MAX_REFRESHES,))

        summary = {
            "version": VERSION,
            "refresh_id": refresh_id,
            "clusters": len(rows),
            "ready": sum(1 for r in rows if r["cluster_state"] == "CLUSTER_READY_FOR_REGIME"),
            "candidate": sum(1 for r in rows if r["cluster_state"] == "CLUSTER_CANDIDATE"),
            "best": rows[0] if rows else None,
        }

        self.audit("REFRESH", "INFO", "Alpha Cluster Aggregator V6 refreshed", summary)
        return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()

    engine = AlphaClusterAggregatorV6()

    if args.refresh:
        print(json.dumps(engine.refresh(), indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
