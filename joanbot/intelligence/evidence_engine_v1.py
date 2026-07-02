from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional, Tuple

from ..storage import get_db

VERSION = "EVIDENCE_ENGINE_V1_1_R_NORMALIZED"

PRIMARY_FORWARD_ACTIONS = {"OPEN", "PROBE"}
SHADOW_FORWARD_ACTIONS = {"WAIT"}
LEGACY_REASONS = {"LEGACY_RECONCILIATION_PRE_CONTRACT"}


def fnum(x: Any, default: float = 0.0) -> float:
    try:
        if x is None:
            return default
        return float(x)
    except Exception:
        return default


def maybe_float(x: Any) -> Optional[float]:
    try:
        if x is None:
            return None
        return float(x)
    except Exception:
        return None


def profit_factor(vals: List[float]) -> Optional[float]:
    gross_profit = sum(v for v in vals if v > 0)
    gross_loss = abs(sum(v for v in vals if v < 0))
    if gross_loss <= 0:
        return 999.0 if gross_profit > 0 else None
    return gross_profit / gross_loss


def wilson_lcb(wins: int, n: int, z: float = 1.64) -> float:
    if n <= 0:
        return 0.0
    phat = wins / n
    denom = 1 + z * z / n
    centre = phat + z * z / (2 * n)
    spread = z * math.sqrt((phat * (1 - phat) + z * z / (4 * n)) / n)
    return max(0.0, (centre - spread) / denom)


def max_drawdown(vals: List[float]) -> float:
    equity = 0.0
    peak = 0.0
    dd = 0.0
    for v in vals:
        equity += v
        peak = max(peak, equity)
        dd = min(dd, equity - peak)
    return abs(dd)


def stats(vals: List[float]) -> Dict[str, Any]:
    n = len(vals)
    wins = sum(1 for v in vals if v > 0)
    losses = sum(1 for v in vals if v < 0)
    flats = sum(1 for v in vals if v == 0)
    total = sum(vals)
    pf = profit_factor(vals)

    return {
        "n": n,
        "wins": wins,
        "losses": losses,
        "flats": flats,
        "sum": round(total, 6),
        "expectancy": round(total / n, 6) if n else 0.0,
        "profit_factor": round(pf, 4) if isinstance(pf, float) else pf,
        "winrate_pct": round(wins / n * 100, 2) if n else 0.0,
        "winrate_lcb": round(wilson_lcb(wins, n), 6),
        "max_drawdown": round(max_drawdown(vals), 6),
        "best": round(max(vals), 6) if vals else None,
        "worst": round(min(vals), 6) if vals else None,
    }


def parse_json(raw: Any) -> Dict[str, Any]:
    try:
        if isinstance(raw, dict):
            return raw
        if raw is None:
            return {}
        return json.loads(str(raw))
    except Exception:
        return {}


def deep_get(d: Dict[str, Any], paths: List[List[str]], default: Any = None) -> Any:
    for path in paths:
        cur: Any = d
        ok = True
        for k in path:
            if not isinstance(cur, dict) or k not in cur:
                ok = False
                break
            cur = cur[k]
        if ok:
            return cur
    return default


def calc_position_r(row: Dict[str, Any]) -> Optional[float]:
    """
    Institutional R calculation:

    R = realized pnl / initial risk

    initial risk USD = size_usd * abs(entry - initial_stop) / entry

    Initial stop is preferably taken from original decision metadata.
    If stop cannot be recovered, R is unavailable.
    """
    payload = parse_json(row.get("payload"))

    entry = fnum(
        row.get("entry"),
        fnum(
            deep_get(payload, [["entry"], ["entry_price"], ["meta", "decision", "entry"]]),
            0.0,
        ),
    )

    size = fnum(
        row.get("size_usd"),
        fnum(deep_get(payload, [["size_usd"], ["meta", "decision", "size_usd"]]), 0.0),
    )

    pnl = maybe_float(row.get("evidence_pnl"))
    if pnl is None:
        pnl = maybe_float(row.get("pnl_usd"))

    stop = fnum(
        deep_get(
            payload,
            [
                ["meta", "decision", "stop_loss"],
                ["decision", "stop_loss"],
                ["initial_stop_loss"],
                ["initial_sl"],
                ["stop_loss"],
                ["sl"],
            ],
        ),
        0.0,
    )

    if pnl is None or entry <= 0 or size <= 0 or stop <= 0:
        return None

    risk_pct = abs(entry - stop) / entry
    initial_risk_usd = size * risk_pct

    if initial_risk_usd <= 0:
        return None

    return pnl / initial_risk_usd


@dataclass
class EvidencePack:
    version: str
    symbol: str
    side: str
    setup: str
    position: Dict[str, Any]
    forward_primary: Dict[str, Any]
    forward_shadow: Dict[str, Any]
    edge_memory: Dict[str, Any]
    effective_n: float
    source_health: Dict[str, Any]
    samples: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class EvidenceEngineV1:
    """
    Single read-only evidence source.

    Owns:
    - closed position evidence
    - R-normalization
    - forward primary evidence
    - forward shadow evidence
    - edge memory normalization
    - schema/source health

    Does not own:
    - trade decisions
    - risk sizing
    - execution checks
    - position management
    - dashboard rendering
    - Telegram commands
    """

    def __init__(self, db: Any | None = None):
        self.db = db or get_db()

    def q(self, sql: str, params: Tuple[Any, ...] = ()) -> List[Dict[str, Any]]:
        try:
            return self.db.query(sql, params)
        except TypeError:
            try:
                return self.db.query(sql)
            except Exception:
                return []
        except Exception:
            return []

    def table_exists(self, table: str) -> bool:
        rows = self.q(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        )
        return bool(rows)

    def columns(self, table: str) -> set[str]:
        rows = self.q(f"PRAGMA table_info({table})")
        return {str(r.get("name")) for r in rows if isinstance(r, dict)}

    def position_rows(self, symbol: str, side: str, setup: str) -> Tuple[str, List[Dict[str, Any]]]:
        def run(where_sql: str, params: Tuple[Any, ...]) -> List[Dict[str, Any]]:
            rows = self.q(
                f"""
                SELECT
                    p.id AS position_id,
                    p.symbol,
                    p.side,
                    p.setup,
                    p.status,
                    p.opened_at,
                    p.closed_at,
                    p.entry,
                    p.exit,
                    p.size_usd,
                    p.pnl_usd,
                    p.payload,
                    COALESCE(SUM(CASE
                        WHEN COALESCE(t.reason,'') NOT IN ('LEGACY_RECONCILIATION_PRE_CONTRACT')
                        THEN COALESCE(t.pnl_usd,0)
                        ELSE 0
                    END),0) AS trade_pnl_usd,
                    SUM(CASE
                        WHEN COALESCE(t.reason,'') IN ('LEGACY_RECONCILIATION_PRE_CONTRACT')
                        THEN 1 ELSE 0
                    END) AS legacy_trade_rows,
                    COUNT(t.id) AS trade_rows
                FROM evidence_clean_positions_v1 p
                LEFT JOIN evidence_clean_trades_v1 t ON t.position_id = p.id
                WHERE p.status='CLOSED'
                  AND {where_sql}
                GROUP BY p.id
                ORDER BY p.closed_at ASC, p.opened_at ASC
                """,
                params,
            )

            clean: List[Dict[str, Any]] = []

            for r in rows:
                payload = str(r.get("payload") or "")
                if fnum(r.get("legacy_trade_rows"), 0) > 0:
                    continue
                if "LEGACY_RECONCILIATION_PRE_CONTRACT" in payload:
                    continue

                pnl = maybe_float(r.get("pnl_usd"))
                if pnl is None:
                    pnl = fnum(r.get("trade_pnl_usd"), 0.0)

                x = dict(r)
                x["evidence_pnl"] = fnum(pnl, 0.0)
                x["evidence_r"] = calc_position_r(x)
                clean.append(x)

            return clean

        exact = run("p.symbol=? AND p.side=? AND p.setup=?", (symbol, side, setup))
        side_setup = run("p.side=? AND p.setup=?", (side, setup))
        setup_rows = run("p.setup=?", (setup,))

        if len(exact) >= 3:
            return "SYMBOL_SIDE_SETUP", exact
        if len(side_setup) >= 6:
            return "SIDE_SETUP", side_setup
        if len(setup_rows) >= 8:
            return "SETUP", setup_rows
        if exact:
            return "SYMBOL_SIDE_SETUP_LOW_N", exact
        if side_setup:
            return "SIDE_SETUP_LOW_N", side_setup
        return "SETUP_LOW_N", setup_rows

    def forward_rows(self, symbol: str, side: str, setup: str, health: Dict[str, Any]) -> List[Dict[str, Any]]:
        if not health.get("forward_join_schema_usable"):
            return []

        return self.q(
            """
            SELECT
                fr.case_id,
                COALESCE(fr.resolved_at, fc.due_at, fc.created_at) AS ts,
                COALESCE(fr.symbol, fc.symbol) AS symbol,
                fc.side AS side,
                fc.setup AS setup,
                fc.action AS action,
                fr.outcome AS outcome,
                fr.result_r AS result_r,
                fr.mfe_r AS mfe_r,
                fr.mae_r AS mae_r
            FROM forward_results fr
            JOIN forward_cases fc ON fr.case_id = fc.id
            WHERE COALESCE(fr.symbol, fc.symbol)=?
              AND fc.side=?
              AND fc.setup=?
            ORDER BY COALESCE(fr.resolved_at, fc.due_at, fc.created_at) DESC
            LIMIT 1500
            """,
            (symbol, side, setup),
        )

    @staticmethod
    def normalize_edge_memory(edge_memory: Dict[str, Any] | None) -> Dict[str, Any]:
        edge_memory = edge_memory or {}
        return {
            "status": str(edge_memory.get("status", "INSUFFICIENT") or "INSUFFICIENT"),
            "n": fnum(edge_memory.get("n"), 0.0),
            "effective_n": fnum(edge_memory.get("effective_n", edge_memory.get("n")), 0.0),
            "expectancy_r": fnum(edge_memory.get("expectancy_r", edge_memory.get("expectancy")), 0.0),
            "profit_factor": edge_memory.get("profit_factor"),
            "lcb": fnum(edge_memory.get("lcb"), 0.0),
        }

    def build(self, symbol: str, side: str, setup: str, edge_memory: Dict[str, Any] | None = None) -> Dict[str, Any]:
        symbol = str(symbol or "").upper()
        side = str(side or "").upper()
        setup = str(setup or "").upper()

        health = {
            "positions_table": self.table_exists("positions"),
            "trades_table": self.table_exists("trades"),
            "forward_results_table": self.table_exists("forward_results"),
            "forward_cases_table": self.table_exists("forward_cases"),
            "forward_join_schema_usable": False,
        }

        if health["forward_results_table"] and health["forward_cases_table"]:
            fr_cols = self.columns("forward_results")
            fc_cols = self.columns("forward_cases")
            health["forward_join_schema_usable"] = (
                {"case_id", "result_r"}.issubset(fr_cols)
                and {"id", "symbol", "side", "setup", "action"}.issubset(fc_cols)
            )

        if health["positions_table"] and health["trades_table"]:
            position_scope, position_rows = self.position_rows(symbol, side, setup)
        else:
            position_scope, position_rows = "NO_POSITION_SCHEMA", []

        usd_vals = [fnum(r.get("evidence_pnl"), 0.0) for r in position_rows]
        r_vals = [
            fnum(r.get("evidence_r"), 0.0)
            for r in position_rows
            if r.get("evidence_r") is not None
        ]

        forward_rows = self.forward_rows(symbol, side, setup, health)

        forward_primary_vals = [
            fnum(r.get("result_r"), 0.0)
            for r in forward_rows
            if str(r.get("action") or "").upper() in PRIMARY_FORWARD_ACTIONS
        ]

        forward_shadow_vals = [
            fnum(r.get("result_r"), 0.0)
            for r in forward_rows
            if str(r.get("action") or "").upper() in SHADOW_FORWARD_ACTIONS
        ]

        position_r_stats = stats(r_vals)
        position_usd_stats = stats(usd_vals)
        forward_primary_stats = stats(forward_primary_vals)
        forward_shadow_stats = stats(forward_shadow_vals)
        mem = self.normalize_edge_memory(edge_memory)

        r_valid = len(r_vals)
        r_missing = max(0, len(position_rows) - r_valid)

        health["position_r_valid"] = r_valid
        health["position_r_missing"] = r_missing
        health["position_metric_primary"] = "R" if r_valid > 0 else "USD_FALLBACK"

        effective_n = (
            r_valid * 1.00
            + forward_primary_stats["n"] * 0.35
            + forward_shadow_stats["n"] * 0.05
            + mem["effective_n"] * 0.15
        )

        return EvidencePack(
            version=VERSION,
            symbol=symbol,
            side=side,
            setup=setup,
            position={
                "scope": position_scope,
                "r": position_r_stats,
                "usd": position_usd_stats,
                "r_valid": r_valid,
                "r_missing": r_missing,
                "metric_primary": "R" if r_valid > 0 else "USD_FALLBACK",
            },
            forward_primary=forward_primary_stats,
            forward_shadow=forward_shadow_stats,
            edge_memory=mem,
            effective_n=round(effective_n, 6),
            source_health=health,
            samples={
                "positions": [
                    {
                        "position_id": r.get("position_id"),
                        "opened_at": r.get("opened_at"),
                        "closed_at": r.get("closed_at"),
                        "symbol": r.get("symbol"),
                        "side": r.get("side"),
                        "setup": r.get("setup"),
                        "pnl_usd": r.get("evidence_pnl"),
                        "r": r.get("evidence_r"),
                    }
                    for r in position_rows[-8:]
                ],
                "forward": [
                    {
                        "case_id": r.get("case_id"),
                        "ts": r.get("ts"),
                        "symbol": r.get("symbol"),
                        "side": r.get("side"),
                        "setup": r.get("setup"),
                        "action": r.get("action"),
                        "result_r": r.get("result_r"),
                        "outcome": r.get("outcome"),
                    }
                    for r in forward_rows[:8]
                ],
            },
        ).to_dict()


def main() -> None:
    engine = EvidenceEngineV1()
    tests = [
        ("BTCUSDT", "LONG", "CAPITULATION_REBOUND_LONG"),
        ("ETHUSDT", "LONG", "CAPITULATION_REBOUND_LONG"),
        ("BTCUSDT", "SHORT", "TREND_BOUNCE_SHORT"),
        ("ETHUSDT", "SHORT", "TREND_BOUNCE_SHORT"),
    ]

    for symbol, side, setup in tests:
        e = engine.build(symbol, side, setup, {})
        pr = e["position"]["r"]
        pu = e["position"]["usd"]
        fp = e["forward_primary"]
        fs = e["forward_shadow"]
        print(
            symbol,
            side,
            setup,
            "scope=", e["position"]["scope"],
            "r_n=", pr["n"],
            "r_exp=", pr["expectancy"],
            "r_pf=", pr["profit_factor"],
            "usd_n=", pu["n"],
            "usd_exp=", pu["expectancy"],
            "fw_primary_n=", fp["n"],
            "fw_primary_exp=", fp["expectancy"],
            "fw_shadow_n=", fs["n"],
            "fw_shadow_exp=", fs["expectancy"],
            "eff_n=", e["effective_n"],
            "join=", e["source_health"]["forward_join_schema_usable"],
        )


if __name__ == "__main__":
    main()
