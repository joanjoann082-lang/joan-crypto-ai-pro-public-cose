#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

VERSION = "V24_6B_CANONICAL_OPERATING_SPINE"

START_EQUITY = 100000.0

PRICE_TABLE = "institutional_v24_market_price_latest"
POSITION_TABLE = "paper_micro_canary_positions_v11"
INTENT_TABLE = "institutional_quant_canary_execution_intents_v17_7_2"

# Execution caps enforced at the adapter level as final safety.
ADAPTER_CAPS = {
    "max_open_global": 2,
    "max_open_per_symbol": 1,
    "max_open_per_key": 1,
    "max_daily_valid_opens": 4,
}

# Memory hygiene policy. No contaminated outcome is allowed to train edge.
MEMORY_POLICY = {
    "max_abs_r_clean": 5.0,
    "max_abs_r_repaired": 2.5,
    "live_canary_weight": 1.0,
    "repaired_canary_weight": 0.75,
    "shadow_weight": 0.20,
    "required_repair_marker": "ACCOUNTING_REPAIRED_V24_4",
}

OFFICIAL_SERVICES = [
    {
        "name": "DATA_PLANE",
        "enabled": True,
        "critical": True,
        "pattern": "run_v18_9_1_data_plane_forever|v18_9_1_semantic_data_plane",
        "script": "scripts/run_v18_9_1_data_plane_forever.sh",
        "stdout": "data/v18_9_1_data_plane/v24_6_runtime_stdout.log",
        "stderr": "data/v18_9_1_data_plane/v24_6_runtime_stderr.log",
    },
    {
        "name": "LIQUIDATION_COLLECTOR",
        "enabled": True,
        "critical": False,
        "pattern": "run_v18_10_liquidation_collector_forever|v18_10_liquidation_collector",
        "script": "scripts/run_v18_10_liquidation_collector_forever.sh",
        "stdout": "data/v18_10_liquidation/v24_6_runtime_stdout.log",
        "stderr": "data/v18_10_liquidation/v24_6_runtime_stderr.log",
    },
    {
        "name": "QUANT_BRAIN",
        "enabled": True,
        "critical": True,
        "pattern": "run_v17_5_1_quant_brain_forever|quant_brain_v17_5_1|tools/run_quant_brain_v17_5_1",
        "script": "scripts/run_v17_5_1_quant_brain_forever.sh",
        "stdout": "data/v17_5_1/v24_6_runtime_stdout.log",
        "stderr": "data/v17_5_1/v24_6_runtime_stderr.log",
    },
    {
        "name": "V24_1_PRICE_CONTRACT",
        "enabled": True,
        "critical": True,
        "pattern": "run_v24_1_market_price_contract_forever|v24_1_market_price_contract.py",
        "script": "scripts/run_v24_1_market_price_contract_forever.sh",
        "stdout": "data/v24_1_market_price_contract/v24_6_runtime_stdout.log",
        "stderr": "data/v24_1_market_price_contract/v24_6_runtime_stderr.log",
    },
    {
        "name": "V24_0_QUANT_AUTHORITY",
        "enabled": True,
        "critical": True,
        "pattern": "run_v24_0_quant_production_authority_forever|v24_0_quant_production_authority.py",
        "script": "scripts/run_v24_0_quant_production_authority_forever.sh",
        "stdout": "data/v24_0_quant_authority/v24_6_runtime_stdout.log",
        "stderr": "data/v24_0_quant_authority/v24_6_runtime_stderr.log",
    },
    {
        "name": "V24_4_CANONICAL_ADAPTER",
        "enabled": True,
        "critical": True,
        "pattern": "run_v24_4_canonical_paper_adapter_forever|v24_4_canonical_paper_adapter.py",
        "script": "scripts/run_v24_4_canonical_paper_adapter_forever.sh",
        "stdout": "data/v24_4_accounting_core/v24_6_runtime_stdout.log",
        "stderr": "data/v24_4_accounting_core/v24_6_runtime_stderr.log",
    },
    {
        "name": "V24_5_CANONICAL_EQUITY",
        "enabled": True,
        "critical": False,
        "pattern": "run_v24_5_canonical_equity_forever|v24_5_canonical_equity_panel.py",
        "script": "scripts/run_v24_5_canonical_equity_forever.sh",
        "stdout": "data/v24_5_canonical_equity/v24_6_runtime_stdout.log",
        "stderr": "data/v24_5_canonical_equity/v24_6_runtime_stderr.log",
    },
    {
        "name": "MARKET_CONTEXT",
        "enabled": True,
        "critical": False,
        "pattern": "run_v18_2_market_context_forever|v18_2_market_context",
        "script": "scripts/run_v18_2_market_context_forever.sh",
        "stdout": "data/v18_2_market_context/v24_6_runtime_stdout.log",
        "stderr": "data/v18_2_market_context/v24_6_runtime_stderr.log",
    },

    # Disabled by canonical operating spine. Kept in repo, blocked at runtime.
    {
        "name": "V17_6_1_PROMOTION_DISABLED_BY_V24_6",
        "enabled": False,
        "critical": False,
        "pattern": "run_v17_6_1_promotion_controller_forever|promotion_controller_v17_6_1",
        "script": "scripts/run_v17_6_1_promotion_controller_forever.sh",
        "stdout": "data/v17_6_1/v24_6_disabled_stdout.log",
        "stderr": "data/v17_6_1/v24_6_disabled_stderr.log",
    },
    {
        "name": "V17_7_2_GOVERNANCE_DISABLED_BY_V24_6",
        "enabled": False,
        "critical": False,
        "pattern": "run_v17_7_2_governance_forever|run_max_quant_canary_governance_v17_7_2|max_quant_canary_governance_v17_7_2",
        "script": "scripts/run_v17_7_2_governance_forever.sh",
        "stdout": "data/v17_7_2_governance/v24_6_disabled_stdout.log",
        "stderr": "data/v17_7_2_governance/v24_6_disabled_stderr.log",
    },
    {
        "name": "V17_8_1_ADAPTER_DISABLED_BY_V24_6",
        "enabled": False,
        "critical": False,
        "pattern": "run_v17_8_1_paper_canary_adapter_forever|institutional_paper_canary_adapter_v17_8_1",
        "script": "scripts/run_v17_8_1_paper_canary_adapter_forever.sh",
        "stdout": "data/v17_8_1/v24_6_disabled_stdout.log",
        "stderr": "data/v17_8_1/v24_6_disabled_stderr.log",
    },
    {
        "name": "V23_3_DISABLED_BY_V24_6",
        "enabled": False,
        "critical": False,
        "pattern": "v23_3_canary_promotion_bridge|run_v23_3_canary_promotion_bridge",
        "script": "scripts/run_v23_3_canary_promotion_bridge_forever.sh",
        "stdout": "data/v23_3_canary_bridge/v24_6_disabled_stdout.log",
        "stderr": "data/v23_3_canary_bridge/v24_6_disabled_stderr.log",
    },
    {
        "name": "V23_4_DISABLED_BY_V24_6",
        "enabled": False,
        "critical": False,
        "pattern": "v23_4_quant_execution_authority|run_v23_4_quant_execution_authority_forever",
        "script": "scripts/run_v23_4_quant_execution_authority_forever.sh",
        "stdout": "data/v23_4_quant_execution_authority/v24_6_disabled_stdout.log",
        "stderr": "data/v23_4_quant_execution_authority/v24_6_disabled_stderr.log",
    },
    {
        "name": "V24_4_LATERAL_STACK_DISABLED_BY_V24_6",
        "enabled": False,
        "critical": False,
        "pattern": "run_v24_4_quant_stack_forever.sh",
        "script": "scripts/run_v24_4_quant_stack_forever.sh",
        "stdout": "data/v24_4_accounting_core/v24_6_disabled_stack_stdout.log",
        "stderr": "data/v24_4_accounting_core/v24_6_disabled_stack_stderr.log",
    },
]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def qid(x: str) -> str:
    return '"' + str(x).replace('"', '""') + '"'


def fnum(x: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if x is None:
            return default
        v = float(x)
        if math.isnan(v) or math.isinf(v):
            return default
        return v
    except Exception:
        return default


def table_exists(con: sqlite3.Connection, table: str) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone() is not None


def cols(con: sqlite3.Connection, table: str) -> List[str]:
    if not table_exists(con, table):
        return []
    return [r[1] for r in con.execute(f"PRAGMA table_info({qid(table)})")]


def canonical_price_snapshot(con: sqlite3.Connection, symbol: str) -> Dict[str, Any]:
    try:
        from joanbot.institutional.canonical_paper_accounting_v24_4 import canonical_price
        price, meta = canonical_price(con, symbol)
        if price is None:
            return {
                "ok": False,
                "symbol": symbol,
                "price": None,
                "reason": meta.get("reason", "CANONICAL_PRICE_REJECTED"),
                "meta": meta,
            }
        return {
            "ok": True,
            "symbol": symbol,
            "price": price,
            "reason": meta.get("reason", "CANONICAL_PRICE_OK"),
            "age_min": meta.get("age_min"),
            "ts": meta.get("source_ts"),
            "source_table": meta.get("source_table"),
            "source_column": "price",
            "meta": meta,
        }
    except Exception as e:
        return {
            "ok": False,
            "symbol": symbol,
            "price": None,
            "reason": "CANONICAL_PRICE_EXCEPTION",
            "error": repr(e),
        }


def canonical_equity_snapshot(con: sqlite3.Connection) -> Dict[str, Any]:
    try:
        from joanbot.institutional.canonical_equity_v24_5 import snapshot
        s = snapshot(con)
        balance = float(s.get("total_equity", START_EQUITY))
        return {
            "balance": round(balance, 2),
            "pnl_usd": round(balance - START_EQUITY, 2),
            "return_pct": round((balance / START_EQUITY - 1.0) * 100.0, 4),
            "closed_trades": int(s.get("closed_trades", 0)),
            "open_positions": int(s.get("open_positions", 0)),
            "open_net_pnl_usd": round(float(s.get("open_net_pnl_usd", 0.0)), 2),
            "suspicious_positions": int(s.get("suspicious_positions", 0)),
            "source": "V24_5_CANONICAL_EQUITY",
            "raw": s,
        }
    except Exception as e:
        return {
            "balance": START_EQUITY,
            "pnl_usd": 0.0,
            "return_pct": 0.0,
            "closed_trades": 0,
            "open_positions": 0,
            "open_net_pnl_usd": 0.0,
            "suspicious_positions": -1,
            "source": "V24_5_CANONICAL_EQUITY_ERROR",
            "error": repr(e),
        }


def outcome_hygiene(row: Dict[str, Any]) -> Dict[str, Any]:
    status = str(row.get("status") or row.get("state") or "").upper()
    closed = status in {"CLOSED", "EXITED", "DONE", "COMPLETED"} or bool(row.get("closed_at") or row.get("exit_price"))

    key = str(row.get("key") or "")
    symbol = str(row.get("symbol") or "").upper()
    side = str(row.get("side") or "").upper()
    setup = str(row.get("setup") or "")

    r = fnum(row.get("net_pnl_r"), fnum(row.get("pnl_r")))
    entry = fnum(row.get("entry_price"))
    exitp = fnum(row.get("exit_price"))
    manager = str(row.get("manager_state") or row.get("close_reason") or "")

    reasons: List[str] = []
    allowed = True
    weight = MEMORY_POLICY["live_canary_weight"]

    if not closed:
        allowed = False
        reasons.append("NOT_CLOSED")

    if r is None:
        allowed = False
        reasons.append("NO_R_VALUE")

    repaired = MEMORY_POLICY["required_repair_marker"] in manager
    if repaired:
        weight = MEMORY_POLICY["repaired_canary_weight"]
        if r is not None and abs(r) > MEMORY_POLICY["max_abs_r_repaired"]:
            allowed = False
            reasons.append("REPAIRED_R_OUT_OF_POLICY")
    else:
        if r is not None and abs(r) > MEMORY_POLICY["max_abs_r_clean"]:
            allowed = False
            reasons.append("R_OUTLIER")

    if entry and exitp:
        ratio = exitp / entry
        if ratio > 1.15 or ratio < 0.85:
            allowed = False
            reasons.append("EXIT_PRICE_OUTLIER")

    return {
        "key": key or f"{symbol}|{side}|{setup}",
        "symbol": symbol,
        "side": side,
        "setup": setup,
        "r": r,
        "learning_allowed": bool(allowed),
        "evidence_weight": weight if allowed else 0.0,
        "state": "LEARNING_ALLOWED" if allowed else "LEARNING_QUARANTINED",
        "reasons": reasons,
        "repaired": repaired,
    }


def clean_r_values(con: sqlite3.Connection, key: str, symbol: str, side: str, setup: str) -> List[float]:
    if not table_exists(con, POSITION_TABLE):
        return []

    rows = [dict(r) for r in con.execute(f"SELECT * FROM {qid(POSITION_TABLE)}").fetchall()]
    vals: List[float] = []

    for row in rows:
        h = outcome_hygiene(row)
        if not h["learning_allowed"]:
            continue

        row_key = h["key"]
        if row_key and row_key != key:
            continue

        if not row_key:
            if h["symbol"] != str(symbol).upper():
                continue
            if h["side"] != str(side).upper():
                continue
            if h["setup"] != str(setup):
                continue

        if h["r"] is not None:
            vals.append(float(h["r"]) * float(h["evidence_weight"]))

    return vals


def memory_hygiene_summary(con: sqlite3.Connection) -> Dict[str, Any]:
    if not table_exists(con, POSITION_TABLE):
        return {
            "version": VERSION,
            "positions_seen": 0,
            "learning_allowed": 0,
            "learning_quarantined": 0,
            "repaired_rows": 0,
            "shadow_weight": MEMORY_POLICY["shadow_weight"],
            "live_canary_weight": MEMORY_POLICY["live_canary_weight"],
            "repaired_canary_weight": MEMORY_POLICY["repaired_canary_weight"],
        }

    rows = [dict(r) for r in con.execute(f"SELECT * FROM {qid(POSITION_TABLE)}").fetchall()]
    allowed = 0
    quarantined = 0
    repaired = 0

    for row in rows:
        h = outcome_hygiene(row)
        if h["learning_allowed"]:
            allowed += 1
        else:
            quarantined += 1
        if h["repaired"]:
            repaired += 1

    return {
        "version": VERSION,
        "positions_seen": len(rows),
        "learning_allowed": allowed,
        "learning_quarantined": quarantined,
        "repaired_rows": repaired,
        "shadow_weight": MEMORY_POLICY["shadow_weight"],
        "live_canary_weight": MEMORY_POLICY["live_canary_weight"],
        "repaired_canary_weight": MEMORY_POLICY["repaired_canary_weight"],
    }
