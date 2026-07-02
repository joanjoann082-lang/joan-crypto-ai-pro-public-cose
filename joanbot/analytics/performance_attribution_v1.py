from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple


DB_PATH = Path("data/joanbot_v14.sqlite")
REPORT_PATH = Path("data/reports/performance_attribution_v1_report.json")
BASELINE_PATH = Path("data/performance_baseline_v1.json")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def fnum(x: Any, default: float = 0.0) -> float:
    try:
        if x is None:
            return default
        return float(x)
    except Exception:
        return default


def safe_json(x: Any) -> Dict[str, Any]:
    if not x:
        return {}
    try:
        return json.loads(x)
    except Exception:
        return {}


def connect() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def fetch(con: sqlite3.Connection, query: str, args: Tuple[Any, ...] = ()) -> List[Dict[str, Any]]:
    return [dict(r) for r in con.execute(query, args).fetchall()]


def one(con: sqlite3.Connection, query: str, args: Tuple[Any, ...] = (), default: Any = 0) -> Any:
    row = con.execute(query, args).fetchone()
    if not row:
        return default
    return row[0]


@dataclass
class MetricBlock:
    n: int
    wins: int
    losses: int
    flats: int
    winrate_pct: float
    pnl_usd: float
    avg_pnl_usd: float
    fees_usd: float
    gross_profit_usd: float
    gross_loss_usd: float
    profit_factor: float | None
    expectancy_usd: float
    best_trade_usd: float | None
    worst_trade_usd: float | None


def calc_metrics(rows: Iterable[Dict[str, Any]]) -> MetricBlock:
    items = list(rows)
    pnls = [fnum(x.get("pnl_usd")) for x in items]
    fees = [fnum(x.get("fees")) for x in items]

    wins = [x for x in pnls if x > 0]
    losses = [x for x in pnls if x < 0]
    flats = [x for x in pnls if x == 0]

    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    total = sum(pnls)
    n = len(items)

    return MetricBlock(
        n=n,
        wins=len(wins),
        losses=len(losses),
        flats=len(flats),
        winrate_pct=round(len(wins) / n * 100, 2) if n else 0.0,
        pnl_usd=round(total, 4),
        avg_pnl_usd=round(total / n, 4) if n else 0.0,
        fees_usd=round(sum(fees), 4),
        gross_profit_usd=round(gross_profit, 4),
        gross_loss_usd=round(gross_loss, 4),
        profit_factor=round(gross_profit / gross_loss, 4) if gross_loss > 0 else None,
        expectancy_usd=round(total / n, 4) if n else 0.0,
        best_trade_usd=round(max(pnls), 4) if pnls else None,
        worst_trade_usd=round(min(pnls), 4) if pnls else None,
    )


def group_by(rows_: List[Dict[str, Any]], keys: List[str]) -> List[Dict[str, Any]]:
    groups: Dict[Tuple[str, ...], List[Dict[str, Any]]] = defaultdict(list)

    for r in rows_:
        k = tuple(str(r.get(key, "UNKNOWN")) for key in keys)
        groups[k].append(r)

    out = []
    for k, vals in groups.items():
        d = {key: value for key, value in zip(keys, k)}
        d.update(asdict(calc_metrics(vals)))
        d["authority"] = classify_authority(d)
        out.append(d)

    return sorted(out, key=lambda x: (x["pnl_usd"], x["n"]))


def classify_authority(m: Dict[str, Any]) -> Dict[str, Any]:
    n = int(m.get("n", 0))
    pnl = fnum(m.get("pnl_usd"))
    exp = fnum(m.get("expectancy_usd"))
    pf = m.get("profit_factor")
    winrate = fnum(m.get("winrate_pct"))

    if n < 6:
        return {
            "status": "INSUFFICIENT_SAMPLE",
            "action": "NO_PROMOTION",
            "reason": "sample below minimum",
        }

    if pnl < 0 and exp < 0:
        return {
            "status": "NEGATIVE_EDGE",
            "action": "REDUCE_OR_BLOCK",
            "reason": "negative realized expectancy",
        }

    if pf is not None and pf >= 1.25 and exp > 0 and winrate >= 45:
        return {
            "status": "PROMISING",
            "action": "ALLOW_SMALL_SIZE_ONLY",
            "reason": "positive expectancy but still requires more sample",
        }

    if pf is not None and pf >= 1.5 and exp > 0 and n >= 30:
        return {
            "status": "VALIDATED_CANDIDATE",
            "action": "ALLOW_NORMAL_SIZE_REVIEW",
            "reason": "higher sample and positive profile",
        }

    return {
        "status": "MIXED",
        "action": "WATCH_ONLY",
        "reason": "not enough positive evidence",
    }


def create_baseline_if_missing(con: sqlite3.Connection) -> Dict[str, Any]:
    if BASELINE_PATH.exists():
        return json.loads(BASELINE_PATH.read_text())

    baseline = {
        "version": "PERFORMANCE_BASELINE_V1",
        "created_at": utc_now(),
        "reason": "baseline after Execution Contract V1",
        "max_trade_id": one(con, "SELECT COALESCE(MAX(id),0) FROM trades"),
        "max_position_rowid": one(con, "SELECT COALESCE(MAX(rowid),0) FROM positions"),
        "max_decision_id": one(con, "SELECT COALESCE(MAX(id),0) FROM decisions"),
        "max_runtime_event_id": one(con, "SELECT COALESCE(MAX(id),0) FROM runtime_events"),
        "max_alert_id": one(con, "SELECT COALESCE(MAX(id),0) FROM alerts"),
    }

    BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
    BASELINE_PATH.write_text(json.dumps(baseline, indent=2, sort_keys=True), encoding="utf-8")
    return baseline


def open_conflicts(open_positions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_symbol: Dict[str, List[Dict[str, Any]]] = defaultdict(list)

    for p in open_positions:
        by_symbol[str(p.get("symbol"))].append(p)

    conflicts = []
    for symbol, positions in by_symbol.items():
        sides = sorted(set(str(p.get("side")) for p in positions))
        setups = sorted(set(str(p.get("setup")) for p in positions))

        if len(positions) > 1 or len(sides) > 1:
            conflicts.append({
                "symbol": symbol,
                "open_positions": len(positions),
                "sides": sides,
                "setups": setups,
                "position_ids": [p.get("id") for p in positions],
            })

    return conflicts


def main() -> None:
    con = connect()
    baseline = create_baseline_if_missing(con)

    all_trades = fetch(con, "SELECT * FROM trades ORDER BY id")
    post_trades = fetch(con, "SELECT * FROM trades WHERE id > ? ORDER BY id", (baseline["max_trade_id"],))

    positions = fetch(con, """
        SELECT rowid, id, opened_at, closed_at, symbol, side, setup, status, entry, exit, size_usd, pnl_usd, payload
        FROM positions
        ORDER BY opened_at
    """)

    open_positions = [p for p in positions if str(p.get("status")) == "OPEN"]
    closed_positions = [p for p in positions if str(p.get("status")) == "CLOSED"]

    recent_decisions = fetch(con, """
        SELECT id, ts, symbol, action, side, setup, final_score, confidence, size_usd
        FROM decisions
        ORDER BY id DESC
        LIMIT 100
    """)

    rejected_executions = fetch(con, """
        SELECT id, ts, component, level, message, payload
        FROM runtime_events
        WHERE message='EXECUTION_REJECTED'
        ORDER BY id DESC
        LIMIT 100
    """)

    report = {
        "version": "PERFORMANCE_ATTRIBUTION_V1",
        "created_at": utc_now(),
        "db_path": str(DB_PATH),
        "baseline": baseline,
        "all_trades": asdict(calc_metrics(all_trades)),
        "post_baseline_trades": asdict(calc_metrics(post_trades)),
        "closed_positions_pnl_usd": round(sum(fnum(p.get("pnl_usd")) for p in closed_positions), 4),
        "open_positions_count": len(open_positions),
        "open_conflicts": open_conflicts(open_positions),
        "by_symbol_side_setup": group_by(all_trades, ["symbol", "side", "setup"]),
        "by_symbol_side_setup_reason": group_by(all_trades, ["symbol", "side", "setup", "reason"]),
        "recent_decisions": recent_decisions,
        "recent_execution_rejections": rejected_executions,
        "next_required_layer": "SETUP_AUTHORITY_V1",
    }

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")

    print("PERFORMANCE_ATTRIBUTION_V1_OK")
    print("baseline_max_trade_id:", baseline["max_trade_id"])
    print("all_trades:", report["all_trades"])
    print("post_baseline_trades:", report["post_baseline_trades"])
    print("open_conflicts:", len(report["open_conflicts"]))
    print("report:", REPORT_PATH)

    print("\nSETUP AUTHORITY INPUT")
    for row in report["by_symbol_side_setup"]:
        a = row["authority"]
        print(
            row["symbol"],
            row["side"],
            row["setup"],
            "n=", row["n"],
            "pnl=", row["pnl_usd"],
            "pf=", row["profit_factor"],
            "exp=", row["expectancy_usd"],
            "authority=", a["action"],
            "status=", a["status"],
        )


if __name__ == "__main__":
    main()
