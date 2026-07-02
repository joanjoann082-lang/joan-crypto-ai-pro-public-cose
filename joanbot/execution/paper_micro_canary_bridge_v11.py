from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from joanbot.config import CFG
from joanbot.storage.db import get_db
from joanbot.utils import utc_now_iso

VERSION = "PAPER_MICRO_CANARY_BRIDGE_V11_NET_RISK"


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
        d = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.astimezone(timezone.utc)
    except Exception:
        return None


class PaperMicroCanaryBridgeV11:
    """
    Isolated V11 paper micro-canary bridge.

    Writes only paper_micro_canary_positions_v11 and audit table.
    Includes fee/slippage-adjusted net R and explicit stop/take-profit price.
    """

    def __init__(self, db=None) -> None:
        self.db = db or get_db()

    def ensure_schema(self) -> None:
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS paper_micro_canary_positions_v11 (
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
                stop_price REAL NOT NULL,
                take_profit_price REAL NOT NULL,
                initial_risk_pct REAL NOT NULL,
                size_usd REAL NOT NULL,
                pnl_usd REAL NOT NULL,
                pnl_r REAL NOT NULL,
                net_pnl_usd REAL NOT NULL,
                net_pnl_r REAL NOT NULL,
                mfe_r REAL NOT NULL,
                mae_r REAL NOT NULL,
                fee_usd_est REAL NOT NULL,
                slippage_usd_est REAL NOT NULL,
                control_id INTEGER NOT NULL,
                source_edge_id INTEGER NOT NULL,
                reason TEXT NOT NULL,
                payload TEXT NOT NULL
            );
        """)
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS paper_micro_canary_audit_v11 (
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
            INSERT INTO paper_micro_canary_audit_v11 (ts, version, event, level, message, payload)
            VALUES (?, ?, ?, ?, ?, ?);
        """, (utc_now_iso(), VERSION, event, level, message[:500], js(payload)))

    def q1(self, sql: str, params=()) -> Dict[str, Any]:
        try:
            rows = self.db.query(sql, params)
            return dict(rows[0]) if rows else {}
        except Exception:
            return {}

    def latest_control(self) -> Dict[str, Any]:
        return self.q1("SELECT * FROM latest_institutional_control_plane_v11;")

    def open_canary(self) -> Dict[str, Any]:
        return self.q1("SELECT * FROM paper_micro_canary_positions_v11 WHERE status='OPEN' ORDER BY id DESC LIMIT 1;")

    def today_canaries(self) -> int:
        r = self.q1("""
            SELECT COUNT(*) AS n
            FROM paper_micro_canary_positions_v11
            WHERE substr(opened_at,1,10)=substr(datetime('now'),1,10);
        """)
        return inum(r.get("n"))

    def price_for(self, prices: Dict[str, Any], symbol: str) -> float:
        v = prices.get(symbol)
        if isinstance(v, dict):
            return fnum(v.get("price") or v.get("last") or v.get("close"))
        return fnum(v)

    def risk_pct_for(self, horizon_min: int) -> float:
        if horizon_min <= 15:
            return 0.006
        if horizon_min <= 45:
            return 0.008
        if horizon_min <= 120:
            return 0.010
        return 0.012

    def prices_for_risk(self, entry: float, side: str, risk_pct: float) -> Dict[str, float]:
        if side.upper() == "LONG":
            return {"stop": entry * (1.0 - risk_pct), "take_profit": entry * (1.0 + 1.5 * risk_pct)}
        return {"stop": entry * (1.0 + risk_pct), "take_profit": entry * (1.0 - 1.5 * risk_pct)}

    def net_result(self, entry: float, px: float, side: str, size: float, risk_pct: float) -> Dict[str, float]:
        direction = 1.0 if side.upper() == "LONG" else -1.0
        ret_pct = direction * ((px - entry) / entry) if entry > 0 else 0.0
        gross_usd = size * ret_pct
        gross_r = ret_pct / risk_pct if risk_pct > 0 else 0.0
        fee_usd = size * fnum(getattr(CFG, "fee_rate", 0.00045)) * 2.0
        slippage_usd = size * (fnum(getattr(CFG, "slippage_base_bps", 1.5)) / 10000.0) * 2.0
        net_usd = gross_usd - fee_usd - slippage_usd
        net_r = net_usd / (size * risk_pct) if size > 0 and risk_pct > 0 else 0.0
        return {
            "ret_pct": ret_pct,
            "gross_usd": gross_usd,
            "gross_r": gross_r,
            "fee_usd": fee_usd,
            "slippage_usd": slippage_usd,
            "net_usd": net_usd,
            "net_r": net_r,
        }

    def manage_open(self, prices: Dict[str, Any]) -> Dict[str, Any]:
        p = self.open_canary()
        if not p:
            return {"managed": False, "reason": "NO_OPEN_CANARY"}

        symbol = str(p.get("symbol"))
        side = str(p.get("side")).upper()
        px = self.price_for(prices, symbol)
        if px <= 0:
            return {"managed": False, "reason": "NO_PRICE_FOR_OPEN_CANARY", "symbol": symbol}

        entry = fnum(p.get("entry_price"))
        size = fnum(p.get("size_usd"))
        horizon = inum(p.get("horizon_min"))
        risk_pct = fnum(p.get("initial_risk_pct"), self.risk_pct_for(horizon))
        res = self.net_result(entry, px, side, size, risk_pct)

        old_mfe = fnum(p.get("mfe_r"))
        old_mae = fnum(p.get("mae_r"))
        mfe = max(old_mfe, res["gross_r"])
        mae = min(old_mae, res["gross_r"])

        opened = parse_ts(p.get("opened_at"))
        age_min = (datetime.now(timezone.utc) - opened).total_seconds() / 60.0 if opened else 0.0

        close = False
        reason = "MARK_TO_MARKET_NET"
        if res["net_r"] <= -1.0:
            close = True
            reason = "V11_CANARY_STOP_NET_-1R"
        elif res["net_r"] >= 1.5:
            close = True
            reason = "V11_CANARY_TAKE_PROFIT_NET_1_5R"
        elif age_min >= max(10, horizon):
            close = True
            reason = "V11_CANARY_HORIZON_CLOSE"

        if close:
            self.db.execute("""
                UPDATE paper_micro_canary_positions_v11
                SET status='CLOSED', closed_at=?, exit_price=?, pnl_usd=?, pnl_r=?,
                    net_pnl_usd=?, net_pnl_r=?, mfe_r=?, mae_r=?,
                    fee_usd_est=?, slippage_usd_est=?, reason=?
                WHERE id=?;
            """, (
                utc_now_iso(), px, res["gross_usd"], res["gross_r"],
                res["net_usd"], res["net_r"], mfe, mae,
                res["fee_usd"], res["slippage_usd"], reason, inum(p.get("id")),
            ))
            self.audit("CANARY_CLOSED", "INFO", reason, {
                "id": inum(p.get("id")), "symbol": symbol, "side": side,
                "entry": entry, "exit": px, "gross_r": res["gross_r"], "net_r": res["net_r"],
                "age_min": age_min, "mfe_r": mfe, "mae_r": mae,
            })
            return {"managed": True, "closed": True, "reason": reason, "net_pnl_r": res["net_r"], "net_pnl_usd": res["net_usd"]}

        self.db.execute("UPDATE paper_micro_canary_positions_v11 SET mfe_r=?, mae_r=?, pnl_usd=?, pnl_r=?, net_pnl_usd=?, net_pnl_r=? WHERE id=?;",
                        (mfe, mae, res["gross_usd"], res["gross_r"], res["net_usd"], res["net_r"], inum(p.get("id"))))
        return {"managed": True, "closed": False, "reason": reason, "net_pnl_r": res["net_r"], "gross_pnl_r": res["gross_r"], "age_min": age_min}

    def maybe_open(self, prices: Dict[str, Any], allow_open: bool = True) -> Dict[str, Any]:
        c = self.latest_control()
        if not c:
            return {"opened": False, "reason": "NO_CONTROL_V11"}
        if not allow_open:
            return {"opened": False, "reason": "AUDIT_MODE_NO_OPEN"}
        if inum(c.get("allow_paper_micro_canary")) != 1:
            return {"opened": False, "reason": "CONTROL_NOT_ALLOWING_MICRO_CANARY", "state": c.get("global_state")}
        if self.open_canary():
            return {"opened": False, "reason": "OPEN_CANARY_EXISTS"}

        if self.today_canaries() >= 1:
            return {"opened": False, "reason": "DAILY_CANARY_LIMIT_REACHED_BY_BRIDGE"}

        symbol = str(c.get("edge_symbol") or "")
        side = str(c.get("edge_side") or "").upper()
        px = self.price_for(prices, symbol)
        if not symbol or side not in ("LONG", "SHORT") or px <= 0:
            return {"opened": False, "reason": "NO_VALID_PRICE_OR_SIDE", "symbol": symbol, "side": side, "price": px}

        horizon = inum(c.get("edge_horizon_min"))
        risk_pct = self.risk_pct_for(horizon)
        pts = self.prices_for_risk(px, side, risk_pct)
        size = min(50.0, max(10.0, fnum(c.get("max_size_usd"), 15.0)))
        est_fee = size * fnum(getattr(CFG, "fee_rate", 0.00045)) * 2.0
        est_slip = size * (fnum(getattr(CFG, "slippage_base_bps", 1.5)) / 10000.0) * 2.0

        payload = {
            "flow_hash": c.get("flow_hash"),
            "decision_tier": c.get("decision_tier"),
            "control_state": c.get("global_state"),
            "control_score": c.get("control_score"),
            "confidence_score": c.get("confidence_score"),
            "derivatives_state": c.get("derivatives_state"),
            "shadow_regime_state": c.get("shadow_regime_state"),
            "required_execution_mode": c.get("required_execution_mode"),
            "decision_contract_json": c.get("decision_contract_json"),
            "paper_only": True,
            "single_final_authority": True,
            "source": VERSION,
        }
        self.db.execute("""
            INSERT INTO paper_micro_canary_positions_v11 (
                opened_at, closed_at, symbol, side, family_name, setup, profile, horizon_min, status,
                entry_price, exit_price, stop_price, take_profit_price, initial_risk_pct, size_usd,
                pnl_usd, pnl_r, net_pnl_usd, net_pnl_r, mfe_r, mae_r, fee_usd_est, slippage_usd_est,
                control_id, source_edge_id, reason, payload
            ) VALUES (?, NULL, ?, ?, ?, ?, ?, ?, 'OPEN', ?, NULL, ?, ?, ?, ?, 0.0, 0.0, ?, ?, 0.0, 0.0, ?, ?, ?, ?, ?, ?);
        """, (
            utc_now_iso(), symbol, side, str(c.get("edge_family") or "UNKNOWN"), str(c.get("edge_setup") or "UNKNOWN"), str(c.get("edge_profile") or "UNKNOWN"), horizon,
            px, pts["stop"], pts["take_profit"], risk_pct, size,
            -(est_fee + est_slip), -(est_fee + est_slip) / (size * risk_pct) if size * risk_pct > 0 else 0.0,
            est_fee, est_slip, inum(c.get("id")), inum(c.get("source_edge_id")),
            "CONTROL_V11_PAPER_MICRO_CANARY_READY", js(payload),
        ))
        self.audit("CANARY_OPENED", "INFO", "V11 paper micro canary opened", {
            "symbol": symbol, "side": side, "entry": px, "size": size,
            "risk_pct": risk_pct, "stop": pts["stop"], "take_profit": pts["take_profit"],
            "control_id": inum(c.get("id")), "source_edge_id": inum(c.get("source_edge_id")),
        })
        return {"opened": True, "symbol": symbol, "side": side, "price": px, "size_usd": size, "risk_pct": risk_pct}

    def refresh(self, prices: Dict[str, Any], allow_open: bool = True) -> Dict[str, Any]:
        self.ensure_schema()
        managed = self.manage_open(prices)
        opened = self.maybe_open(prices, allow_open=allow_open)
        return {"version": VERSION, "managed": managed, "opened": opened}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--schema", action="store_true")
    args = parser.parse_args()
    engine = PaperMicroCanaryBridgeV11()
    engine.ensure_schema()
    if args.schema:
        print(json.dumps({"version": VERSION, "schema": "ok"}, indent=2))


if __name__ == "__main__":
    main()
