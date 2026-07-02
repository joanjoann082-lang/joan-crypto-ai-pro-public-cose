from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable

from ..utils import fnum, utc_now_iso


CONTRACT_VERSION = "EXECUTION_CONTRACT_V1"

VALID_SIDES = {"LONG", "SHORT"}

# Temporary quarantine until live/forward evidence improves.
QUARANTINED_SETUPS = {
    "CAPITULATION_REBOUND_LONG",
}


@dataclass(frozen=True)
class ExecutionVerdict:
    allowed: bool
    reason: str
    severity: str = "INFO"
    contract_version: str = CONTRACT_VERSION
    details: Dict[str, Any] = field(default_factory=dict)


def is_setup_quarantined(setup: str | None) -> bool:
    return str(setup or "").upper() in QUARANTINED_SETUPS


def _decision_snapshot(d: Any) -> Dict[str, Any]:
    return {
        "action": getattr(d, "action", None),
        "symbol": getattr(d, "symbol", None),
        "side": getattr(d, "side", None),
        "setup": getattr(d, "setup", None),
        "trade_type": getattr(d, "trade_type", None),
        "final_score": getattr(d, "final_score", None),
        "confidence": getattr(d, "confidence", None),
        "size_usd": getattr(d, "size_usd", None),
        "entry": getattr(d, "entry", None),
        "stop_loss": getattr(d, "stop_loss", None),
        "take_profit_1": getattr(d, "take_profit_1", None),
        "take_profit_2": getattr(d, "take_profit_2", None),
    }


def _compact_position(p: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": p.get("id"),
        "symbol": p.get("symbol"),
        "side": p.get("side"),
        "setup": p.get("setup"),
        "entry_price": p.get("entry_price") or p.get("entry"),
        "size_usd": p.get("size_usd"),
        "opened_at": p.get("opened_at"),
    }


def evaluate_execution(d: Any, open_positions: Iterable[Dict[str, Any]]) -> ExecutionVerdict:
    """
    Final institutional execution authority.

    This function must remain deterministic and side-effect free.
    It only returns a verdict. Logging happens outside.
    """

    action = str(getattr(d, "action", "") or "").upper()
    symbol = str(getattr(d, "symbol", "") or "").upper()
    side = str(getattr(d, "side", "") or "").upper()
    setup = str(getattr(d, "setup", "") or "").upper()
    size_usd = fnum(getattr(d, "size_usd", 0))
    entry = fnum(getattr(d, "entry", 0))
    stop_loss = fnum(getattr(d, "stop_loss", 0))
    tp1 = fnum(getattr(d, "take_profit_1", 0))
    tp2 = fnum(getattr(d, "take_profit_2", 0))

    if action != "OPEN":
        return ExecutionVerdict(False, "REJECT_NON_OPEN_ACTION", details={"action": action})

    if not symbol:
        return ExecutionVerdict(False, "REJECT_MISSING_SYMBOL")

    if side not in VALID_SIDES:
        return ExecutionVerdict(False, "REJECT_INVALID_SIDE", details={"side": side})

    if size_usd <= 0:
        return ExecutionVerdict(False, "REJECT_ZERO_OR_NEGATIVE_SIZE", details={"size_usd": size_usd})

    if entry <= 0:
        return ExecutionVerdict(False, "REJECT_INVALID_ENTRY", details={"entry": entry})

    if stop_loss <= 0 or tp1 <= 0 or tp2 <= 0:
        return ExecutionVerdict(
            False,
            "REJECT_INVALID_EXIT_PLAN",
            details={"entry": entry, "stop_loss": stop_loss, "tp1": tp1, "tp2": tp2},
        )

    if side == "LONG":
        if stop_loss >= entry:
            return ExecutionVerdict(False, "REJECT_LONG_STOP_NOT_BELOW_ENTRY", details={"entry": entry, "stop_loss": stop_loss})
        if tp1 <= entry:
            return ExecutionVerdict(False, "REJECT_LONG_TP_NOT_ABOVE_ENTRY", details={"entry": entry, "tp1": tp1})
    else:
        if stop_loss <= entry:
            return ExecutionVerdict(False, "REJECT_SHORT_STOP_NOT_ABOVE_ENTRY", details={"entry": entry, "stop_loss": stop_loss})
        if tp1 >= entry:
            return ExecutionVerdict(False, "REJECT_SHORT_TP_NOT_BELOW_ENTRY", details={"entry": entry, "tp1": tp1})

    if is_setup_quarantined(setup):
        return ExecutionVerdict(False, "REJECT_QUARANTINED_SETUP", details={"setup": setup})

    for p in open_positions or []:
        if not isinstance(p, dict):
            continue

        p_symbol = str(p.get("symbol") or "").upper()
        p_side = str(p.get("side") or "").upper()

        if p_symbol != symbol:
            continue

        if p_side != side:
            return ExecutionVerdict(
                False,
                "REJECT_OPPOSITE_SIDE_HEDGE",
                details={"existing_position": _compact_position(p)},
            )

        return ExecutionVerdict(
            False,
            "REJECT_SYMBOL_ALREADY_OPEN",
            details={"existing_position": _compact_position(p)},
        )

    return ExecutionVerdict(True, "EXECUTION_ALLOWED")


def record_execution_rejection(db: Any, d: Any, verdict: ExecutionVerdict) -> None:
    if verdict.allowed:
        return

    payload = {
        "ts": utc_now_iso(),
        "contract_version": verdict.contract_version,
        "reason": verdict.reason,
        "severity": verdict.severity,
        "decision": _decision_snapshot(d),
        "details": verdict.details,
    }

    try:
        db.runtime_event("broker", "WARN", "EXECUTION_REJECTED", payload)
        return
    except Exception:
        pass

    try:
        db.insert_json(
            "runtime_events",
            payload,
            {
                "ts": payload["ts"],
                "component": "broker",
                "level": "WARN",
                "message": "EXECUTION_REJECTED",
            },
        )
    except Exception:
        pass


def verdict_to_dict(verdict: ExecutionVerdict) -> Dict[str, Any]:
    return asdict(verdict)
