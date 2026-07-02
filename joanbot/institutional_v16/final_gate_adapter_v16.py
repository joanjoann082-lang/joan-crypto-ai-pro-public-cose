from __future__ import annotations

import json
import sqlite3
from typing import Any, Dict

DB = "data/joanbot_v14.sqlite"


def latest_gate():
    try:
        con = sqlite3.connect(DB, timeout=10)
        con.row_factory = sqlite3.Row
        cur = con.cursor()
        row = cur.execute("SELECT * FROM latest_alpha_final_gate_v16 LIMIT 1;").fetchone()
        con.close()
        return dict(row) if row else {}
    except Exception:
        return {}


def as_list(x):
    if isinstance(x, list):
        return x
    if isinstance(x, str):
        try:
            y = json.loads(x)
            return y if isinstance(y, list) else [str(y)]
        except Exception:
            return [x]
    if not x:
        return []
    return [str(x)]


def apply_alpha_final_gate_v16(control: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(control, dict):
        return control

    gate = latest_gate()
    control["alpha_gate_v16_seen"] = bool(gate)

    if not gate:
        control["allow_paper_micro_canary"] = 0
        control["max_size_usd"] = 0.0
        control["recommended_action"] = "BLOCKED_ALPHA_GATE_V16_NOT_READY"
        control["hard_vetoes"] = as_list(control.get("hard_vetoes")) + ["ALPHA_GATE_V16_NOT_READY"]
        return control

    state = gate.get("gate_state")
    allow = int(gate.get("allow_trade") or 0)
    size_mult = float(gate.get("size_mult") or 0.0)

    control["alpha_gate_v16_state"] = state
    control["alpha_gate_v16_policy"] = gate.get("policy")
    control["alpha_gate_v16_size_mult"] = size_mult

    gate_vetoes = as_list(gate.get("hard_vetoes"))

    if allow != 1:
        control["allow_paper_micro_canary"] = 0
        control["max_size_usd"] = 0.0
        control["recommended_action"] = "BLOCKED_BY_ALPHA_FINAL_GATE_V16"
        control["hard_vetoes"] = as_list(control.get("hard_vetoes")) + ["ALPHA_FINAL_GATE_V16_BLOCK"] + gate_vetoes
        return control

    if state in {"FINAL_DISCOVERY_MICRO", "FINAL_REDUCE"}:
        old_size = float(control.get("max_size_usd") or 0.0)
        control["max_size_usd"] = old_size * min(size_mult, 0.25)
        control["recommended_action"] = "ALPHA_FINAL_GATE_V16_REDUCED_OR_DISCOVERY"

    return control
