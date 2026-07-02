from __future__ import annotations

import argparse
import json
from typing import Any, Dict, List

from joanbot.storage.db import get_db
from joanbot.utils import utc_now_iso

VERSION = "MICRO_CANARY_OUTCOME_FEEDBACK_V11"


def fnum(x: Any, default: float = 0.0) -> float:
    try:
        if x is None:
            return default
        return float(x)
    except Exception:
        return default


def js(x: Any) -> str:
    return json.dumps(x, separators=(",", ":"), ensure_ascii=False, default=str)


class MicroCanaryOutcomeFeedbackV11:
    """Feedback/cooldown from V11 net micro-canary outcomes."""

    def __init__(self, db=None) -> None:
        self.db = db or get_db()

    def ensure_schema(self) -> None:
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS micro_canary_outcome_feedback_v11 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                version TEXT NOT NULL,
                closed_n INTEGER NOT NULL,
                last5_n INTEGER NOT NULL,
                last5_avg_r REAL NOT NULL,
                last5_sum_r REAL NOT NULL,
                last5_winrate REAL NOT NULL,
                last20_avg_r REAL NOT NULL,
                loss_streak INTEGER NOT NULL,
                feedback_state TEXT NOT NULL,
                canary_cooldown INTEGER NOT NULL,
                hard_vetoes TEXT NOT NULL,
                payload TEXT NOT NULL
            );
        """)
        self.db.execute("DROP VIEW IF EXISTS latest_micro_canary_outcome_feedback_v11;")
        self.db.execute("""
            CREATE VIEW latest_micro_canary_outcome_feedback_v11 AS
            SELECT * FROM micro_canary_outcome_feedback_v11
            ORDER BY id DESC LIMIT 1;
        """)

    def qmany(self, sql: str) -> List[Dict[str, Any]]:
        try:
            return [dict(r) for r in self.db.query(sql)]
        except Exception:
            return []

    def refresh(self) -> Dict[str, Any]:
        self.ensure_schema()
        rows = self.qmany("""
            SELECT COALESCE(net_pnl_r, pnl_r, 0.0) AS pnl_r
            FROM paper_micro_canary_positions_v11
            WHERE status='CLOSED'
            ORDER BY id DESC
            LIMIT 20;
        """)
        vals = [fnum(r.get("pnl_r")) for r in rows]
        last5 = vals[:5]
        closed_n = len(vals)
        last5_n = len(last5)
        last5_sum = sum(last5)
        last5_avg = last5_sum / last5_n if last5_n else 0.0
        last5_winrate = sum(1 for x in last5 if x > 0) * 100.0 / last5_n if last5_n else 0.0
        last20_avg = sum(vals) / closed_n if closed_n else 0.0

        loss_streak = 0
        for x in vals:
            if x < 0:
                loss_streak += 1
            else:
                break

        vetoes: List[str] = []
        cooldown = 0
        if closed_n == 0:
            state = "NO_CANARY_HISTORY_OK"
        elif loss_streak >= 2:
            state = "CANARY_COOLDOWN"
            cooldown = 1
            vetoes.append("LOSS_STREAK_GE_2")
        elif last5_n >= 3 and last5_sum <= -1.2:
            state = "CANARY_COOLDOWN"
            cooldown = 1
            vetoes.append("LAST5_SUM_R_TOO_NEGATIVE")
        elif last5_n >= 5 and last5_winrate < 40:
            state = "CANARY_COOLDOWN"
            cooldown = 1
            vetoes.append("LAST5_WINRATE_LT_40")
        elif closed_n >= 8 and last20_avg <= -0.05:
            state = "CANARY_COOLDOWN"
            cooldown = 1
            vetoes.append("LAST20_EXPECTANCY_NEGATIVE")
        else:
            state = "CANARY_FEEDBACK_OK"

        payload = {"last20_net_pnl_r": vals, "paper_only": True}
        self.db.execute("""
            INSERT INTO micro_canary_outcome_feedback_v11 (
                ts, version, closed_n, last5_n, last5_avg_r, last5_sum_r, last5_winrate,
                last20_avg_r, loss_streak, feedback_state, canary_cooldown, hard_vetoes, payload
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """, (
            utc_now_iso(), VERSION, closed_n, last5_n, last5_avg, last5_sum, last5_winrate,
            last20_avg, loss_streak, state, cooldown, js(vetoes), js(payload),
        ))
        return {
            "version": VERSION,
            "feedback_state": state,
            "canary_cooldown": cooldown,
            "closed_n": closed_n,
            "loss_streak": loss_streak,
            "last5_sum_r": round(last5_sum, 4),
            "last20_avg_r": round(last20_avg, 4),
            "hard_vetoes": vetoes,
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    engine = MicroCanaryOutcomeFeedbackV11()
    if args.refresh:
        print(json.dumps(engine.refresh(), indent=2, sort_keys=True))
    else:
        engine.ensure_schema()
        print(json.dumps({"version": VERSION, "schema": "ok"}, indent=2))


if __name__ == "__main__":
    main()
