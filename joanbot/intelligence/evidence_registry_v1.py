from __future__ import annotations

import argparse
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

VERSION = "EVIDENCE_REGISTRY_FOUNDATION_V1_REPAIR"
DB_PATH = Path(os.environ.get("JOANBOT_DB", "data/joanbot_v14.sqlite"))


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def txt(x: Any) -> str:
    return "" if x is None else str(x)


def fnum(x: Any, default: float = 0.0) -> float:
    try:
        if x is None:
            return default
        return float(x)
    except Exception:
        return default


class EvidenceRegistryV1:
    """
    Institutional evidence registry.

    Purpose:
    - classify closed outcomes before statistical evidence uses them
    - exclude legacy / reconciliation / pre-contract outcomes
    - expose clean evidence views
    - avoid raw positions/trades inside decision-impacting evidence

    Does not:
    - open trades
    - change risk
    - change broker
    - change execution
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
            CREATE TABLE IF NOT EXISTS outcome_provenance_v1 (
                position_id TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                setup TEXT NOT NULL,
                opened_at TEXT,
                closed_at TEXT,
                status TEXT,
                pnl_usd REAL,
                provenance TEXT NOT NULL,
                clean_for_evidence INTEGER NOT NULL,
                evidence_weight REAL NOT NULL,
                exclude_reason TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                payload TEXT NOT NULL
            );
        """)

        con.execute("""
            CREATE INDEX IF NOT EXISTS idx_outcome_provenance_v1_key
            ON outcome_provenance_v1(symbol, side, setup, clean_for_evidence);
        """)

        con.execute("""
            CREATE TABLE IF NOT EXISTS evidence_hygiene_summary_v1 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                version TEXT NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                setup TEXT NOT NULL,
                total_closed_positions INTEGER NOT NULL,
                clean_closed_positions INTEGER NOT NULL,
                excluded_positions INTEGER NOT NULL,
                clean_pnl_usd REAL NOT NULL,
                excluded_pnl_usd REAL NOT NULL,
                payload TEXT NOT NULL
            );
        """)

        con.execute("""
            CREATE TABLE IF NOT EXISTS evidence_registry_audit_v1 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                version TEXT NOT NULL,
                event TEXT NOT NULL,
                level TEXT NOT NULL,
                message TEXT NOT NULL,
                payload TEXT NOT NULL
            );
        """)

        con.execute("DROP VIEW IF EXISTS evidence_clean_positions_v1;")
        con.execute("""
            CREATE VIEW evidence_clean_positions_v1 AS
            SELECT p.*
            FROM positions p
            JOIN outcome_provenance_v1 op
              ON op.position_id = p.id
            WHERE op.clean_for_evidence = 1;
        """)

        con.execute("DROP VIEW IF EXISTS evidence_clean_trades_v1;")
        con.execute("""
            CREATE VIEW evidence_clean_trades_v1 AS
            SELECT t.*
            FROM trades t
            JOIN outcome_provenance_v1 op
              ON op.position_id = t.position_id
            WHERE op.clean_for_evidence = 1;
        """)

        con.execute("DROP VIEW IF EXISTS evidence_positions_with_provenance_v1;")
        con.execute("""
            CREATE VIEW evidence_positions_with_provenance_v1 AS
            SELECT
                p.*,
                op.provenance,
                op.clean_for_evidence,
                op.evidence_weight,
                op.exclude_reason
            FROM positions p
            LEFT JOIN outcome_provenance_v1 op
              ON op.position_id = p.id;
        """)

    def audit(self, con: sqlite3.Connection, event: str, level: str, message: str, payload: Dict[str, Any]) -> None:
        con.execute("""
            INSERT INTO evidence_registry_audit_v1 (
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

        con.execute("""
            DELETE FROM evidence_registry_audit_v1
            WHERE id NOT IN (
                SELECT id
                FROM evidence_registry_audit_v1
                ORDER BY id DESC
                LIMIT 200
            );
        """)

    def legacy_cutoff(self, con: sqlite3.Connection) -> Optional[str]:
        row = con.execute("""
            SELECT MAX(ts) AS cutoff
            FROM trades
            WHERE UPPER(COALESCE(reason,'')) LIKE '%LEGACY%'
               OR UPPER(COALESCE(reason,'')) LIKE '%RECONCILIATION%'
               OR UPPER(COALESCE(reason,'')) LIKE '%PRE_CONTRACT%';
        """).fetchone()
        if not row:
            return None
        return row["cutoff"] if row["cutoff"] else None

    def trades_for_position(self, con: sqlite3.Connection, position_id: str) -> List[sqlite3.Row]:
        return list(con.execute("""
            SELECT id, ts, position_id, symbol, side, setup, pnl_usd, pnl_r, fees, reason, payload
            FROM trades
            WHERE position_id=?
            ORDER BY ts ASC, id ASC;
        """, (position_id,)).fetchall())

    def classify_position(self, con: sqlite3.Connection, p: sqlite3.Row, cutoff: Optional[str]) -> Dict[str, Any]:
        position_id = txt(p["id"])
        opened_at = txt(p["opened_at"])
        closed_at = txt(p["closed_at"])
        status = txt(p["status"]).upper()
        pnl_usd = fnum(p["pnl_usd"], 0.0)

        trades = self.trades_for_position(con, position_id)
        reasons = [txt(t["reason"]).upper() for t in trades]
        trade_ids = [t["id"] for t in trades]

        has_legacy = any(
            "LEGACY" in r or "RECONCILIATION" in r or "PRE_CONTRACT" in r
            for r in reasons
        )

        pre_contract_by_time = False
        if cutoff:
            if opened_at and opened_at <= cutoff:
                pre_contract_by_time = True
            if closed_at and closed_at <= cutoff:
                pre_contract_by_time = True

        if status != "CLOSED":
            provenance = "OPEN_OR_NON_CLOSED"
            clean = 0
            weight = 0.0
            exclude_reason = "NOT_A_CLOSED_OUTCOME"
        elif has_legacy:
            provenance = "LEGACY_RECONCILIATION_PRE_CONTRACT"
            clean = 0
            weight = 0.0
            exclude_reason = "TRADE_REASON_LEGACY_OR_RECONCILIATION"
        elif pre_contract_by_time:
            provenance = "PRE_CONTRACT_EXCLUDED"
            clean = 0
            weight = 0.0
            exclude_reason = "OPENED_OR_CLOSED_BEFORE_EVIDENCE_BASELINE"
        elif not trades:
            provenance = "UNKNOWN_NO_TRADE_ROWS"
            clean = 0
            weight = 0.0
            exclude_reason = "NO_MATCHING_TRADE_ROWS"
        else:
            provenance = "CLEAN_EXECUTION_CANDIDATE"
            clean = 1
            weight = 1.0
            exclude_reason = "POST_BASELINE_NON_LEGACY_EXECUTION"

        return {
            "position_id": position_id,
            "symbol": txt(p["symbol"]),
            "side": txt(p["side"]),
            "setup": txt(p["setup"]),
            "opened_at": opened_at,
            "closed_at": closed_at,
            "status": status,
            "pnl_usd": round(pnl_usd, 8),
            "provenance": provenance,
            "clean_for_evidence": clean,
            "evidence_weight": weight,
            "exclude_reason": exclude_reason,
            "updated_at": utc_now_iso(),
            "payload": {
                "version": VERSION,
                "legacy_cutoff": cutoff,
                "trade_ids": trade_ids,
                "trade_reasons": reasons,
                "trade_count": len(trades),
                "rule": "only post-baseline non-legacy closed executions are clean evidence",
            },
        }

    def upsert(self, con: sqlite3.Connection, r: Dict[str, Any]) -> None:
        con.execute("""
            INSERT OR REPLACE INTO outcome_provenance_v1 (
                position_id, symbol, side, setup, opened_at, closed_at, status, pnl_usd,
                provenance, clean_for_evidence, evidence_weight, exclude_reason, updated_at, payload
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """, (
            r["position_id"],
            r["symbol"],
            r["side"],
            r["setup"],
            r["opened_at"],
            r["closed_at"],
            r["status"],
            r["pnl_usd"],
            r["provenance"],
            r["clean_for_evidence"],
            r["evidence_weight"],
            r["exclude_reason"],
            r["updated_at"],
            json.dumps(r["payload"], separators=(",", ":"), ensure_ascii=False),
        ))

    def refresh_summary(self, con: sqlite3.Connection) -> None:
        con.execute("""
            INSERT INTO evidence_hygiene_summary_v1 (
                ts, version, symbol, side, setup,
                total_closed_positions, clean_closed_positions, excluded_positions,
                clean_pnl_usd, excluded_pnl_usd, payload
            )
            SELECT
                ?,
                ?,
                symbol,
                side,
                setup,
                COUNT(*),
                SUM(CASE WHEN clean_for_evidence=1 THEN 1 ELSE 0 END),
                SUM(CASE WHEN clean_for_evidence=0 THEN 1 ELSE 0 END),
                ROUND(SUM(CASE WHEN clean_for_evidence=1 THEN pnl_usd ELSE 0 END),8),
                ROUND(SUM(CASE WHEN clean_for_evidence=0 THEN pnl_usd ELSE 0 END),8),
                json_object(
                    'version', ?,
                    'rule', 'clean evidence excludes legacy/pre-contract/reconciliation/unknown outcomes'
                )
            FROM outcome_provenance_v1
            WHERE status='CLOSED'
            GROUP BY symbol, side, setup;
        """, (utc_now_iso(), VERSION, VERSION))

        con.execute("""
            DELETE FROM evidence_hygiene_summary_v1
            WHERE id NOT IN (
                SELECT id
                FROM evidence_hygiene_summary_v1
                ORDER BY id DESC
                LIMIT 200
            );
        """)

    def refresh(self) -> Dict[str, Any]:
        with self.connect() as con:
            self.ensure_schema(con)
            cutoff = self.legacy_cutoff(con)

            positions = list(con.execute("""
                SELECT *
                FROM positions
                WHERE status='CLOSED'
                ORDER BY closed_at ASC;
            """).fetchall())

            classified = []
            for p in positions:
                r = self.classify_position(con, p, cutoff)
                self.upsert(con, r)
                classified.append(r)

            self.refresh_summary(con)

            result = {
                "version": VERSION,
                "legacy_cutoff": cutoff,
                "classified_positions": len(classified),
                "clean_for_evidence": sum(1 for r in classified if r["clean_for_evidence"] == 1),
                "excluded_from_evidence": sum(1 for r in classified if r["clean_for_evidence"] == 0),
            }

            self.audit(con, "EVIDENCE_REGISTRY_REFRESH", "INFO", "Evidence registry refreshed", result)
            con.commit()
            return result

    def summary_rows(self) -> List[Dict[str, Any]]:
        with self.connect() as con:
            self.ensure_schema(con)
            return [
                dict(r)
                for r in con.execute("""
                    SELECT provenance, clean_for_evidence, COUNT(*) AS n, ROUND(SUM(pnl_usd),2) AS pnl_usd
                    FROM outcome_provenance_v1
                    GROUP BY provenance, clean_for_evidence
                    ORDER BY clean_for_evidence ASC, n DESC;
                """).fetchall()
            ]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--summary", action="store_true")
    args = parser.parse_args()

    registry = EvidenceRegistryV1()

    if args.refresh:
        print(json.dumps(registry.refresh(), indent=2, sort_keys=True))

    if args.summary:
        for row in registry.summary_rows():
            print(row)


if __name__ == "__main__":
    main()
