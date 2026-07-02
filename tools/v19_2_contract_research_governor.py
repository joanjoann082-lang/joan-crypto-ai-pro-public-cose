#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

DB = Path("data/joanbot_v14.sqlite")
OUT = Path("data/v19_2_contract_governor")
VERSION = "V19.2_INSTITUTIONAL_CONTRACT_RESEARCH_GOVERNOR"

V19_1_PATH = Path("tools/v19_1_institutional_research_governor.py")

INTENT_TABLE = "institutional_quant_canary_execution_intents_v17_7_2"
CONTRACT_AUDIT_TABLE = "institutional_contract_audit_v19_2"
CONTRACT_HEALTH_TABLE = "institutional_contract_governor_health_v19_2"

ADAPTER_INTENT_STATE = "MAX_QUANT_MANUAL_APPROVED_PENDING_PAPER_ADAPTER"
ADAPTER_STATUS = "PENDING_ADAPTER_BINDING"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def qid(x: str) -> str:
    return '"' + str(x).replace('"', '""') + '"'


def fnum(x: Any, default=None):
    try:
        if x is None:
            return default
        v = float(x)
        if math.isnan(v) or math.isinf(v):
            return default
        return v
    except Exception:
        return default


def exists(con: sqlite3.Connection, table: str) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone() is not None


def table_info(con: sqlite3.Connection, table: str) -> List[Dict[str, Any]]:
    rows = con.execute(f"PRAGMA table_info({qid(table)})").fetchall()
    return [
        {
            "cid": r[0],
            "name": r[1],
            "type": r[2],
            "notnull": int(r[3] or 0),
            "default": r[4],
            "pk": int(r[5] or 0),
        }
        for r in rows
    ]


def create_audit_tables(con: sqlite3.Connection) -> None:
    con.execute(f"""
        CREATE TABLE IF NOT EXISTS {qid(CONTRACT_AUDIT_TABLE)} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            version TEXT NOT NULL,
            table_name TEXT NOT NULL,
            action TEXT NOT NULL,
            state TEXT NOT NULL,
            inserted_row_id INTEGER,
            required_cols TEXT,
            inserted_cols TEXT,
            missing_cols TEXT,
            error TEXT,
            payload TEXT
        )
    """)

    con.execute(f"""
        CREATE TABLE IF NOT EXISTS {qid(CONTRACT_HEALTH_TABLE)} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            version TEXT NOT NULL,
            db_quick_check TEXT,
            v19_1_present INTEGER,
            intent_table_present INTEGER,
            intent_columns INTEGER,
            required_notnull_without_default INTEGER,
            mode TEXT,
            result_state TEXT,
            emitted INTEGER,
            summary TEXT,
            payload TEXT
        )
    """)


def audit_event(
    con: sqlite3.Connection,
    table_name: str,
    action: str,
    state: str,
    inserted_row_id: Optional[int] = None,
    required_cols: Optional[List[str]] = None,
    inserted_cols: Optional[List[str]] = None,
    missing_cols: Optional[List[str]] = None,
    error: Optional[str] = None,
    payload: Optional[Dict[str, Any]] = None,
) -> None:
    con.execute(f"""
        INSERT INTO {qid(CONTRACT_AUDIT_TABLE)}
        (ts, version, table_name, action, state, inserted_row_id,
         required_cols, inserted_cols, missing_cols, error, payload)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        utc_now(),
        VERSION,
        table_name,
        action,
        state,
        inserted_row_id,
        json.dumps(required_cols or [], sort_keys=True),
        json.dumps(inserted_cols or [], sort_keys=True),
        json.dumps(missing_cols or [], sort_keys=True),
        error,
        json.dumps(payload or {}, sort_keys=True),
    ))


def required_notnull_cols(info: List[Dict[str, Any]]) -> List[str]:
    out = []
    for r in info:
        if r["pk"]:
            continue
        if r["notnull"] and r["default"] is None:
            out.append(r["name"])
    return out


def classify_type_default(col_type: str, name: str, context: Dict[str, Any]) -> Any:
    lname = name.lower()
    t = (col_type or "").upper()

    if "version" in lname:
        return VERSION
    if lname in {"ts", "created_at", "updated_at"} or "time" in lname or "date" in lname:
        return utc_now()
    if "symbol" in lname:
        return context.get("symbol", "")
    if "side" in lname:
        return context.get("side", "")
    if "setup" in lname:
        return context.get("setup", "")
    if "alpha" in lname and "key" in lname:
        return context.get("alpha_key", "")
    if "intent_state" in lname or lname == "state":
        return ADAPTER_INTENT_STATE
    if "adapter_status" in lname or lname == "status":
        return ADAPTER_STATUS
    if "source" in lname:
        return VERSION
    if "reason" in lname:
        return context.get("reasons", "")
    if "payload" in lname or "json" in lname:
        return context.get("payload", "{}")
    if "hash" in lname or lname.endswith("_id") or "contract" in lname:
        return context.get("hash", "")
    if "priority" in lname:
        return float(context.get("priority") or 0.0)
    if "score" in lname:
        return float(context.get("score") or 0.0)
    if "size" in lname or "mult" in lname:
        return float(context.get("size_mult") or 0.0)

    if "INT" in t:
        return 0
    if any(x in t for x in ["REAL", "FLOA", "DOUB", "NUM"]):
        return 0.0

    return ""


def schema_safe_insert(
    con: sqlite3.Connection,
    table: str,
    base_values: Dict[str, Any],
    context: Dict[str, Any],
) -> Tuple[Optional[int], str]:
    if not exists(con, table):
        return None, "TABLE_MISSING"

    info = table_info(con, table)
    col_names = [r["name"] for r in info]

    required = required_notnull_cols(info)
    values = dict(base_values)

    # V19.2.1 — adapter-contract semantic defaults.
    # No només compleix NOT NULL: intenta reutilitzar el valor real històric
    # d'un intent ja obert per l'adapter quan existeix.
    def _template_value(_col: str, _fallback: Any) -> Any:
        try:
            if _col not in col_names:
                return _fallback
            order_col = qid("id") if "id" in col_names else "rowid"
            if "adapter_status" in col_names:
                row = con.execute(
                    f"""
                    SELECT {qid(_col)} AS v
                    FROM {qid(table)}
                    WHERE COALESCE({qid(_col)}, '') <> ''
                      AND UPPER(COALESCE({qid('adapter_status')}, '')) LIKE 'OPENED%'
                    ORDER BY {order_col} DESC
                    LIMIT 1
                    """
                ).fetchone()
            else:
                row = con.execute(
                    f"""
                    SELECT {qid(_col)} AS v
                    FROM {qid(table)}
                    WHERE COALESCE({qid(_col)}, '') <> ''
                    ORDER BY {order_col} DESC
                    LIMIT 1
                    """
                ).fetchone()
            if row and row[0] not in (None, ""):
                return row[0]
        except Exception:
            pass
        return _fallback

    semantic_defaults = {
        "version": VERSION,
        "ts": context.get("ts") or utc_now(),
        "created_at": context.get("ts") or utc_now(),
        "updated_at": context.get("ts") or utc_now(),

        "intent_hash": context.get("hash", ""),
        "key": context.get("hash", ""),
        "contract_id": context.get("hash", ""),
        "decision_hash": context.get("hash", ""),

        "intent_state": ADAPTER_INTENT_STATE,
        "adapter_status": ADAPTER_STATUS,

        "requested_mode": _template_value("requested_mode", "PAPER_CANARY"),
        "execution_permission": _template_value("execution_permission", "PAPER_CANARY_ALLOWED"),

        "source": VERSION,
        "source_tier": VERSION,
        "source_version": VERSION,
    }

    for _k, _v in semantic_defaults.items():
        if _k in col_names and (values.get(_k) is None or values.get(_k) == ""):
            values[_k] = _v

    for r in info:
        name = r["name"]
        if r["pk"]:
            continue
        if name in values:
            continue
        if r["notnull"] and r["default"] is None:
            values[name] = classify_type_default(r["type"], name, context)

    missing = []
    for name in required:
        if name not in values:
            missing.append(name)
        elif values[name] is None:
            missing.append(name)

    if missing:
        audit_event(
            con,
            table,
            "schema_safe_insert",
            "BLOCKED_MISSING_REQUIRED",
            required_cols=required,
            inserted_cols=list(values.keys()),
            missing_cols=missing,
            payload=context,
        )
        return None, "MISSING_REQUIRED_" + "_".join(missing)

    insert_cols = [c for c in col_names if c != "id" and c in values]

    if not insert_cols:
        return None, "NO_INSERTABLE_COLUMNS"

    sql = (
        f"INSERT INTO {qid(table)} "
        f"({','.join(qid(c) for c in insert_cols)}) "
        f"VALUES ({','.join(['?'] * len(insert_cols))})"
    )

    try:
        con.execute(sql, tuple(values[c] for c in insert_cols))
        row_id = int(con.execute("SELECT last_insert_rowid()").fetchone()[0])
        audit_event(
            con,
            table,
            "schema_safe_insert",
            "INSERT_OK",
            inserted_row_id=row_id,
            required_cols=required,
            inserted_cols=insert_cols,
            payload=context,
        )
        return row_id, "INSERT_OK"
    except Exception as e:
        audit_event(
            con,
            table,
            "schema_safe_insert",
            "INSERT_ERROR",
            required_cols=required,
            inserted_cols=insert_cols,
            error=repr(e),
            payload=context,
        )
        return None, "INSERT_ERROR_" + repr(e)


def load_v19_1_module():
    if not V19_1_PATH.exists():
        raise RuntimeError("V19_1_MODULE_MISSING")

    spec = importlib.util.spec_from_file_location("v19_1_institutional_research_governor", str(V19_1_PATH))
    if spec is None or spec.loader is None:
        raise RuntimeError("V19_1_MODULE_IMPORT_SPEC_FAILED")

    mod = importlib.util.module_from_spec(spec)
    sys.modules["v19_1_institutional_research_governor"] = mod
    spec.loader.exec_module(mod)
    return mod


def make_safe_emit_intent(mod):
    def safe_emit_intent(con, c, d):
        reasons = ",".join(d.get("reasons") or [])
        now = utc_now()

        alpha_key = getattr(c, "alpha_key", f"{getattr(c, 'symbol', '')}|{getattr(c, 'side', '')}|{getattr(c, 'setup', '')}")
        raw_hash = f"{VERSION}|{now}|{alpha_key}|{d.get('state')}|{d.get('action')}"
        h = hashlib.sha256(raw_hash.encode()).hexdigest()[:24]

        payload = {
            "version": VERSION,
            "source_governor": "V19.1",
            "alpha_key": alpha_key,
            "decision_hash": h,
            "candidate": {
                "symbol": getattr(c, "symbol", None),
                "side": getattr(c, "side", None),
                "setup": getattr(c, "setup", None),
            },
            "decision": d,
        }

        context = {
            "symbol": getattr(c, "symbol", ""),
            "side": getattr(c, "side", ""),
            "setup": getattr(c, "setup", ""),
            "alpha_key": alpha_key,
            "reasons": reasons,
            "payload": json.dumps(payload, sort_keys=True),
            "hash": h,
            "priority": d.get("priority"),
            "score": d.get("allocation_score"),
            "size_mult": d.get("size_mult"),
        }

        base_values = {
            "version": VERSION,
            "ts": now,
            "created_at": now,
            "updated_at": now,
            "intent_state": ADAPTER_INTENT_STATE,
            "adapter_status": ADAPTER_STATUS,
            "symbol": getattr(c, "symbol", ""),
            "side": getattr(c, "side", ""),
            "setup": getattr(c, "setup", ""),
            "requested_size_mult": d.get("size_mult"),
            "institutional_priority": d.get("priority"),
            "source_tier": VERSION,
            "source": VERSION,
            "reason": reasons,
            "reasons": reasons,
            "payload": json.dumps(payload, sort_keys=True),
            "decision_hash": h,
            "contract_id": h,
            "intent_hash": h,
            "alpha_key": alpha_key,
        }

        return schema_safe_insert(con, INTENT_TABLE, base_values, context)

    return safe_emit_intent


def write_health(con, report: Dict[str, Any]) -> None:
    info = table_info(con, INTENT_TABLE) if exists(con, INTENT_TABLE) else []
    req = required_notnull_cols(info)

    con.execute(f"""
        INSERT INTO {qid(CONTRACT_HEALTH_TABLE)}
        (ts, version, db_quick_check, v19_1_present, intent_table_present,
         intent_columns, required_notnull_without_default, mode, result_state,
         emitted, summary, payload)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        utc_now(),
        VERSION,
        report.get("db_quick_check"),
        1 if V19_1_PATH.exists() else 0,
        1 if exists(con, INTENT_TABLE) else 0,
        len(info),
        len(req),
        report.get("mode"),
        report.get("result_state"),
        int(report.get("emitted") or 0),
        report.get("summary"),
        json.dumps(report, sort_keys=True),
    ))


def write_summary(report: Dict[str, Any]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)

    lines = [
        "# V19.2 Institutional Contract Research Governor",
        "",
        f"- UTC: `{utc_now()}`",
        f"- Mode: `{report.get('mode')}`",
        f"- DB: `{report.get('db_quick_check')}`",
        f"- Result: `{report.get('result_state')}`",
        f"- Summary: `{report.get('summary')}`",
        f"- Emitted: `{report.get('emitted')}`",
        f"- V19.1 present: `{report.get('v19_1_present')}`",
        f"- Intent table present: `{report.get('intent_table_present')}`",
        f"- Intent columns: `{report.get('intent_columns')}`",
        f"- Required NOT NULL cols: `{report.get('required_notnull_cols')}`",
        "",
        "## Governor output",
    ]

    for line in report.get("governor_lines", [])[:80]:
        lines.append(f"- `{str(line)[:240]}`")

    lines += [
        "",
        "## Latest emitted intents",
    ]

    for r in report.get("latest_intents", []):
        lines.append(f"- `{r}`")

    (OUT / "contract_governor_summary.md").write_text("\n".join(lines))
    (OUT / "contract_governor_report.json").write_text(json.dumps(report, indent=2, sort_keys=True))


def latest_intents(con: sqlite3.Connection, limit: int = 8) -> List[Dict[str, Any]]:
    if not exists(con, INTENT_TABLE):
        return []

    cols_available = [r["name"] for r in table_info(con, INTENT_TABLE)]
    wanted = [
        "id", "ts", "version", "intent_state", "adapter_status",
        "symbol", "side", "setup", "requested_size_mult",
        "institutional_priority", "source_tier",
    ]
    use = [c for c in wanted if c in cols_available]

    if not use:
        return []

    try:
        rows = con.execute(
            f"SELECT {','.join(qid(c) for c in use)} FROM {qid(INTENT_TABLE)} ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(zip(use, row)) for row in rows]
    except Exception:
        return []


def run(mode_emit: bool, max_candidates: int) -> Dict[str, Any]:
    if not DB.exists():
        raise RuntimeError("DB_MISSING")

    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row

    create_audit_tables(con)

    qc = con.execute("PRAGMA quick_check").fetchone()[0]
    info = table_info(con, INTENT_TABLE) if exists(con, INTENT_TABLE) else []
    req = required_notnull_cols(info)

    report: Dict[str, Any] = {
        "version": VERSION,
        "utc": utc_now(),
        "mode": "EMIT_ONE_CONTRACT_SAFE" if mode_emit else "REVIEW_ONLY_CONTRACT_SAFE",
        "db_quick_check": qc,
        "v19_1_present": V19_1_PATH.exists(),
        "intent_table_present": exists(con, INTENT_TABLE),
        "intent_columns": len(info),
        "required_notnull_cols": req,
        "result_state": "INIT",
        "summary": "",
        "emitted": 0,
        "governor_lines": [],
        "latest_intents": [],
    }

    if qc.lower() != "ok":
        report["result_state"] = "DB_NOT_OK"
        report["summary"] = "DB quick_check failed"
        write_health(con, report)
        con.commit()
        con.close()
        write_summary(report)
        return report

    if not V19_1_PATH.exists():
        report["result_state"] = "V19_1_MISSING"
        report["summary"] = "V19.1 governor file missing"
        write_health(con, report)
        con.commit()
        con.close()
        write_summary(report)
        return report

    if not exists(con, INTENT_TABLE):
        report["result_state"] = "INTENT_TABLE_MISSING"
        report["summary"] = "intent table missing"
        write_health(con, report)
        con.commit()
        con.close()
        write_summary(report)
        return report

    con.close()

    mod = load_v19_1_module()
    mod.emit_intent = make_safe_emit_intent(mod)

    # Executem el mateix V19.1, però amb emissió contract-safe.
    result = mod.run(emit_one=mode_emit, max_candidates=max_candidates)

    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    create_audit_tables(con)

    emitted = 1 if result.get("emitted") else 0
    report["emitted"] = emitted
    report["result_state"] = "OK"
    report["summary"] = result.get("summary", "NO_SUMMARY")
    report["governor_lines"] = [
        f"mode={result.get('mode')}",
        f"candidates={result.get('candidates')}",
        f"approved={result.get('approved')}",
        f"blocked={result.get('blocked')}",
        f"emitted={result.get('emitted')}",
        f"open_positions={result.get('open_positions')}",
        f"pending_intents={result.get('pending_intents')}",
        f"daily_emits={result.get('daily_emits')}",
        f"summary={result.get('summary')}",
    ]

    for d in result.get("decisions", [])[:20]:
        report["governor_lines"].append(
            f"{d.get('state')} {d.get('symbol')} {d.get('side')} {d.get('setup')} "
            f"alloc={d.get('allocation_score')} prob={d.get('prob_edge_pos')} q={d.get('q_value')} "
            f"size={d.get('size_mult')} intent={d.get('emitted_intent_id')} "
            f"reasons={','.join(d.get('reasons') or [])[:180]}"
        )

    report["latest_intents"] = latest_intents(con)

    write_health(con, report)
    con.commit()
    con.close()

    write_summary(report)
    return report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--emit-one", action="store_true")
    ap.add_argument("--max-candidates", type=int, default=50)
    args = ap.parse_args()

    try:
        report = run(args.emit_one, args.max_candidates)
    except Exception as e:
        OUT.mkdir(parents=True, exist_ok=True)
        err = {
            "version": VERSION,
            "utc": utc_now(),
            "error": repr(e),
        }
        (OUT / "contract_governor_crash.json").write_text(json.dumps(err, indent=2))
        print("V19_2_CRASH", repr(e))
        return 2

    print("===== V19.2 INSTITUTIONAL CONTRACT RESEARCH GOVERNOR =====")
    print("mode:", report.get("mode"))
    print("db:", report.get("db_quick_check"))
    print("result:", report.get("result_state"))
    print("summary:", report.get("summary"))
    print("emitted:", report.get("emitted"))
    print("intent_table_present:", report.get("intent_table_present"))
    print("intent_columns:", report.get("intent_columns"))
    print("required_notnull_cols:", report.get("required_notnull_cols"))
    print("summary_file: data/v19_2_contract_governor/contract_governor_summary.md")

    for line in report.get("governor_lines", [])[:30]:
        print(line)

    return 0 if report.get("result_state") == "OK" else 2


if __name__ == "__main__":
    raise SystemExit(main())
