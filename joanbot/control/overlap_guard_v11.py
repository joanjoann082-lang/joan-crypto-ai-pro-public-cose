from __future__ import annotations

import argparse
import json
from typing import Any, Dict, List

from joanbot.storage.db import get_db
from joanbot.utils import utc_now_iso

VERSION = "OVERLAP_GUARD_V11_SINGLE_PIPELINE"


def inum(x: Any, default: int = 0) -> int:
    try:
        if x is None:
            return default
        return int(float(x))
    except Exception:
        return default


def js(x: Any) -> str:
    return json.dumps(x, separators=(",", ":"), ensure_ascii=False, sort_keys=True, default=str)


class OverlapGuardV11:
    """
    Prevents parallel execution authorities from overlapping.

    V11 may read older V9/V10 tables, but it must not open a V11 canary when
    legacy positions or older canary bridges are already active. This is an
    execution-level guard, not a cosmetic audit.
    """

    def __init__(self, db=None) -> None:
        self.db = db or get_db()

    def ensure_schema(self) -> None:
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS overlap_guard_v11 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                version TEXT NOT NULL,
                guard_state TEXT NOT NULL,
                allow_v11_pipeline INTEGER NOT NULL,
                open_legacy_positions INTEGER NOT NULL,
                open_v9_canaries INTEGER NOT NULL,
                open_v10_canaries INTEGER NOT NULL,
                open_v11_canaries INTEGER NOT NULL,
                legacy_decisions_count INTEGER NOT NULL,
                legacy_positions_count INTEGER NOT NULL,
                legacy_trades_count INTEGER NOT NULL,
                hard_vetoes TEXT NOT NULL,
                payload TEXT NOT NULL
            );
        """)
        self.db.execute("DROP VIEW IF EXISTS latest_overlap_guard_v11;")
        self.db.execute("""
            CREATE VIEW latest_overlap_guard_v11 AS
            SELECT * FROM overlap_guard_v11
            ORDER BY id DESC LIMIT 1;
        """)

    def q1(self, sql: str) -> Dict[str, Any]:
        try:
            rows = self.db.query(sql)
            return dict(rows[0]) if rows else {}
        except Exception:
            return {}

    def count(self, sql: str) -> int:
        return inum(self.q1(sql).get("n"))

    def refresh(self) -> Dict[str, Any]:
        self.ensure_schema()
        open_legacy = self.count("SELECT COUNT(*) AS n FROM positions WHERE status='OPEN';")
        open_v9 = self.count("SELECT COUNT(*) AS n FROM paper_micro_canary_positions_v9 WHERE status='OPEN';")
        open_v10 = self.count("SELECT COUNT(*) AS n FROM paper_micro_canary_positions_v10 WHERE status='OPEN';")
        open_v11 = self.count("SELECT COUNT(*) AS n FROM paper_micro_canary_positions_v11 WHERE status='OPEN';")
        dec_n = self.count("SELECT COUNT(*) AS n FROM decisions;")
        pos_n = self.count("SELECT COUNT(*) AS n FROM positions;")
        tr_n = self.count("SELECT COUNT(*) AS n FROM trades;")

        vetoes: List[str] = []
        if open_legacy > 0:
            vetoes.append("OPEN_LEGACY_POSITION_EXISTS")
        if open_v9 > 0:
            vetoes.append("OPEN_V9_CANARY_EXISTS")
        if open_v10 > 0:
            vetoes.append("OPEN_V10_CANARY_EXISTS")
        if open_v11 > 1:
            vetoes.append("MULTIPLE_V11_CANARIES_ACTIVE")

        if vetoes:
            state = "OVERLAP_BLOCK"
            allow = 0
        else:
            state = "SINGLE_PIPELINE_OK"
            allow = 1

        payload = {
            "paper_only": True,
            "v11_can_read_v9_v10_but_must_not_overlap_execution": True,
            "legacy_tables_protected": ["decisions", "positions", "trades"],
        }
        self.db.execute("""
            INSERT INTO overlap_guard_v11 (
                ts, version, guard_state, allow_v11_pipeline,
                open_legacy_positions, open_v9_canaries, open_v10_canaries, open_v11_canaries,
                legacy_decisions_count, legacy_positions_count, legacy_trades_count,
                hard_vetoes, payload
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """, (
            utc_now_iso(), VERSION, state, allow,
            open_legacy, open_v9, open_v10, open_v11,
            dec_n, pos_n, tr_n, js(vetoes), js(payload),
        ))
        return {
            "version": VERSION,
            "guard_state": state,
            "allow_v11_pipeline": allow,
            "open_legacy_positions": open_legacy,
            "open_v9_canaries": open_v9,
            "open_v10_canaries": open_v10,
            "open_v11_canaries": open_v11,
            "hard_vetoes": vetoes,
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    engine = OverlapGuardV11()
    if args.refresh:
        print(json.dumps(engine.refresh(), indent=2, sort_keys=True, default=str))
    else:
        engine.ensure_schema()
        print(json.dumps({"version": VERSION, "schema": "ok"}, indent=2))


if __name__ == "__main__":
    main()
