from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List


DB = Path("data/joanbot_v14.sqlite")
REPORT_DIR = Path("data/reports")
BASELINE_PATH = Path("data/state_integrity_baseline_v1.json")

RECON_REASON = "LEGACY_RECONCILIATION_PRE_CONTRACT"
EVENT_COMPONENT = "state_integrity"
VERSION = "STATE_INTEGRITY_V1"


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
    try:
        return json.loads(x) if x else {}
    except Exception:
        return {}


def parse_ts(x: Any):
    try:
        return datetime.fromisoformat(str(x).replace("Z", "+00:00"))
    except Exception:
        return None


def connect() -> sqlite3.Connection:
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    return con


def fetch(con: sqlite3.Connection, q: str, args=()) -> List[Dict[str, Any]]:
    return [dict(r) for r in con.execute(q, args).fetchall()]


def one(con: sqlite3.Connection, q: str, args=(), default=0):
    r = con.execute(q, args).fetchone()
    return r[0] if r else default


def ensure_schema(con: sqlite3.Connection) -> None:
    with con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS state_integrity_events(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT,
                version TEXT,
                event TEXT,
                plan_hash TEXT,
                status TEXT,
                payload TEXT
            )
        """)
        con.execute("CREATE INDEX IF NOT EXISTS idx_state_integrity_ts ON state_integrity_events(ts)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_state_integrity_hash ON state_integrity_events(plan_hash)")


def latest_prices(con: sqlite3.Connection) -> Dict[str, Dict[str, Any]]:
    rows = fetch(con, """
        SELECT m.symbol, m.price, m.ts
        FROM market_snapshots m
        JOIN (
            SELECT symbol, MAX(rowid) AS rowid
            FROM market_snapshots
            GROUP BY symbol
        ) x ON x.rowid = m.rowid
    """)
    return {str(r["symbol"]): {"price": fnum(r["price"]), "ts": r["ts"]} for r in rows}


def open_positions(con: sqlite3.Connection) -> List[Dict[str, Any]]:
    return fetch(con, """
        SELECT rowid, id, opened_at, closed_at, symbol, side, setup, status,
               entry, exit, size_usd, pnl_usd, payload
        FROM positions
        WHERE status='OPEN'
        ORDER BY opened_at
    """)


def detect_conflicts(positions: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    by_symbol: Dict[str, List[Dict[str, Any]]] = {}
    for p in positions:
        by_symbol.setdefault(str(p.get("symbol")), []).append(p)

    conflicts: Dict[str, List[Dict[str, Any]]] = {}
    for symbol, ps in by_symbol.items():
        sides = sorted(set(str(p.get("side")) for p in ps))
        if len(ps) > 1 or len(sides) > 1:
            conflicts[symbol] = ps
    return conflicts


def effective_size_usd(row: Dict[str, Any], payload: Dict[str, Any]) -> float:
    if "size_usd" in payload:
        return fnum(payload.get("size_usd"))
    remaining_pct = fnum(payload.get("remaining_pct"), 1.0)
    return fnum(row.get("size_usd")) * remaining_pct


def entry_price(row: Dict[str, Any], payload: Dict[str, Any]) -> float:
    return fnum(payload.get("entry_price") or payload.get("entry") or row.get("entry"))


def calc_close(row: Dict[str, Any], mark_price: float) -> Dict[str, Any]:
    payload = safe_json(row.get("payload"))
    side = str(row.get("side")).upper()
    entry = entry_price(row, payload)
    size = effective_size_usd(row, payload)

    if entry <= 0 or mark_price <= 0 or size <= 0:
        return {
            "ok": False,
            "error": "BAD_NUMERIC_STATE",
            "entry": entry,
            "mark_price": mark_price,
            "size_usd": size,
        }

    if side == "LONG":
        gross = ((mark_price - entry) / entry) * size
    elif side == "SHORT":
        gross = ((entry - mark_price) / entry) * size
    else:
        return {"ok": False, "error": "BAD_SIDE", "side": side}

    fee_rate = fnum(getattr(__import__("joanbot.config", fromlist=["CFG"]).CFG, "fee_rate", 0.00045))
    fees = size * fee_rate * 2
    pnl = gross - fees

    return {
        "ok": True,
        "entry": entry,
        "exit": mark_price,
        "side": side,
        "size_usd": size,
        "gross_pnl_usd": round(gross, 8),
        "fees": round(fees, 8),
        "pnl_usd": round(pnl, 8),
        "strategy_attributable": False,
        "reason": RECON_REASON,
        "original_remaining_pct": fnum(payload.get("remaining_pct"), 1.0),
        "tp1_done": bool(payload.get("tp1_done")),
        "trail_active": bool(payload.get("trail_active")),
        "original_payload": payload,
    }


def canonical_plan(plan: Dict[str, Any]) -> Dict[str, Any]:
    """Stable reconciliation fingerprint.

    Hash changes when executable reconciliation content changes.
    Hash must not change only because wall-clock age_min increases.
    """
    planned = []
    for item in plan.get("planned_closes", []):
        pos = item.get("position", {})
        mark = item.get("mark", {})
        close = item.get("close", {})

        planned.append({
            "position": {
                "id": pos.get("id"),
                "opened_at": pos.get("opened_at"),
                "symbol": pos.get("symbol"),
                "side": pos.get("side"),
                "setup": pos.get("setup"),
            },
            "mark": {
                "price": mark.get("price"),
                "ts": mark.get("ts"),
            },
            "close": {
                "entry": close.get("entry"),
                "exit": close.get("exit"),
                "size_usd": close.get("size_usd"),
                "fees": close.get("fees"),
                "pnl_usd": close.get("pnl_usd"),
                "reason": close.get("reason"),
                "strategy_attributable": close.get("strategy_attributable"),
            },
        })

    blocked = []
    for b in plan.get("blocked", []):
        blocked.append({
            "position_id": b.get("position_id"),
            "symbol": b.get("symbol"),
            "reason": b.get("reason"),
            "mark_ts": b.get("mark_ts"),
        })

    return {
        "version": plan["version"],
        "max_age_min": plan["max_age_min"],
        "planned_closes": planned,
        "blocked": blocked,
        "planned_total_pnl_usd": plan["planned_total_pnl_usd"],
        "planned_total_fees_usd": plan["planned_total_fees_usd"],
    }


def hash_plan(plan: Dict[str, Any]) -> str:
    raw = json.dumps(canonical_plan(plan), sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()[:16]


def build_plan(con: sqlite3.Connection, max_age_min: float) -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    prices = latest_prices(con)
    opens = open_positions(con)
    conflicts = detect_conflicts(opens)

    planned = []
    blocked = []

    for symbol, ps in conflicts.items():
        lp = prices.get(symbol)
        if not lp:
            for p in ps:
                blocked.append({"position_id": p.get("id"), "symbol": symbol, "reason": "NO_MARK_PRICE"})
            continue

        mark_ts = parse_ts(lp.get("ts"))
        age_min = ((now - mark_ts).total_seconds() / 60.0) if mark_ts else 999999.0

        if age_min > max_age_min:
            for p in ps:
                blocked.append({
                    "position_id": p.get("id"),
                    "symbol": symbol,
                    "reason": "STALE_MARK_PRICE",
                    "mark_ts": lp.get("ts"),
                    "age_min": round(age_min, 2),
                })
            continue

        for p in ps:
            close = calc_close(p, fnum(lp.get("price")))
            if not close.get("ok"):
                blocked.append({
                    "position_id": p.get("id"),
                    "symbol": symbol,
                    "reason": close.get("error"),
                    "detail": close,
                })
                continue

            planned.append({
                "position": {
                    "rowid": p.get("rowid"),
                    "id": p.get("id"),
                    "opened_at": p.get("opened_at"),
                    "symbol": p.get("symbol"),
                    "side": p.get("side"),
                    "setup": p.get("setup"),
                    "entry": p.get("entry"),
                    "size_usd_column": p.get("size_usd"),
                },
                "mark": {
                    "price": fnum(lp.get("price")),
                    "ts": lp.get("ts"),
                    "age_min": round(age_min, 2),
                },
                "close": close,
            })

    plan = {
        "version": VERSION,
        "created_at": utc_now(),
        "max_age_min": max_age_min,
        "open_positions": len(opens),
        "conflict_symbols": len(conflicts),
        "conflict_positions": sum(len(v) for v in conflicts.values()),
        "planned_closes": planned,
        "blocked": blocked,
        "planned_total_pnl_usd": round(sum(fnum(x["close"]["pnl_usd"]) for x in planned), 4),
        "planned_total_fees_usd": round(sum(fnum(x["close"]["fees"]) for x in planned), 4),
    }
    plan["plan_hash"] = hash_plan(plan)
    return plan


def write_report(plan: Dict[str, Any], mode: str) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORT_DIR / f"state_integrity_v1_{mode}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{plan['plan_hash']}.json"
    path.write_text(json.dumps(plan, indent=2, sort_keys=True), encoding="utf-8")
    return path


def record_event(con: sqlite3.Connection, event: str, status: str, plan: Dict[str, Any]) -> None:
    payload = json.dumps(plan, sort_keys=True)
    con.execute("""
        INSERT INTO state_integrity_events(ts, version, event, plan_hash, status, payload)
        VALUES(?,?,?,?,?,?)
    """, (utc_now(), VERSION, event, plan.get("plan_hash"), status, payload))

    con.execute("""
        INSERT INTO runtime_events(ts, component, level, message, payload)
        VALUES(?,?,?,?,?)
    """, (utc_now(), EVENT_COMPONENT, "WARN", event, payload))


def apply_plan(con: sqlite3.Connection, plan: Dict[str, Any]) -> None:
    if plan.get("blocked"):
        raise SystemExit("REFUSE_APPLY_BLOCKED_ITEMS")

    if not plan.get("planned_closes"):
        raise SystemExit("REFUSE_APPLY_EMPTY_PLAN")

    ensure_schema(con)
    ts = utc_now()

    with con:
        record_event(con, "STATE_INTEGRITY_RECONCILIATION_STARTED", "STARTED", plan)

        for item in plan["planned_closes"]:
            p = item["position"]
            c = item["close"]

            event_payload = {
                "version": VERSION,
                "reason": RECON_REASON,
                "plan_hash": plan["plan_hash"],
                "ts": ts,
                "position": p,
                "mark": item["mark"],
                "close": c,
                "strategy_attributable": False,
            }

            con.execute("""
                INSERT INTO trades(ts, position_id, symbol, side, setup, pnl_usd, pnl_r, fees, reason, payload)
                VALUES(?,?,?,?,?,?,?,?,?,?)
            """, (
                ts,
                p["id"],
                p["symbol"],
                p["side"],
                p["setup"],
                c["pnl_usd"],
                0.0,
                c["fees"],
                RECON_REASON,
                json.dumps(event_payload, sort_keys=True),
            ))

            con.execute("""
                INSERT INTO position_events(ts, position_id, event, symbol, payload)
                VALUES(?,?,?,?,?)
            """, (
                ts,
                p["id"],
                "LEGACY_RECONCILIATION_CLOSE",
                p["symbol"],
                json.dumps(event_payload, sort_keys=True),
            ))

            final_payload = dict(c.get("original_payload") or {})
            final_payload["state_integrity"] = event_payload
            final_payload["strategy_attributable"] = False
            final_payload["status"] = "CLOSED"

            con.execute("""
                UPDATE positions
                SET status='CLOSED',
                    closed_at=?,
                    exit=?,
                    pnl_usd=?,
                    payload=?
                WHERE id=? AND status='OPEN'
            """, (
                ts,
                c["exit"],
                c["pnl_usd"],
                json.dumps(final_payload, sort_keys=True),
                p["id"],
            ))

        baseline = {
            "version": "STATE_INTEGRITY_BASELINE_V1",
            "created_at": utc_now(),
            "reason": "baseline after legacy reconciliation",
            "plan_hash": plan["plan_hash"],
            "max_trade_id": one(con, "SELECT COALESCE(MAX(id),0) FROM trades"),
            "max_position_rowid": one(con, "SELECT COALESCE(MAX(rowid),0) FROM positions"),
            "max_decision_id": one(con, "SELECT COALESCE(MAX(id),0) FROM decisions"),
            "max_runtime_event_id": one(con, "SELECT COALESCE(MAX(id),0) FROM runtime_events"),
        }

        BASELINE_PATH.write_text(json.dumps(baseline, indent=2, sort_keys=True), encoding="utf-8")

        completed_plan = dict(plan)
        completed_plan["baseline"] = baseline
        record_event(con, "STATE_INTEGRITY_RECONCILIATION_COMPLETED", "COMPLETED", completed_plan)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--plan-hash", default="")
    ap.add_argument("--max-age-min", type=float, default=15.0)
    args = ap.parse_args()

    con = connect()
    ensure_schema(con)
    plan = build_plan(con, args.max_age_min)

    if args.apply:
        if not args.plan_hash:
            raise SystemExit(f"REFUSE_APPLY_WITHOUT_PLAN_HASH current_plan_hash={plan['plan_hash']}")
        if args.plan_hash != plan["plan_hash"]:
            raise SystemExit(f"REFUSE_PLAN_HASH_MISMATCH expected={plan['plan_hash']} got={args.plan_hash}")
        apply_plan(con, plan)
        mode = "applied"
    else:
        mode = "dry_run"
        with con:
            record_event(con, "STATE_INTEGRITY_RECONCILIATION_DRY_RUN", "DRY_RUN", plan)

    report = write_report(plan, mode)

    print(f"{VERSION}_{'APPLIED' if args.apply else 'DRY_RUN_OK'}")
    print("PLAN_HASH:", plan["plan_hash"])
    print("open_positions:", plan["open_positions"])
    print("conflict_symbols:", plan["conflict_symbols"])
    print("conflict_positions:", plan["conflict_positions"])
    print("planned_closes:", len(plan["planned_closes"]))
    print("blocked:", len(plan["blocked"]))
    print("planned_total_pnl_usd:", plan["planned_total_pnl_usd"])
    print("planned_total_fees_usd:", plan["planned_total_fees_usd"])
    print("report:", report)


if __name__ == "__main__":
    main()
