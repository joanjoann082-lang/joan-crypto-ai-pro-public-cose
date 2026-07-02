from __future__ import annotations

import argparse
import json
import re
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

DB_PATH = "data/joanbot_v14.sqlite"
VERSION = "V11_5_POSITION_MANAGER_INSTITUTIONAL"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utc_now().isoformat()


def parse_dt(x: Any) -> Optional[datetime]:
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


def fnum(x: Any, default: float = 0.0) -> float:
    try:
        if x is None or x == "":
            return default
        return float(x)
    except Exception:
        return default


def inum(x: Any, default: int = 0) -> int:
    try:
        if x is None or x == "":
            return default
        return int(float(x))
    except Exception:
        return default


def js(x: Any) -> str:
    return json.dumps(x, separators=(",", ":"), ensure_ascii=False, default=str)


class V11PositionManager:
    def __init__(self, db_path: str = DB_PATH) -> None:
        self.db_path = db_path
        self.grace_min = 5
        self.roundtrip_fee_rate = 0.0008

    def connect(self):
        con = sqlite3.connect(self.db_path, timeout=30)
        con.row_factory = sqlite3.Row
        return con

    def table_exists(self, cur, name: str) -> bool:
        return cur.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE name=?",
            (name,),
        ).fetchone()[0] > 0

    def cols(self, cur, table: str) -> List[str]:
        return [r[1] for r in cur.execute(f"PRAGMA table_info({table})").fetchall()]

    def add_col(self, cur, table: str, col: str, typ: str) -> None:
        if col not in self.cols(cur, table):
            cur.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ};")

    def ensure_schema(self, cur) -> None:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS v11_position_manager_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                version TEXT NOT NULL,
                event TEXT NOT NULL,
                canary_id INTEGER,
                symbol TEXT,
                side TEXT,
                status TEXT,
                reason TEXT,
                payload TEXT NOT NULL
            );
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS v11_telegram_dedupe (
                dedupe_key TEXT PRIMARY KEY,
                ts TEXT NOT NULL
            );
        """)

        table = "paper_micro_canary_positions_v11"
        if self.table_exists(cur, table):
            for col, typ in [
                ("risk_usd", "REAL"),
                ("fees_usd", "REAL"),
                ("gross_usd", "REAL"),
                ("net_usd", "REAL"),
                ("gross_pnl_r", "REAL"),
                ("net_pnl_r", "REAL"),
                ("stop_loss_price", "REAL"),
                ("take_profit_price", "REAL"),
                ("last_managed_at", "TEXT"),
                ("manager_version", "TEXT"),
                ("manager_state", "TEXT"),
            ]:
                self.add_col(cur, table, col, typ)

    def audit(self, cur, event: str, payload: Dict[str, Any]) -> None:
        cur.execute("""
            INSERT INTO v11_position_manager_audit (
                ts, version, event, canary_id, symbol, side, status, reason, payload
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
        """, (
            iso_now(),
            VERSION,
            event,
            payload.get("id"),
            payload.get("symbol"),
            payload.get("side"),
            payload.get("status"),
            payload.get("reason"),
            js(payload),
        ))

    def notify_once(self, cur, key: str, text: str) -> None:
        try:
            cur.execute(
                "INSERT INTO v11_telegram_dedupe (dedupe_key, ts) VALUES (?, ?)",
                (key, iso_now()),
            )
        except sqlite3.IntegrityError:
            return

        try:
            from joanbot.integrations.telegram_v11 import send_message
            send_message(text)
        except Exception:
            pass

    def latest_price(self, cur, symbol: str) -> float:
        if not self.table_exists(cur, "market_snapshots"):
            raise RuntimeError("NO_MARKET_SNAPSHOTS")

        cols = self.cols(cur, "market_snapshots")
        for pc in ["price", "last_price", "close", "mark_price"]:
            if pc in cols:
                row = cur.execute(
                    f"""
                    SELECT {pc} AS px
                    FROM market_snapshots
                    WHERE symbol=?
                      AND {pc} IS NOT NULL
                    ORDER BY rowid DESC
                    LIMIT 1;
                    """,
                    (symbol,),
                ).fetchone()
                if row and row["px"] is not None:
                    return float(row["px"])

        raise RuntimeError(f"NO_PRICE_FOR_{symbol}")

    def parse_profile_risk(self, profile: str) -> Dict[str, float]:
        profile = profile or ""

        tp_mult = 2.0
        sl_mult = 1.0

        tp = re.search(r"TP(\d+)_(\d+)", profile)
        sl = re.search(r"SL(\d+)_(\d+)", profile)

        if tp:
            tp_mult = float(f"{tp.group(1)}.{tp.group(2)}")
        if sl:
            sl_mult = float(f"{sl.group(1)}.{sl.group(2)}")

        stop_pct = max(0.004, min(0.025, sl_mult / 100.0))
        take_pct = max(0.004, min(0.05, tp_mult * stop_pct))

        return {
            "tp_mult": tp_mult,
            "sl_mult": sl_mult,
            "stop_pct": stop_pct,
            "take_pct": take_pct,
        }

    def fill_missing_risk_levels(self, cur, d: Dict[str, Any]) -> Dict[str, float]:
        cid = d["id"]
        entry = fnum(d.get("entry_price"))
        side = str(d.get("side") or "").upper()
        size = fnum(d.get("size_usd"))
        profile = str(d.get("profile") or "")

        risk = self.parse_profile_risk(profile)
        stop_pct = risk["stop_pct"]
        take_pct = risk["take_pct"]

        if side == "LONG":
            sl = fnum(d.get("stop_loss_price")) or entry * (1 - stop_pct)
            tp = fnum(d.get("take_profit_price")) or entry * (1 + take_pct)
        else:
            sl = fnum(d.get("stop_loss_price")) or entry * (1 + stop_pct)
            tp = fnum(d.get("take_profit_price")) or entry * (1 - take_pct)

        risk_usd = fnum(d.get("risk_usd")) or size * abs(entry - sl) / entry

        cur.execute("""
            UPDATE paper_micro_canary_positions_v11
            SET stop_loss_price=?,
                take_profit_price=?,
                risk_usd=?,
                manager_version=?,
                manager_state='RISK_LEVELS_READY',
                last_managed_at=?
            WHERE id=?;
        """, (sl, tp, risk_usd, VERSION, iso_now(), cid))

        return {
            "stop_loss_price": sl,
            "take_profit_price": tp,
            "risk_usd": risk_usd,
        }

    def exit_reason(self, side: str, px: float, sl: float, tp: float, age_min: float, horizon: int) -> Optional[str]:
        if side == "LONG":
            if px <= sl:
                return "STOP_LOSS_HIT"
            if px >= tp:
                return "TAKE_PROFIT_HIT"

        if side == "SHORT":
            if px >= sl:
                return "STOP_LOSS_HIT"
            if px <= tp:
                return "TAKE_PROFIT_HIT"

        if horizon > 0 and age_min >= horizon + self.grace_min:
            return "HORIZON_EXPIRED"

        return None

    def pnl(self, side: str, entry: float, exit_px: float, size: float, risk_usd: float) -> Dict[str, float]:
        if side == "LONG":
            gross_usd = size * ((exit_px - entry) / entry)
        else:
            gross_usd = size * ((entry - exit_px) / entry)

        fees_usd = size * self.roundtrip_fee_rate
        net_usd = gross_usd - fees_usd
        net_r = net_usd / risk_usd if risk_usd > 0 else 0.0
        gross_r = gross_usd / risk_usd if risk_usd > 0 else 0.0

        return {
            "gross_usd": gross_usd,
            "fees_usd": fees_usd,
            "net_usd": net_usd,
            "pnl_usd": net_usd,
            "gross_pnl_r": gross_r,
            "net_pnl_r": net_r,
            "pnl_r": net_r,
        }

    def run_once(self) -> Dict[str, Any]:
        con = self.connect()
        cur = con.cursor()
        self.ensure_schema(cur)

        result = {
            "version": VERSION,
            "checked": 0,
            "closed": 0,
            "kept_open": 0,
            "errors": [],
            "actions": [],
        }

        table = "paper_micro_canary_positions_v11"

        if not self.table_exists(cur, table):
            result["state"] = "NO_V11_CANARY_TABLE"
            con.commit()
            con.close()
            return result

        rows = cur.execute(f"""
            SELECT *
            FROM {table}
            WHERE status='OPEN' OR closed_at IS NULL
            ORDER BY id ASC;
        """).fetchall()

        result["checked"] = len(rows)

        for row in rows:
            d = dict(row)

            try:
                cid = d["id"]
                symbol = d.get("symbol")
                side = str(d.get("side") or "").upper()
                entry = fnum(d.get("entry_price"))
                size = fnum(d.get("size_usd"))
                horizon = inum(d.get("horizon_min"))
                opened = parse_dt(d.get("opened_at"))

                if not opened or not symbol or side not in {"LONG", "SHORT"} or entry <= 0 or size <= 0:
                    action = {"id": cid, "status": "SKIP_INVALID_CANARY"}
                    result["actions"].append(action)
                    self.audit(cur, "skip_invalid", action)
                    continue

                age_min = (utc_now() - opened).total_seconds() / 60.0
                current_px = self.latest_price(cur, symbol)
                levels = self.fill_missing_risk_levels(cur, d)

                reason = self.exit_reason(
                    side=side,
                    px=current_px,
                    sl=levels["stop_loss_price"],
                    tp=levels["take_profit_price"],
                    age_min=age_min,
                    horizon=horizon,
                )

                if not reason:
                    action = {
                        "id": cid,
                        "symbol": symbol,
                        "side": side,
                        "status": "KEEP_OPEN",
                        "age_min": round(age_min, 2),
                        "horizon_min": horizon,
                        "entry": round(entry, 4),
                        "current": round(current_px, 4),
                        "sl": round(levels["stop_loss_price"], 4),
                        "tp": round(levels["take_profit_price"], 4),
                    }
                    result["kept_open"] += 1
                    result["actions"].append(action)
                    self.audit(cur, "keep_open", action)
                    continue

                pnl = self.pnl(
                    side=side,
                    entry=entry,
                    exit_px=current_px,
                    size=size,
                    risk_usd=levels["risk_usd"],
                )

                cur.execute("""
                    UPDATE paper_micro_canary_positions_v11
                    SET closed_at=?,
                        exit_price=?,
                        status='CLOSED',
                        gross_usd=?,
                        fees_usd=?,
                        net_usd=?,
                        pnl_usd=?,
                        gross_pnl_r=?,
                        net_pnl_r=?,
                        pnl_r=?,
                        reason=?,
                        manager_version=?,
                        manager_state='CLOSED_BY_POSITION_MANAGER',
                        last_managed_at=?
                    WHERE id=?
                      AND (status='OPEN' OR closed_at IS NULL);
                """, (
                    iso_now(),
                    current_px,
                    pnl["gross_usd"],
                    pnl["fees_usd"],
                    pnl["net_usd"],
                    pnl["pnl_usd"],
                    pnl["gross_pnl_r"],
                    pnl["net_pnl_r"],
                    pnl["pnl_r"],
                    f"V11_5_{reason}",
                    VERSION,
                    iso_now(),
                    cid,
                ))

                action = {
                    "id": cid,
                    "symbol": symbol,
                    "side": side,
                    "status": "CLOSED",
                    "reason": reason,
                    "entry": round(entry, 4),
                    "exit": round(current_px, 4),
                    "size_usd": size,
                    "net_usd": round(pnl["net_usd"], 6),
                    "net_pnl_r": round(pnl["net_pnl_r"], 6),
                    "age_min": round(age_min, 2),
                    "horizon_min": horizon,
                }

                result["closed"] += 1
                result["actions"].append(action)
                self.audit(cur, "closed", action)

                self.notify_once(
                    cur,
                    f"v11_canary_closed_{cid}",
                    "📌 JoanBot V11 canary CLOSED\n"
                    f"{symbol} {side}\n"
                    f"Reason: {reason}\n"
                    f"Entry: {entry:.2f}\n"
                    f"Exit: {current_px:.2f}\n"
                    f"Net: {pnl['net_usd']:.4f}$ / {pnl['net_pnl_r']:.4f}R"
                )

            except Exception as e:
                err = {"id": d.get("id"), "error": repr(e)}
                result["errors"].append(err)
                self.audit(cur, "error", err)

        result["state"] = "OK"
        con.commit()
        con.close()
        return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    print(json.dumps(V11PositionManager().run_once(), indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
