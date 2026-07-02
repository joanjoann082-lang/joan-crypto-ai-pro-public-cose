from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from joanbot.storage.db import get_db
from joanbot.utils import utc_now_iso

VERSION = "PAPER_MICRO_CANARY_BRIDGE_V9"


def fnum(x: Any, default: float = 0.0) -> float:
    try:
        if x is None:
            return default
        return float(x)
    except Exception:
        return default


def inum(x: Any, default: int = 0) -> int:
    try:
        if x is None:
            return default
        return int(float(x))
    except Exception:
        return default


def js(x: Any) -> str:
    return json.dumps(x, separators=(",", ":"), ensure_ascii=False, default=str)


def parse_ts(ts: Any) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except Exception:
        return None


class PaperMicroCanaryBridgeV9:
    """
    Isolated paper micro-canary bridge.

    Writes only paper_micro_canary_positions_v9.
    Does not write legacy trades/positions/decisions.
    """

    def __init__(self, db=None) -> None:
        self.db = db or get_db()

    def ensure_schema(self) -> None:
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS paper_micro_canary_positions_v9 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                opened_at TEXT NOT NULL,
                closed_at TEXT,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                family_name TEXT NOT NULL,
                setup TEXT NOT NULL,
                profile TEXT NOT NULL,
                horizon_min INTEGER NOT NULL,
                status TEXT NOT NULL,
                entry_price REAL NOT NULL,
                exit_price REAL,
                size_usd REAL NOT NULL,
                pnl_usd REAL NOT NULL,
                pnl_r REAL NOT NULL,
                control_id INTEGER NOT NULL,
                source_edge_id INTEGER NOT NULL,
                reason TEXT NOT NULL,
                payload TEXT NOT NULL
            );
        """)

        self.db.execute("""
            CREATE TABLE IF NOT EXISTS paper_micro_canary_audit_v9 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                version TEXT NOT NULL,
                event TEXT NOT NULL,
                level TEXT NOT NULL,
                message TEXT NOT NULL,
                payload TEXT NOT NULL
            );
        """)

    def audit(self, event: str, level: str, message: str, payload: Dict[str, Any]) -> None:
        self.db.execute("""
            INSERT INTO paper_micro_canary_audit_v9
            (ts, version, event, level, message, payload)
            VALUES (?, ?, ?, ?, ?, ?);
        """, (utc_now_iso(), VERSION, event, level, message[:500], js(payload)))

    def q1(self, sql: str) -> Dict[str, Any]:
        try:
            rows = self.db.query(sql)
            return dict(rows[0]) if rows else {}
        except Exception:
            return {}

    def latest_control(self) -> Dict[str, Any]:
        return self.q1("SELECT * FROM latest_institutional_control_plane_v9;")

    def open_canary(self) -> Dict[str, Any]:
        return self.q1("SELECT * FROM paper_micro_canary_positions_v9 WHERE status='OPEN' ORDER BY id DESC LIMIT 1;")

    def price_for(self, prices: Dict[str, Any], symbol: str) -> float:
        v = prices.get(symbol)
        if isinstance(v, dict):
            return fnum(v.get("price") or v.get("last") or v.get("close"))
        return fnum(v)

    def manage_open(self, prices: Dict[str, Any]) -> Dict[str, Any]:
        p = self.open_canary()
        if not p:
            return {"managed": False, "reason": "NO_OPEN_CANARY"}

        symbol = str(p.get("symbol"))
        px = self.price_for(prices, symbol)

        if px <= 0:
            return {"managed": False, "reason": "NO_PRICE_FOR_OPEN_CANARY", "symbol": symbol}

        entry = fnum(p.get("entry_price"))
        side = str(p.get("side")).upper()
        size = fnum(p.get("size_usd"))
        horizon = inum(p.get("horizon_min"))

        direction = 1.0 if side == "LONG" else -1.0
        ret_pct = direction * ((px - entry) / entry) if entry > 0 else 0.0

        risk_pct_proxy = 0.01
        pnl_r = ret_pct / risk_pct_proxy
        pnl_usd = size * ret_pct

        opened = parse_ts(p.get("opened_at"))
        age_min = 0.0

        if opened:
            age_min = (datetime.now(timezone.utc) - opened).total_seconds() / 60.0

        close = False
        reason = "MARK_TO_MARKET"

        if pnl_r <= -1.0:
            close = True
            reason = "CANARY_STOP_-1R"
        elif pnl_r >= 1.5:
            close = True
            reason = "CANARY_TAKE_PROFIT_1_5R"
        elif age_min >= max(10, horizon):
            close = True
            reason = "CANARY_HORIZON_CLOSE"

        if close:
            self.db.execute("""
                UPDATE paper_micro_canary_positions_v9
                SET status='CLOSED',
                    closed_at=?,
                    exit_price=?,
                    pnl_usd=?,
                    pnl_r=?,
                    reason=?
                WHERE id=?;
            """, (utc_now_iso(), px, pnl_usd, pnl_r, reason, inum(p.get("id"))))

            self.audit("CANARY_CLOSED", "INFO", reason, {
                "id": inum(p.get("id")),
                "symbol": symbol,
                "side": side,
                "entry": entry,
                "exit": px,
                "pnl_r": pnl_r,
                "pnl_usd": pnl_usd,
                "age_min": age_min,
            })

            return {"managed": True, "closed": True, "reason": reason, "pnl_r": pnl_r, "pnl_usd": pnl_usd}

        return {"managed": True, "closed": False, "reason": reason, "pnl_r": pnl_r, "pnl_usd": pnl_usd, "age_min": age_min}

    def maybe_open(self, prices: Dict[str, Any], allow_open: bool = True) -> Dict[str, Any]:
        c = self.latest_control()

        if not c:
            return {"opened": False, "reason": "NO_CONTROL_V9"}

        if not allow_open:
            return {"opened": False, "reason": "AUDIT_MODE_NO_OPEN"}

        if inum(c.get("allow_paper_micro_canary")) != 1:
            return {"opened": False, "reason": "CONTROL_NOT_ALLOWING_MICRO_CANARY", "state": c.get("global_state")}

        if self.open_canary():
            return {"opened": False, "reason": "OPEN_CANARY_EXISTS"}

        symbol = str(c.get("edge_symbol") or "")
        side = str(c.get("edge_side") or "")
        px = self.price_for(prices, symbol)

        if not symbol or px <= 0:
            return {"opened": False, "reason": "NO_VALID_PRICE", "symbol": symbol, "price": px}

        size = min(50.0, max(10.0, fnum(c.get("max_size_usd"), 25.0)))

        payload = {
            "control_state": c.get("global_state"),
            "control_score": c.get("control_score"),
            "paper_only": True,
            "source": VERSION,
        }

        self.db.execute("""
            INSERT INTO paper_micro_canary_positions_v9 (
                opened_at,
                closed_at,
                symbol,
                side,
                family_name,
                setup,
                profile,
                horizon_min,
                status,
                entry_price,
                exit_price,
                size_usd,
                pnl_usd,
                pnl_r,
                control_id,
                source_edge_id,
                reason,
                payload
            )
            VALUES (?, NULL, ?, ?, ?, ?, ?, ?, 'OPEN', ?, NULL, ?, 0.0, 0.0, ?, ?, ?, ?);
        """, (
            utc_now_iso(),
            symbol,
            side,
            str(c.get("edge_family") or "UNKNOWN"),
            str(c.get("edge_setup") or "UNKNOWN"),
            str(c.get("edge_profile") or "UNKNOWN"),
            inum(c.get("edge_horizon_min")),
            px,
            size,
            inum(c.get("id")),
            inum(c.get("source_edge_id")),
            "CONTROL_V9_PAPER_MICRO_CANARY_READY",
            js(payload),
        ))

        self.audit("CANARY_OPENED", "INFO", "Paper micro canary opened", {
            "symbol": symbol,
            "side": side,
            "entry": px,
            "size": size,
            "control_id": inum(c.get("id")),
            "source_edge_id": inum(c.get("source_edge_id")),
        })

        return {"opened": True, "symbol": symbol, "side": side, "price": px, "size_usd": size}

    def refresh(self, prices: Dict[str, Any], allow_open: bool = True) -> Dict[str, Any]:
        self.ensure_schema()
        managed = self.manage_open(prices)
        opened = self.maybe_open(prices, allow_open=allow_open)

        return {
            "version": VERSION,
            "managed": managed,
            "opened": opened,
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--schema", action="store_true")
    args = parser.parse_args()

    engine = PaperMicroCanaryBridgeV9()
    engine.ensure_schema()

    if args.schema:
        print(json.dumps({"version": VERSION, "schema": "ok"}, indent=2))


if __name__ == "__main__":
    main()
