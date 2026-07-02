from __future__ import annotations

import argparse
import json
import math
import time
from typing import Any, Dict, List

from joanbot.storage.db import get_db
from joanbot.utils import utc_now_iso

VERSION = "EDGE_ROBUSTNESS_VALIDATOR_V9_2"
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


def esc(x: Any) -> str:
    return str(x or "").replace("'", "''")


class EdgeRobustnessValidatorV9:
    """
    Institutional robustness layer.

    Reads:
    - latest_institutional_edge_factory_v8
    - universal_shadow_cases_v2
    - universal_shadow_results_v2

    Writes:
    - edge_robustness_validator_v9
    - latest_edge_robustness_validator_v9

    Purpose:
    - block overfit edges
    - require exact sub-edge consistency
    - require recent edge not toxic
    - require LCB defensible before canary
    """

    def __init__(self, db=None) -> None:
        self.db = db or get_db()

    def ensure_schema(self) -> None:
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS edge_robustness_validator_v9 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                refresh_id INTEGER NOT NULL,
                ts TEXT NOT NULL,
                version TEXT NOT NULL,

                source_edge_id INTEGER NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                family_name TEXT NOT NULL,
                setup TEXT NOT NULL,
                profile TEXT NOT NULL,
                horizon_min INTEGER NOT NULL,

                n INTEGER NOT NULL,
                avg_r REAL NOT NULL,
                lcb_r REAL NOT NULL,
                winrate REAL NOT NULL,
                worst_r REAL NOT NULL,
                best_r REAL NOT NULL,

                recent20_n INTEGER NOT NULL,
                recent20_avg_r REAL NOT NULL,
                recent20_lcb_r REAL NOT NULL,
                recent20_winrate REAL NOT NULL,

                recent50_n INTEGER NOT NULL,
                recent50_avg_r REAL NOT NULL,
                recent50_lcb_r REAL NOT NULL,
                recent50_winrate REAL NOT NULL,

                decay_guard REAL NOT NULL,
                overfit_penalty REAL NOT NULL,
                robustness_score REAL NOT NULL,

                validation_state TEXT NOT NULL,
                canary_permission INTEGER NOT NULL,

                hard_vetoes TEXT NOT NULL,
                payload TEXT NOT NULL
            );
        """)

        self.db.execute("DROP VIEW IF EXISTS latest_edge_robustness_validator_v9;")
        self.db.execute("""
            CREATE VIEW latest_edge_robustness_validator_v9 AS
            SELECT *
            FROM edge_robustness_validator_v9
            WHERE refresh_id = (
                SELECT MAX(refresh_id)
                FROM edge_robustness_validator_v9
            );
        """)

    def qmany(self, sql: str) -> List[Dict[str, Any]]:
        try:
            return [dict(r) for r in self.db.query(sql)]
        except Exception:
            return []

    def candidates(self) -> List[Dict[str, Any]]:
        return self.qmany("""
            SELECT *
            FROM latest_institutional_edge_factory_v8
            ORDER BY
              micro_canary_eligible DESC,
              edge_score DESC,
              lcb_r DESC,
              avg_r DESC,
              n DESC
            LIMIT 30;
        """)

    def exact_results(self, edge: Dict[str, Any], limit: int = 300) -> List[float]:
        symbol = esc(edge.get("symbol"))
        side = esc(edge.get("side"))
        setup = esc(edge.get("setup"))
        profile = esc(edge.get("profile"))
        horizon = inum(edge.get("horizon_min"))

        rows = self.qmany(f"""
            SELECT r.result_r
            FROM universal_shadow_results_v2 r
            JOIN universal_shadow_cases_v2 c ON c.id = r.case_id
            WHERE r.result_r IS NOT NULL
              AND c.symbol='{symbol}'
              AND c.side='{side}'
              AND c.setup='{setup}'
              AND COALESCE(c.profile,'UNKNOWN')='{profile}'
              AND c.horizon_min={horizon}
            ORDER BY r.id DESC
            LIMIT {int(limit)};
        """)
        return [fnum(r.get("result_r")) for r in rows]

    def stats(self, vals: List[float]) -> Dict[str, float]:
        n = len(vals)
        if n <= 0:
            return {
                "n": 0,
                "avg": 0.0,
                "lcb": -999.0,
                "winrate": 0.0,
                "worst": 0.0,
                "best": 0.0,
                "std": 0.0,
            }

        avg = sum(vals) / n
        win = sum(1 for x in vals if x > 0) * 100.0 / n
        worst = min(vals)
        best = max(vals)

        if n > 1:
            var = sum((x - avg) ** 2 for x in vals) / (n - 1)
        else:
            var = 0.0

        std = math.sqrt(max(0.0, var))
        se = std / math.sqrt(max(1, n))
        lcb = avg - 1.64 * se

        return {
            "n": n,
            "avg": avg,
            "lcb": lcb,
            "winrate": win,
            "worst": worst,
            "best": best,
            "std": std,
        }

    def validate_edge(self, edge: Dict[str, Any]) -> Dict[str, Any]:
        vals = self.exact_results(edge, 300)
        all_s = self.stats(vals)
        s20 = self.stats(vals[:20])
        s50 = self.stats(vals[:50])

        vetoes: List[str] = []

        n = inum(all_s["n"])
        avg = fnum(all_s["avg"])
        lcb = fnum(all_s["lcb"])
        wr = fnum(all_s["winrate"])
        worst = fnum(all_s["worst"])

        decay_guard = fnum(s20["avg"]) - fnum(s50["avg"])

        overfit_penalty = 0.0
        if n < 35 and avg > 0.20:
            overfit_penalty += 20.0
            vetoes.append("SMALL_SAMPLE_HIGH_AVG_OVERFIT_RISK")
        if fnum(s20["avg"]) < 0 and avg > 0:
            overfit_penalty += 30.0
            vetoes.append("RECENT20_NEGATIVE_WHILE_FULL_POSITIVE")
        if fnum(s50["lcb"]) <= -0.05:
            overfit_penalty += 20.0
            vetoes.append("RECENT50_LCB_TOO_NEGATIVE")

        if n < 25:
            vetoes.append("N_LT_25")
        if lcb <= 0:
            vetoes.append("LCB_NOT_POSITIVE")
        if avg < 0.06:
            vetoes.append("AVG_LT_006R")
        if wr < 55:
            vetoes.append("WINRATE_LT_55")
        if worst <= -1.0:
            vetoes.append("WORST_GE_1R_LOSS")
        if fnum(s20["avg"]) < -0.02:
            vetoes.append("RECENT20_TOXIC")
        # V9.2 contextual decay policy.
        # A falling recent20 average is not automatically toxic if it remains
        # positive, its LCB remains positive, the 50-sample LCB is strong,
        # the full edge is robust, and the worst loss is controlled.
        positive_cooling_accepted = (
            n >= 45
            and avg >= 0.12
            and lcb >= 0.05
            and wr >= 65
            and worst > -0.75
            and fnum(s20["avg"]) >= 0.03
            and fnum(s20["lcb"]) >= 0.0
            and fnum(s50["lcb"]) >= 0.05
            and overfit_penalty == 0.0
        )

        toxic_decay = decay_guard < -0.10 and not positive_cooling_accepted

        if toxic_decay:
            vetoes.append("DECAY_GUARD_FAIL")

        robustness_score = 0.0
        robustness_score += min(30.0, max(0.0, lcb) * 500.0)
        robustness_score += min(25.0, max(0.0, avg) * 120.0)
        robustness_score += min(20.0, max(0.0, fnum(s20["avg"])) * 180.0)
        robustness_score += min(15.0, max(0.0, fnum(s50["lcb"])) * 300.0)
        robustness_score += min(10.0, max(0.0, wr - 50.0) * 1.25)
        robustness_score = max(0.0, robustness_score - overfit_penalty)

        if not vetoes and robustness_score >= 45:
            state = "ROBUST_EDGE_READY"
            canary = 1
        elif n >= 20 and avg > 0.04 and fnum(s20["avg"]) >= -0.02:
            state = "ROBUST_EDGE_CANDIDATE"
            canary = 0
        else:
            state = "ROBUSTNESS_BLOCK"
            canary = 0

        return {
            "source_edge_id": inum(edge.get("id")),
            "symbol": str(edge.get("symbol") or "UNKNOWN"),
            "side": str(edge.get("side") or "UNKNOWN"),
            "family_name": str(edge.get("family_name") or "UNKNOWN"),
            "setup": str(edge.get("setup") or "UNKNOWN"),
            "profile": str(edge.get("profile") or "UNKNOWN"),
            "horizon_min": inum(edge.get("horizon_min")),
            "n": n,
            "avg_r": avg,
            "lcb_r": lcb,
            "winrate": wr,
            "worst_r": fnum(all_s["worst"]),
            "best_r": fnum(all_s["best"]),
            "recent20_n": inum(s20["n"]),
            "recent20_avg_r": fnum(s20["avg"]),
            "recent20_lcb_r": fnum(s20["lcb"]),
            "recent20_winrate": fnum(s20["winrate"]),
            "recent50_n": inum(s50["n"]),
            "recent50_avg_r": fnum(s50["avg"]),
            "recent50_lcb_r": fnum(s50["lcb"]),
            "recent50_winrate": fnum(s50["winrate"]),
            "decay_guard": decay_guard,
            "overfit_penalty": overfit_penalty,
            "robustness_score": round(robustness_score, 4),
            "validation_state": state,
            "canary_permission": canary,
            "hard_vetoes": vetoes,
        }

    def refresh(self) -> Dict[str, Any]:
        self.ensure_schema()
        refresh_id = int(time.time() * 1000)
        ts = utc_now_iso()

        results: List[Dict[str, Any]] = []

        for edge in self.candidates():
            r = self.validate_edge(edge)
            results.append(r)

            self.db.execute("""
                INSERT INTO edge_robustness_validator_v9 (
                    refresh_id, ts, version,
                    source_edge_id, symbol, side, family_name, setup, profile, horizon_min,
                    n, avg_r, lcb_r, winrate, worst_r, best_r,
                    recent20_n, recent20_avg_r, recent20_lcb_r, recent20_winrate,
                    recent50_n, recent50_avg_r, recent50_lcb_r, recent50_winrate,
                    decay_guard, overfit_penalty, robustness_score,
                    validation_state, canary_permission,
                    hard_vetoes, payload
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """, (
                refresh_id, ts, VERSION,
                r["source_edge_id"], r["symbol"], r["side"], r["family_name"], r["setup"], r["profile"], r["horizon_min"],
                r["n"], r["avg_r"], r["lcb_r"], r["winrate"], r["worst_r"], r["best_r"],
                r["recent20_n"], r["recent20_avg_r"], r["recent20_lcb_r"], r["recent20_winrate"],
                r["recent50_n"], r["recent50_avg_r"], r["recent50_lcb_r"], r["recent50_winrate"],
                r["decay_guard"], r["overfit_penalty"], r["robustness_score"],
                r["validation_state"], r["canary_permission"],
                js(r["hard_vetoes"]), js({"source": VERSION, "no_execution_permission": True}),
            ))

        self.db.execute("""
            DELETE FROM edge_robustness_validator_v9
            WHERE refresh_id NOT IN (
                SELECT DISTINCT refresh_id
                FROM edge_robustness_validator_v9
                ORDER BY refresh_id DESC
                LIMIT ?
            );
        """, (MAX_REFRESHES,))

        results.sort(
            key=lambda x: (
                x["canary_permission"],
                x["robustness_score"],
                x["lcb_r"],
                x["avg_r"],
                x["n"],
            ),
            reverse=True,
        )

        return {
            "version": VERSION,
            "refresh_id": refresh_id,
            "validated": len(results),
            "ready": sum(1 for r in results if r["validation_state"] == "ROBUST_EDGE_READY"),
            "candidate": sum(1 for r in results if r["validation_state"] == "ROBUST_EDGE_CANDIDATE"),
            "blocked": sum(1 for r in results if r["validation_state"] == "ROBUSTNESS_BLOCK"),
            "best": results[0] if results else None,
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()

    engine = EdgeRobustnessValidatorV9()

    if args.refresh:
        print(json.dumps(engine.refresh(), indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
