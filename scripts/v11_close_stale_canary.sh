#!/data/data/com.termux/files/usr/bin/bash
set -e

cd "$(dirname "$0")/.."
DB="data/joanbot_v14.sqlite"

python - <<'PY'
import sqlite3
import json
from datetime import datetime, timezone

DB = "data/joanbot_v14.sqlite"
GRACE_MIN = 10

con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row
cur = con.cursor()

def table_exists(name):
    return cur.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE name=?",
        (name,)
    ).fetchone()[0] > 0

def cols(table):
    return [r[1] for r in cur.execute(f"PRAGMA table_info({table})").fetchall()]

def parse_dt(x):
    if not x:
        return None
    s = str(x).replace("Z", "+00:00")
    try:
        d = datetime.fromisoformat(s)
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d
    except Exception:
        return None

def latest_price(symbol):
    if not table_exists("market_snapshots"):
        raise SystemExit("NO_MARKET_SNAPSHOTS_TABLE")

    c = cols("market_snapshots")
    for pc in ["price", "last_price", "close", "mark_price"]:
        if pc in c:
            row = cur.execute(
                f"""
                SELECT {pc} AS px
                FROM market_snapshots
                WHERE symbol=?
                  AND {pc} IS NOT NULL
                ORDER BY rowid DESC
                LIMIT 1
                """,
                (symbol,)
            ).fetchone()
            if row and row["px"] is not None:
                return float(row["px"])

    raise SystemExit(f"NO_LATEST_PRICE_FOR_{symbol}")

table = "paper_micro_canary_positions_v11"
if not table_exists(table):
    raise SystemExit("NO_V11_CANARY_TABLE")

c = cols(table)

rows = cur.execute(f"""
    SELECT *
    FROM {table}
    WHERE status='OPEN' OR closed_at IS NULL
    ORDER BY id ASC
""").fetchall()

if not rows:
    print("NO_OPEN_V11_CANARY")
    raise SystemExit(0)

now = datetime.now(timezone.utc)

for r in rows:
    d = dict(r)

    cid = d.get("id")
    symbol = d.get("symbol")
    side = str(d.get("side") or "").upper()
    entry = float(d.get("entry_price") or 0)
    size = float(d.get("size_usd") or 0)
    horizon = int(float(d.get("horizon_min") or 0))
    opened = parse_dt(d.get("opened_at"))

    if not opened:
        print("SKIP_NO_OPENED_AT", cid)
        continue

    age_min = (now - opened).total_seconds() / 60.0
    stale_after = horizon + GRACE_MIN

    print(json.dumps({
        "id": cid,
        "symbol": symbol,
        "side": side,
        "entry": entry,
        "size_usd": size,
        "horizon_min": horizon,
        "age_min": round(age_min, 2),
        "stale_after_min": stale_after,
        "status": d.get("status")
    }, indent=2))

    if horizon <= 0:
        print("SKIP_NO_HORIZON")
        continue

    if age_min < stale_after:
        print("NOT_STALE_YET_NO_ACTION")
        continue

    if side not in {"LONG", "SHORT"} or entry <= 0 or size <= 0:
        print("SKIP_INVALID_CANARY")
        continue

    exit_px = latest_price(symbol)

    if side == "LONG":
        pnl_usd = size * ((exit_px - entry) / entry)
    else:
        pnl_usd = size * ((entry - exit_px) / entry)

    # V11 risk fallback: current V11 canary uses approx 1.2% risk of notional.
    risk_usd = size * 0.012
    pnl_r = pnl_usd / risk_usd if risk_usd > 0 else 0.0

    updates = {}

    if "closed_at" in c:
        updates["closed_at"] = now.isoformat()
    if "exit_price" in c:
        updates["exit_price"] = exit_px
    if "status" in c:
        updates["status"] = "CLOSED"
    if "pnl_usd" in c:
        updates["pnl_usd"] = pnl_usd
    if "net_usd" in c:
        updates["net_usd"] = pnl_usd
    if "gross_pnl_r" in c:
        updates["gross_pnl_r"] = pnl_r
    if "net_pnl_r" in c:
        updates["net_pnl_r"] = pnl_r
    if "pnl_r" in c:
        updates["pnl_r"] = pnl_r
    if "reason" in c:
        updates["reason"] = "V11_HORIZON_EXPIRED_STALE_CANARY_GUARD"

    set_sql = ", ".join([f"{k}=?" for k in updates])
    params = list(updates.values()) + [cid]

    cur.execute(
        f"UPDATE {table} SET {set_sql} WHERE id=? AND (status='OPEN' OR closed_at IS NULL)",
        params
    )

    print(json.dumps({
        "closed_v11_canary_id": cid,
        "symbol": symbol,
        "side": side,
        "entry": entry,
        "exit": exit_px,
        "size_usd": size,
        "pnl_usd": round(pnl_usd, 6),
        "pnl_r": round(pnl_r, 6),
        "reason": "V11_HORIZON_EXPIRED_STALE_CANARY_GUARD"
    }, indent=2))

con.commit()
con.close()
print("V11_STALE_CANARY_GUARD_DONE")
PY
