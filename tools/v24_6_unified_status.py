#!/usr/bin/env python3
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from joanbot.institutional.canonical_market_data_contract_v24_9_final import canonical_market_health
from joanbot.institutional.canonical_schema_v24_6 import schema_report

ROOT = Path("/storage/emulated/0/Download/joan_crypto_ai_pro_v14")
DB = ROOT / "data" / "joanbot_v14.sqlite"


def parse_ts(x):
    if not x:
        return None
    try:
        return datetime.fromisoformat(str(x).replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


def age_min(x):
    d = parse_ts(x)
    if not d:
        return None
    return round((datetime.now(timezone.utc) - d).total_seconds() / 60, 3)


def table_exists(con, t):
    return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (t,)).fetchone() is not None


def cols(con, t):
    if not table_exists(con, t):
        return []
    return [r[1] for r in con.execute(f'PRAGMA table_info("{t}")')]


def latest(con, t, n=5):
    if not table_exists(con, t):
        return []
    c = cols(con, t)
    if not c:
        return []
    order = "id" if "id" in c else ("ts" if "ts" in c else c[0])
    return [dict(r) for r in con.execute(f'SELECT * FROM "{t}" ORDER BY "{order}" DESC LIMIT {n}')]


def main() -> int:
    con = sqlite3.connect(str(DB), timeout=30)
    con.row_factory = sqlite3.Row

    print("# V24.9 FINAL UNIFIED STATUS")
    print(f"- UTC: `{datetime.now(timezone.utc).isoformat()}`")
    print(f"- DB quick_check: `{con.execute('PRAGMA quick_check').fetchone()[0]}`")

    print("\n## Runtime")
    p = ROOT / "data/v22_1_runtime_manager/runtime_summary.md"
    if p.exists():
        txt = p.read_text().splitlines()
        for line in txt[:45]:
            print(line)
    else:
        print("- NO_RUNTIME_SUMMARY")

    print("\n## Schema")
    s = schema_report(con)
    for k in [
        "schema_ok",
        "position_schema_ok",
        "intent_schema_ok",
        "missing_position_columns",
        "missing_intent_columns",
        "duplicate_open_keys",
        "position_insert_contract_ok",
        "position_insert_contract_error",
        "position_insert_defaults",
    ]:
        print(f"- {k}={s.get(k)}")

    print("\n## Price")
    h = canonical_market_health(con)
    print(f"- market_health_ok={h.get('ok')} reason={h.get('reason')}")
    for sym, d in h.get("details", {}).items():
        print(
            f"- {sym}: ok={d.get('ok')} price={d.get('price')} "
            f"age={d.get('age_min')} source={d.get('source')} reason={d.get('reason')} "
            f"mark={d.get('mark_price')} index={d.get('index_price')} div={d.get('mark_index_divergence')}"
        )

    print("\n## Equity")
    t = "paper_micro_canary_positions_v11"
    if table_exists(con, t):
        c = cols(con, t)
        pnl_col = "net_pnl_usd" if "net_pnl_usd" in c else ("pnl_usd" if "pnl_usd" in c else None)
        if pnl_col:
            r = con.execute(f'''
                SELECT
                  COUNT(*) AS n,
                  SUM(CASE WHEN UPPER(status)="OPEN" THEN 1 ELSE 0 END) AS open_n,
                  SUM(CASE WHEN UPPER(status)="CLOSED" THEN 1 ELSE 0 END) AS closed_n,
                  ROUND(SUM(CASE WHEN UPPER(status)="CLOSED" THEN COALESCE({pnl_col},0) ELSE 0 END), 6) AS closed_pnl
                FROM "{t}"
            ''').fetchone()
            closed_pnl = float(r["closed_pnl"] or 0.0)
            balance = 100000.0 + closed_pnl
            print(f"- source=paper_micro_canary_positions_v11.{pnl_col}")
            print(f"- balance={round(balance, 6)}")
            print(f"- pnl_usd={round(closed_pnl, 6)}")
            print(f"- return_pct={round((closed_pnl/100000.0)*100, 6)}")
            print(f"- closed_trades={r['closed_n']}")
            print(f"- open_positions={r['open_n']}")
        else:
            print("- NO_PNL_COLUMN")
    else:
        print("- NO_POSITION_TABLE")

    print("\n## Adapter")
    t = "institutional_v24_4_canonical_adapter_health"
    if table_exists(con, t):
        for r in latest(con, t, 3):
            print(
                f"- id={r.get('id')} age={age_min(r.get('ts'))} quick={r.get('quick_check')} "
                f"pending={r.get('pending_intents')} opened={r.get('opened_positions')} "
                f"managed={r.get('managed_positions')} closed={r.get('closed_positions')} "
                f"rejected={r.get('rejected_intents')} errors={r.get('errors')}"
            )
    else:
        print("- NO_ADAPTER_HEALTH")

    print("\n## Intents")
    t = "institutional_quant_canary_execution_intents_v17_7_2"
    if table_exists(con, t):
        c = cols(con, t)
        keep = [x for x in ["id", "ts", "intent_state", "adapter_status", "symbol", "side", "setup", "requested_size_mult", "position_row_id", "stable_position_id"] if x in c]
        for r in con.execute(f'SELECT {",".join(keep)} FROM "{t}" ORDER BY id DESC LIMIT 8'):
            d = dict(r)
            print(
                f"- id={d.get('id')} age={age_min(d.get('ts'))} state={d.get('intent_state')} "
                f"adapter={d.get('adapter_status')} {d.get('symbol')} {d.get('side')} {d.get('setup')} "
                f"size={d.get('requested_size_mult')} pos={d.get('position_row_id')}"
            )
    else:
        print("- NO_INTENT_TABLE")

    print("\n## Memory hygiene")
    if table_exists(con, "paper_micro_canary_positions_v11"):
        c = cols(con, "paper_micro_canary_positions_v11")
        n = con.execute('SELECT COUNT(*) FROM "paper_micro_canary_positions_v11"').fetchone()[0]
        quarantined = 0
        if "manager_state" in c:
            quarantined = con.execute('''
                SELECT COUNT(*) FROM "paper_micro_canary_positions_v11"
                WHERE UPPER(COALESCE(manager_state,'')) LIKE '%CORRUPT%'
                   OR UPPER(COALESCE(manager_state,'')) LIKE '%QUARANTINE%'
            ''').fetchone()[0]
        repaired = 0
        if "accounting_repair_version" in c:
            repaired = con.execute('''
                SELECT COUNT(*) FROM "paper_micro_canary_positions_v11"
                WHERE accounting_repair_version IS NOT NULL
            ''').fetchone()[0]
        print(f"- positions_seen={n}")
        print(f"- learning_allowed={max(0, n-quarantined)}")
        print(f"- learning_quarantined={quarantined}")
        print(f"- repaired_rows={repaired}")
        print("- live_weight=1.0")
        print("- repaired_weight=0.75")
        print("- shadow_weight=0.2")
    else:
        print("- NO_POSITIONS")

    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
