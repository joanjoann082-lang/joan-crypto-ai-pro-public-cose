from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from joanbot.storage.db import get_db
from joanbot.utils import utc_now_iso, fnum

VERSION = "UNIVERSAL_SHADOW_ALPHA_LOOP_V2_ISOLATED_COGNITIVE_ALPHA"

MAX_CASES_PER_CYCLE = int(os.environ.get("UNIVERSAL_ALPHA_V2_MAX_CASES_PER_CYCLE", "24"))
MIN_DUPLICATE_SECONDS = int(os.environ.get("UNIVERSAL_ALPHA_V2_MIN_DUPLICATE_SECONDS", "600"))
MAX_PENDING_CASES = int(os.environ.get("UNIVERSAL_ALPHA_V2_MAX_PENDING_CASES", "800"))
MAX_CASE_ROWS = int(os.environ.get("UNIVERSAL_ALPHA_V2_MAX_CASE_ROWS", "2500"))
MAX_RESULT_ROWS = int(os.environ.get("UNIVERSAL_ALPHA_V2_MAX_RESULT_ROWS", "2500"))
MAX_REGISTRY_ROWS = int(os.environ.get("UNIVERSAL_ALPHA_V2_MAX_REGISTRY_ROWS", "800"))
MAX_AUDIT_ROWS = int(os.environ.get("UNIVERSAL_ALPHA_V2_MAX_AUDIT_ROWS", "200"))

PROFILES = [
    {"profile": "SCALP_15_TP1_2_SL1_0", "horizon_min": 15, "tp_r": 1.20, "sl_r": 1.00},
    {"profile": "SCALP_45_TP1_6_SL1_0", "horizon_min": 45, "tp_r": 1.60, "sl_r": 1.00},
    {"profile": "INTRADAY_120_TP2_0_SL1_1", "horizon_min": 120, "tp_r": 2.00, "sl_r": 1.10},
    {"profile": "SWING_240_TP2_5_SL1_2", "horizon_min": 240, "tp_r": 2.50, "sl_r": 1.20},
]

SETUPS = {
    "LONG": [
        "UAL2_REBOUND_LONG",
        "UAL2_TREND_PULLBACK_LONG",
        "UAL2_SQUEEZE_REVERSAL_LONG",
    ],
    "SHORT": [
        "UAL2_TREND_CONTINUATION_SHORT",
        "UAL2_BOUNCE_FADE_SHORT",
        "UAL2_SQUEEZE_REVERSAL_SHORT",
    ],
}


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def txt(x: Any) -> str:
    return "" if x is None else str(x)


def inum(x: Any, default: int = 0) -> int:
    try:
        if x is None:
            return default
        return int(float(x))
    except Exception:
        return default


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(x)))


def safe_json(x: Any) -> Dict[str, Any]:
    if isinstance(x, dict):
        return x
    if not x:
        return {}
    try:
        y = json.loads(str(x))
        return y if isinstance(y, dict) else {}
    except Exception:
        return {}


def safe_div(a: float, b: float, default: float = 0.0) -> float:
    try:
        if abs(b) < 1e-12:
            return default
        return float(a) / float(b)
    except Exception:
        return default


def parse_ts(x: Any) -> Optional[datetime]:
    s = txt(x)
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


class UniversalShadowAlphaLoopV2:
    """
    Isolated cognitive shadow-alpha loop.

    This module creates and resolves research-only alpha hypotheses.

    It does not use forward_cases/forward_results, so it cannot contaminate:
    - current EvidenceEngine
    - Telegram threshold advisor
    - existing forward tester
    - existing strategy reputation

    It is connected to the runner only as a separate learning step.
    """

    def __init__(self, db=None):
        self.db = db or get_db()
        self._context_engine = None

    def ensure_schema(self) -> None:
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS universal_shadow_cases_v2 (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                due_at TEXT NOT NULL,
                resolved_at TEXT,
                status TEXT NOT NULL,

                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                setup TEXT NOT NULL,
                profile TEXT NOT NULL,
                horizon_min INTEGER NOT NULL,

                entry REAL NOT NULL,
                sl REAL NOT NULL,
                tp1 REAL NOT NULL,
                tp2 REAL NOT NULL,
                risk_abs REAL NOT NULL,

                context_bucket TEXT NOT NULL,
                context_score REAL NOT NULL,
                thesis TEXT NOT NULL,
                counter_thesis TEXT NOT NULL,
                invalidation TEXT NOT NULL,

                payload TEXT NOT NULL
            );
        """)

        self.db.execute("""
            CREATE TABLE IF NOT EXISTS universal_shadow_results_v2 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                case_id TEXT NOT NULL,
                resolved_at TEXT NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                setup TEXT NOT NULL,
                profile TEXT NOT NULL,
                horizon_min INTEGER NOT NULL,

                outcome TEXT NOT NULL,
                result_r REAL NOT NULL,
                mfe_r REAL NOT NULL,
                mae_r REAL NOT NULL,
                bars_seen INTEGER NOT NULL,
                exit_price REAL NOT NULL,

                payload TEXT NOT NULL
            );
        """)

        self.db.execute("""
            CREATE TABLE IF NOT EXISTS universal_shadow_registry_v2 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                version TEXT NOT NULL,
                alpha_key TEXT NOT NULL,

                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                setup TEXT NOT NULL,
                profile TEXT NOT NULL,
                horizon_min INTEGER NOT NULL,
                context_bucket TEXT NOT NULL,

                n INTEGER NOT NULL,
                expectancy_r REAL NOT NULL,
                winrate REAL NOT NULL,
                profit_factor REAL,
                avg_mfe_r REAL NOT NULL,
                avg_mae_r REAL NOT NULL,
                train_exp_r REAL NOT NULL,
                validation_exp_r REAL NOT NULL,
                stability_score REAL NOT NULL,
                quality_score REAL NOT NULL,

                state TEXT NOT NULL,
                recommendation TEXT NOT NULL,
                reasons TEXT NOT NULL,
                payload TEXT NOT NULL
            );
        """)

        self.db.execute("""
            CREATE TABLE IF NOT EXISTS universal_shadow_alpha_audit_v2 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                version TEXT NOT NULL,
                event TEXT NOT NULL,
                level TEXT NOT NULL,
                message TEXT NOT NULL,
                payload TEXT NOT NULL
            );
        """)

        self.db.execute("""
            CREATE INDEX IF NOT EXISTS idx_universal_shadow_cases_v2_status_due
            ON universal_shadow_cases_v2(status, due_at);
        """)

        self.db.execute("""
            CREATE INDEX IF NOT EXISTS idx_universal_shadow_registry_v2_key
            ON universal_shadow_registry_v2(alpha_key, id);
        """)

        self.db.execute("DROP VIEW IF EXISTS latest_universal_shadow_registry_v2;")
        self.db.execute("""
            CREATE VIEW latest_universal_shadow_registry_v2 AS
            SELECT r.*
            FROM universal_shadow_registry_v2 r
            JOIN (
                SELECT alpha_key, MAX(id) AS max_id
                FROM universal_shadow_registry_v2
                GROUP BY alpha_key
            ) x ON x.max_id = r.id;
        """)

    def audit(self, event: str, level: str, message: str, payload: Dict[str, Any]) -> None:
        self.db.execute("""
            INSERT INTO universal_shadow_alpha_audit_v2 (
                ts, version, event, level, message, payload
            )
            VALUES (?, ?, ?, ?, ?, ?);
        """, (
            utc_now_iso(),
            VERSION,
            event,
            level,
            message[:500],
            json.dumps(payload, separators=(",", ":"), ensure_ascii=False),
        ))

    def context_engine(self):
        if self._context_engine is not None:
            return self._context_engine
        try:
            from joanbot.features.context import ContextEngine
            self._context_engine = ContextEngine()
        except Exception:
            self._context_engine = False
        return self._context_engine

    def latest_contexts_from_db(self) -> Dict[str, Dict[str, Any]]:
        rows = self.db.query("""
            SELECT id, ts, symbol, price, payload
            FROM market_snapshots
            WHERE symbol IN ('BTCUSDT','ETHUSDT')
            ORDER BY id DESC
            LIMIT 40;
        """)

        ce = self.context_engine()
        out: Dict[str, Dict[str, Any]] = {}

        for r in rows:
            symbol = txt(r.get("symbol")).upper()
            if symbol in out:
                continue

            raw = safe_json(r.get("payload"))
            raw["symbol"] = symbol
            raw["price"] = fnum(r.get("price"), 0.0)

            ctx = raw
            if ce:
                try:
                    ctx = ce.build(
                        raw,
                        {
                            "macro": raw.get("macro", {"risk_score": 50}),
                            "news": raw.get("news", {"severity": 0}),
                            "calendar": raw.get("calendar", {}),
                        },
                    )
                except Exception:
                    ctx = raw

            ctx["symbol"] = symbol
            ctx["price"] = fnum(ctx.get("price"), fnum(r.get("price"), 0.0))
            out[symbol] = ctx

        return out

    def market_path(self, symbol: str, created_at: str, due_at: str) -> List[Dict[str, Any]]:
        return self.db.query("""
            SELECT ts, price
            FROM market_snapshots
            WHERE symbol=?
              AND ts >= ?
              AND ts <= ?
              AND price IS NOT NULL
            ORDER BY ts ASC
            LIMIT 1200;
        """, (symbol, created_at, due_at))

    def atr_abs(self, ctx: Dict[str, Any]) -> float:
        price = fnum(ctx.get("price"), 0.0)
        tech = ctx.get("technical", {}) or {}
        tf = tech.get("timeframes", {}) or {}
        tf1h = tf.get("1h", {}) or {}
        atr = fnum(tf1h.get("atr"), 0.0)
        atr_pct = fnum(tf1h.get("atr_pct"), 0.0)

        if atr <= 0 and price > 0:
            atr = price * max(0.004, atr_pct / 100.0 if atr_pct > 0 else 0.008)

        return max(atr, price * 0.003 if price > 0 else 1.0)

    def context_bucket(self, ctx: Dict[str, Any]) -> str:
        tech = ctx.get("technical", {}) or {}
        tf = tech.get("timeframes", {}) or {}
        tf15 = tf.get("15m", {}) or {}
        tf1h = tf.get("1h", {}) or {}

        regime = txt(ctx.get("regime") or "UNKNOWN").upper()
        session = txt(ctx.get("session") or "UNKNOWN").upper()
        vol = txt(ctx.get("volatility_bucket") or "UNKNOWN").upper()

        rsi15 = fnum(tf15.get("rsi"), 50.0)
        score1h = fnum(tf1h.get("score"), 0.0)

        rsi_bucket = "RSI_LOW" if rsi15 < 38 else "RSI_HIGH" if rsi15 > 65 else "RSI_MID"
        trend_bucket = "S1H_BEAR" if score1h < -8 else "S1H_BULL" if score1h > 8 else "S1H_FLAT"

        return f"{regime}|{session}|{vol}|{rsi_bucket}|{trend_bucket}"

    def thesis(self, ctx: Dict[str, Any], side: str, setup: str) -> Tuple[float, List[str], str, str, str]:
        tech = ctx.get("technical", {}) or {}
        tf = tech.get("timeframes", {}) or {}
        tf15 = tf.get("15m", {}) or {}
        tf1h = tf.get("1h", {}) or {}
        tf4h = tf.get("4h", {}) or {}
        flags = ctx.get("flags", {}) or {}
        macro = ctx.get("macro", {}) or {}
        news = ctx.get("news", {}) or {}

        regime = txt(ctx.get("regime") or "UNKNOWN").upper()
        rsi15 = fnum(tf15.get("rsi"), 50.0)
        s15 = fnum(tf15.get("score"), 0.0)
        s1h = fnum(tf1h.get("score"), 0.0)
        s4h = fnum(tf4h.get("score"), 0.0)
        macro_risk = fnum(macro.get("risk_score"), 50.0)
        news_sev = fnum(news.get("severity"), 0.0)
        squeeze = fnum(flags.get("squeeze_risk"), 0.0)

        score = 50.0
        reasons: List[str] = []

        if side == "LONG":
            if "REBOUND" in setup:
                if regime in ("RANGE_CHOP", "TREND_DOWN", "MIXED"):
                    score += 8
                    reasons.append("REBOUND_COMPATIBLE_REGIME")
                if rsi15 < 40:
                    score += clamp((40 - rsi15) * 1.15, 0, 18)
                    reasons.append("RSI15_REBOUND_ZONE")
                if s1h < 0:
                    score += 5
                    reasons.append("1H_WEAKNESS_CAN_MEAN_REVERSION")
                if flags.get("late_short"):
                    score += 10
                    reasons.append("LATE_SHORT_EXHAUSTION")
                thesis = "Long rebound hypothesis: weak/oversold context may revert before trend resumes."
                counter = "Could be a falling knife if 1H/4H trend continues and liquidity does not absorb selling."
                invalid = "Invalid if price accepts below local support or MAE reaches SL before any MFE expansion."

            elif "PULLBACK" in setup:
                if s4h > 0:
                    score += 8
                    reasons.append("4H_BULL_SUPPORT")
                if s1h > 0:
                    score += 8
                    reasons.append("1H_BULL_SUPPORT")
                if rsi15 > 72:
                    score -= 12
                    reasons.append("OVERHEATED_LONG_RISK")
                thesis = "Trend pullback long hypothesis: higher timeframe strength may resume after short-term reset."
                counter = "Could fail if pullback becomes reversal or macro/news risk expands."
                invalid = "Invalid if 1H score flips negative with expanding downside volatility."

            else:
                if squeeze > 20:
                    score += 10
                    reasons.append("SQUEEZE_PRESENT")
                if rsi15 < 36:
                    score += 5
                    reasons.append("LOW_RSI_SQUEEZE_OPTION")
                thesis = "Long squeeze reversal hypothesis: compression may resolve upward after downside exhaustion."
                counter = "Compression can also break down if sellers control liquidation flow."
                invalid = "Invalid if breakdown accelerates through SL before upside expansion."

        else:
            if "CONTINUATION" in setup:
                if s4h < 0:
                    score += 9
                    reasons.append("4H_BEAR_SUPPORT")
                if s1h < 0:
                    score += 9
                    reasons.append("1H_BEAR_SUPPORT")
                if rsi15 < 34:
                    score -= 12
                    reasons.append("OVERSOLD_SHORT_RISK")
                thesis = "Short continuation hypothesis: bearish higher timeframe structure may continue."
                counter = "Could fail if market is already oversold and squeezes upward."
                invalid = "Invalid if price reclaims local resistance with positive 15m/1h impulse."

            elif "BOUNCE_FADE" in setup:
                if s4h < 0 and s1h < 0 and s15 > 0:
                    score += 14
                    reasons.append("BEAR_TREND_WITH_15M_BOUNCE")
                if flags.get("late_short"):
                    score -= 12
                    reasons.append("LATE_SHORT_RISK")
                thesis = "Bounce-fade short hypothesis: weak HTF trend may reject a short-term bounce."
                counter = "Could fail if bounce becomes reversal and trapped shorts cover."
                invalid = "Invalid if 15m strength expands into 1H trend improvement."

            else:
                if squeeze > 20:
                    score += 10
                    reasons.append("SQUEEZE_PRESENT")
                if rsi15 > 64:
                    score += 5
                    reasons.append("HIGH_RSI_SQUEEZE_OPTION")
                thesis = "Short squeeze reversal hypothesis: compression may resolve downward after upside exhaustion."
                counter = "Could fail if compression breaks upward into short squeeze."
                invalid = "Invalid if breakout above TP-side resistance persists."

        if macro_risk > 75:
            score -= 10
            reasons.append("MACRO_RISK_HIGH")

        if news_sev > 75:
            score -= 12
            reasons.append("NEWS_RISK_HIGH")

        return clamp(score, 0, 100), reasons, thesis, counter, invalid

    def duplicate_exists(self, symbol: str, side: str, setup: str, profile: str, horizon_min: int) -> bool:
        since = iso(now_utc() - timedelta(seconds=MIN_DUPLICATE_SECONDS))
        rows = self.db.query("""
            SELECT id
            FROM universal_shadow_cases_v2
            WHERE symbol=?
              AND side=?
              AND setup=?
              AND profile=?
              AND horizon_min=?
              AND created_at >= ?
            LIMIT 1;
        """, (symbol, side, setup, profile, horizon_min, since))
        return bool(rows)

    def make_case_id(self, symbol: str, side: str, setup: str, profile: str, horizon_min: int, bucket: str) -> str:
        raw = f"{VERSION}|{symbol}|{side}|{setup}|{profile}|{horizon_min}|{bucket}"
        return "usal2_" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:18]

    def candidate_rows(self, contexts: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []

        for symbol, ctx in contexts.items():
            symbol = txt(symbol).upper()
            if symbol not in ("BTCUSDT", "ETHUSDT"):
                continue

            price = fnum(ctx.get("price"), 0.0)
            if price <= 0:
                continue

            dq = ctx.get("data_quality", {}) or {}
            if dq and dq.get("hard_ok") is False:
                continue

            atr = self.atr_abs(ctx)
            bucket = self.context_bucket(ctx)

            for side, setups in SETUPS.items():
                for setup in setups:
                    ctx_score, reasons, thesis, counter, invalid = self.thesis(ctx, side, setup)

                    if ctx_score < 38:
                        continue

                    for profile in PROFILES:
                        rows.append({
                            "symbol": symbol,
                            "ctx": ctx,
                            "side": side,
                            "setup": setup,
                            "profile": profile,
                            "price": price,
                            "atr": atr,
                            "context_bucket": bucket,
                            "context_score": ctx_score,
                            "reasons": reasons,
                            "thesis": thesis,
                            "counter": counter,
                            "invalid": invalid,
                        })

        rows.sort(key=lambda r: (r["context_score"], r["symbol"] == "BTCUSDT"), reverse=True)
        return rows

    def register_case(self, row: Dict[str, Any]) -> bool:
        symbol = row["symbol"]
        side = row["side"]
        setup = row["setup"]
        profile = row["profile"]
        profile_name = txt(profile["profile"])
        horizon = int(profile["horizon_min"])

        if self.duplicate_exists(symbol, side, setup, profile_name, horizon):
            return False

        price = fnum(row["price"], 0.0)
        atr = fnum(row["atr"], 0.0)
        tp_r = fnum(profile["tp_r"], 1.5)
        sl_r = fnum(profile["sl_r"], 1.0)
        risk_abs = max(atr * sl_r, price * 0.0025)

        if side == "LONG":
            sl = price - risk_abs
            tp1 = price + atr * tp_r
            tp2 = price + atr * tp_r * 1.65
        else:
            sl = price + risk_abs
            tp1 = price - atr * tp_r
            tp2 = price - atr * tp_r * 1.65

        now = now_utc()
        due = now + timedelta(minutes=horizon)
        bucket_time = now.strftime("%Y%m%d%H%M")
        case_id = self.make_case_id(symbol, side, setup, profile_name, horizon, bucket_time)

        payload = {
            "source": VERSION,
            "learning_only": True,
            "no_execution": True,
            "no_forward_table_pollution": True,
            "thinking": {
                "thesis": row["thesis"],
                "counter_thesis": row["counter"],
                "invalidation": row["invalid"],
                "why_shadow": "Research hypothesis only. It must prove statistical edge before any future micro-canary.",
                "learning_goal": "Estimate result_R, MFE_R and MAE_R for this scenario/profile/context bucket.",
                "reason_vector": row["reasons"],
            },
            "context": {
                "bucket": row["context_bucket"],
                "score": round(row["context_score"], 4),
                "regime": txt(row["ctx"].get("regime")),
                "session": txt(row["ctx"].get("session")),
                "volatility_bucket": txt(row["ctx"].get("volatility_bucket")),
                "flags": row["ctx"].get("flags", {}),
                "data_quality": row["ctx"].get("data_quality", {}),
                "macro": row["ctx"].get("macro", {}),
                "news": row["ctx"].get("news", {}),
            },
            "profile": profile,
        }

        self.db.execute("""
            INSERT OR IGNORE INTO universal_shadow_cases_v2 (
                id, created_at, due_at, resolved_at, status,
                symbol, side, setup, profile, horizon_min,
                entry, sl, tp1, tp2, risk_abs,
                context_bucket, context_score,
                thesis, counter_thesis, invalidation,
                payload
            )
            VALUES (?, ?, ?, NULL, 'PENDING', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """, (
            case_id,
            iso(now),
            iso(due),
            symbol,
            side,
            setup,
            profile_name,
            horizon,
            price,
            sl,
            tp1,
            tp2,
            risk_abs,
            row["context_bucket"],
            row["context_score"],
            row["thesis"],
            row["counter"],
            row["invalid"],
            json.dumps(payload, separators=(",", ":"), ensure_ascii=False, sort_keys=True),
        ))

        return True

    def register_from_contexts(self, contexts: Optional[Dict[str, Dict[str, Any]]] = None) -> Dict[str, Any]:
        self.ensure_schema()

        pending = self.db.query("SELECT COUNT(*) AS n FROM universal_shadow_cases_v2 WHERE status='PENDING';")
        pending_n = inum(pending[0].get("n") if pending else 0, 0)

        if pending_n >= MAX_PENDING_CASES:
            result = {
                "version": VERSION,
                "cases_created": 0,
                "pending_n": pending_n,
                "reason": "MAX_PENDING_CASES_REACHED",
            }
            self.audit("REGISTER_SKIPPED", "WARN", "Pending universal shadow alpha queue is full", result)
            return result

        contexts = contexts or self.latest_contexts_from_db()
        candidates = self.candidate_rows(contexts)

        created = 0
        evaluated = len(candidates)

        for row in candidates:
            if created >= MAX_CASES_PER_CYCLE:
                break
            if self.register_case(row):
                created += 1

        result = {
            "version": VERSION,
            "evaluated_candidates": evaluated,
            "cases_created": created,
            "pending_before": pending_n,
            "max_cases_per_cycle": MAX_CASES_PER_CYCLE,
        }

        self.audit("REGISTER_CASES", "INFO", "Universal shadow alpha V2 cases registered", result)
        return result

    def resolve_one(self, case: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        path = self.market_path(txt(case["symbol"]), txt(case["created_at"]), txt(case["due_at"]))
        if not path:
            return None

        side = txt(case["side"]).upper()
        entry = fnum(case["entry"], 0.0)
        sl = fnum(case["sl"], 0.0)
        tp1 = fnum(case["tp1"], 0.0)
        risk_abs = max(fnum(case["risk_abs"], 0.0), 1e-9)

        direction = 1.0 if side == "LONG" else -1.0

        max_fav = 0.0
        max_adv = 0.0
        exit_price = entry
        outcome = "TIME"
        bars = 0

        for p in path:
            px = fnum(p.get("price"), 0.0)
            if px <= 0:
                continue

            bars += 1
            exit_price = px

            move_r = ((px - entry) * direction) / risk_abs
            max_fav = max(max_fav, move_r)
            max_adv = min(max_adv, move_r)

            if side == "LONG":
                sl_hit = px <= sl
                tp_hit = px >= tp1
            else:
                sl_hit = px >= sl
                tp_hit = px <= tp1

            # Conservative order: SL before TP if ambiguity ever exists.
            if sl_hit:
                outcome = "SL"
                result_r = -1.0
                break

            if tp_hit:
                outcome = "TP"
                result_r = fnum(case.get("tp_r"), 1.0)
                result_r = ((tp1 - entry) * direction) / risk_abs
                break
        else:
            raw_r = ((exit_price - entry) * direction) / risk_abs
            result_r = clamp(raw_r, -1.5, 3.0)

        return {
            "case_id": case["id"],
            "symbol": case["symbol"],
            "side": side,
            "setup": case["setup"],
            "profile": case["profile"],
            "horizon_min": inum(case["horizon_min"], 0),
            "outcome": outcome,
            "result_r": round(result_r, 8),
            "mfe_r": round(max_fav, 8),
            "mae_r": round(max_adv, 8),
            "bars_seen": bars,
            "exit_price": exit_price,
            "payload": {
                "source": VERSION,
                "method": "market_snapshots_path_resolution",
                "conservative_same_bar": "SL_FIRST",
                "learning_only": True,
            },
        }

    def resolve_due(self) -> Dict[str, Any]:
        self.ensure_schema()

        now = utc_now_iso()
        cases = self.db.query("""
            SELECT *
            FROM universal_shadow_cases_v2
            WHERE status='PENDING'
              AND due_at <= ?
            ORDER BY due_at ASC
            LIMIT 200;
        """, (now,))

        resolved = 0
        skipped_no_path = 0

        for c in cases:
            r = self.resolve_one(c)
            if r is None:
                skipped_no_path += 1
                continue

            self.db.execute("""
                INSERT INTO universal_shadow_results_v2 (
                    case_id, resolved_at,
                    symbol, side, setup, profile, horizon_min,
                    outcome, result_r, mfe_r, mae_r, bars_seen, exit_price, payload
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """, (
                r["case_id"],
                now,
                r["symbol"],
                r["side"],
                r["setup"],
                r["profile"],
                r["horizon_min"],
                r["outcome"],
                r["result_r"],
                r["mfe_r"],
                r["mae_r"],
                r["bars_seen"],
                r["exit_price"],
                json.dumps(r["payload"], separators=(",", ":"), ensure_ascii=False),
            ))

            self.db.execute("""
                UPDATE universal_shadow_cases_v2
                SET status='RESOLVED', resolved_at=?
                WHERE id=?;
            """, (now, r["case_id"]))

            resolved += 1

        result = {
            "version": VERSION,
            "due_cases": len(cases),
            "resolved": resolved,
            "skipped_no_path": skipped_no_path,
        }

        self.audit("RESOLVE_DUE", "INFO", "Universal shadow alpha V2 due cases resolved", result)
        return result

    def registry_rows(self) -> List[Dict[str, Any]]:
        return self.db.query("""
            SELECT
                c.id AS case_id,
                c.created_at,
                c.symbol,
                c.side,
                c.setup,
                c.profile,
                c.horizon_min,
                c.context_bucket,
                c.context_score,
                c.payload AS case_payload,
                r.outcome,
                r.result_r,
                r.mfe_r,
                r.mae_r,
                r.bars_seen,
                r.exit_price
            FROM universal_shadow_results_v2 r
            JOIN universal_shadow_cases_v2 c ON c.id = r.case_id
            WHERE r.result_r IS NOT NULL
            ORDER BY c.created_at ASC;
        """)

    def cluster_key(self, r: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        symbol = txt(r["symbol"]).upper()
        side = txt(r["side"]).upper()
        setup = txt(r["setup"]).upper()
        profile = txt(r["profile"]).upper()
        horizon = inum(r["horizon_min"], 0)
        bucket = txt(r["context_bucket"]).upper()

        key = f"{symbol}|{side}|{setup}|{profile}|{horizon}|{bucket}"

        return key, {
            "symbol": symbol,
            "side": side,
            "setup": setup,
            "profile": profile,
            "horizon_min": horizon,
            "context_bucket": bucket,
        }

    def score_cluster(self, key: str, meta: Dict[str, Any], rows: List[Dict[str, Any]]) -> Dict[str, Any]:
        vals = [fnum(r.get("result_r"), 0.0) for r in rows]
        mfes = [fnum(r.get("mfe_r"), 0.0) for r in rows]
        maes = [fnum(r.get("mae_r"), 0.0) for r in rows]

        n = len(vals)
        wins = [x for x in vals if x > 0]
        losses = [x for x in vals if x < 0]

        expectancy = sum(vals) / n if n else 0.0
        winrate = len(wins) / n if n else 0.0
        gross_win = sum(wins)
        gross_loss = abs(sum(losses))
        pf = 99.0 if gross_loss <= 0 and gross_win > 0 else safe_div(gross_win, gross_loss, 0.0)

        mid = max(1, n // 2)
        train = vals[:mid]
        valid = vals[mid:]
        train_exp = sum(train) / len(train) if train else 0.0
        validation_exp = sum(valid) / len(valid) if valid else 0.0

        if train and valid:
            stability = clamp(1.0 - abs(train_exp - validation_exp) / max(0.10, abs(expectancy)), 0.0, 1.0)
        else:
            stability = 0.0

        avg_mfe = sum(mfes) / len(mfes) if mfes else 0.0
        avg_mae = sum(maes) / len(maes) if maes else 0.0

        sample_score = clamp(n / 120.0, 0, 1)
        exp_score = clamp(expectancy / 0.18, 0, 1)
        pf_score = clamp((pf - 1.0) / 2.0, 0, 1)
        win_score = clamp((winrate - 0.45) / 0.30, 0, 1)
        stability_score = stability

        mae_penalty = clamp(abs(min(0.0, avg_mae)) / 3.0, 0.0, 0.20)

        quality = clamp(100.0 * (
            0.24 * sample_score
            + 0.30 * exp_score
            + 0.22 * pf_score
            + 0.10 * win_score
            + 0.14 * stability_score
            - mae_penalty
        ), 0, 100)

        reasons: List[str] = []

        if n >= 30:
            reasons.append("MIN_RESEARCH_SAMPLE_OK")
        if expectancy > 0:
            reasons.append("EXPECTANCY_POSITIVE")
        if pf >= 1.15:
            reasons.append("PF_ABOVE_MIN")
        if validation_exp > 0:
            reasons.append("VALIDATION_POSITIVE")
        if stability >= 0.35:
            reasons.append("STABILITY_ACCEPTABLE")
        if avg_mae < -1.5:
            reasons.append("MAE_RISK_ELEVATED")

        if n >= 90 and expectancy >= 0.08 and pf >= 1.25 and validation_exp > 0 and stability >= 0.35 and quality >= 55:
            state = "VALIDATED_SHADOW_ALPHA"
            recommendation = "ELIGIBLE_FOR_FUTURE_PROMOTION_POLICY_V2"
            reasons.append("VALIDATED_SHADOW_ALPHA_THRESHOLDS")
        elif n >= 30 and expectancy >= 0.04 and pf >= 1.10:
            state = "RESEARCH_SHADOW_ALPHA"
            recommendation = "CONTINUE_ACCUMULATING_SAMPLE"
            reasons.append("RESEARCH_SHADOW_ALPHA_THRESHOLDS")
        elif expectancy <= 0:
            state = "REJECTED_NEGATIVE_ALPHA"
            recommendation = "DO_NOT_PROMOTE"
            reasons.append("NEGATIVE_EXPECTANCY")
        else:
            state = "WATCHLIST"
            recommendation = "INSUFFICIENT_SAMPLE_OR_STABILITY"
            reasons.append("WATCHLIST_ONLY")

        return {
            "alpha_key": key,
            "symbol": meta["symbol"],
            "side": meta["side"],
            "setup": meta["setup"],
            "profile": meta["profile"],
            "horizon_min": meta["horizon_min"],
            "context_bucket": meta["context_bucket"],
            "n": n,
            "expectancy_r": round(expectancy, 8),
            "winrate": round(winrate, 8),
            "profit_factor": round(pf, 8),
            "avg_mfe_r": round(avg_mfe, 8),
            "avg_mae_r": round(avg_mae, 8),
            "train_exp_r": round(train_exp, 8),
            "validation_exp_r": round(validation_exp, 8),
            "stability_score": round(stability, 8),
            "quality_score": round(quality, 4),
            "state": state,
            "recommendation": recommendation,
            "reasons": reasons,
            "payload": {
                "version": VERSION,
                "method": "isolated_universal_shadow_alpha_registry",
                "no_execution": True,
                "case_count": n,
            },
        }

    def insert_registry(self, r: Dict[str, Any]) -> None:
        self.db.execute("""
            INSERT INTO universal_shadow_registry_v2 (
                ts, version, alpha_key,
                symbol, side, setup, profile, horizon_min, context_bucket,
                n, expectancy_r, winrate, profit_factor,
                avg_mfe_r, avg_mae_r,
                train_exp_r, validation_exp_r, stability_score, quality_score,
                state, recommendation, reasons, payload
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """, (
            utc_now_iso(),
            VERSION,
            r["alpha_key"],
            r["symbol"],
            r["side"],
            r["setup"],
            r["profile"],
            r["horizon_min"],
            r["context_bucket"],
            r["n"],
            r["expectancy_r"],
            r["winrate"],
            r["profit_factor"],
            r["avg_mfe_r"],
            r["avg_mae_r"],
            r["train_exp_r"],
            r["validation_exp_r"],
            r["stability_score"],
            r["quality_score"],
            r["state"],
            r["recommendation"],
            json.dumps(r["reasons"], separators=(",", ":"), ensure_ascii=False),
            json.dumps(r["payload"], separators=(",", ":"), ensure_ascii=False),
        ))

    def refresh_registry(self) -> Dict[str, Any]:
        self.ensure_schema()
        rows = self.registry_rows()

        groups: Dict[str, Dict[str, Any]] = {}

        for r in rows:
            key, meta = self.cluster_key(r)
            g = groups.setdefault(key, {"meta": meta, "rows": []})
            g["rows"].append(r)

        scored = []
        for key, g in groups.items():
            scored.append(self.score_cluster(key, g["meta"], g["rows"]))

        scored.sort(key=lambda x: (x["quality_score"], x["expectancy_r"], x["n"]), reverse=True)

        for r in scored[:120]:
            self.insert_registry(r)

        result = {
            "version": VERSION,
            "resolved_rows": len(rows),
            "clusters_scored": len(scored),
            "registry_inserted": min(len(scored), 120),
        }

        self.audit("REFRESH_REGISTRY", "INFO", "Universal shadow alpha V2 registry refreshed", result)
        return result

    def retention(self) -> Dict[str, Any]:
        self.db.execute("""
            DELETE FROM universal_shadow_cases_v2
            WHERE id NOT IN (
                SELECT id FROM universal_shadow_cases_v2 ORDER BY created_at DESC LIMIT ?
            );
        """, (MAX_CASE_ROWS,))

        self.db.execute("""
            DELETE FROM universal_shadow_results_v2
            WHERE id NOT IN (
                SELECT id FROM universal_shadow_results_v2 ORDER BY id DESC LIMIT ?
            );
        """, (MAX_RESULT_ROWS,))

        self.db.execute("""
            DELETE FROM universal_shadow_registry_v2
            WHERE id NOT IN (
                SELECT id FROM universal_shadow_registry_v2 ORDER BY id DESC LIMIT ?
            );
        """, (MAX_REGISTRY_ROWS,))

        self.db.execute("""
            DELETE FROM universal_shadow_alpha_audit_v2
            WHERE id NOT IN (
                SELECT id FROM universal_shadow_alpha_audit_v2 ORDER BY id DESC LIMIT ?
            );
        """, (MAX_AUDIT_ROWS,))

        return {
            "max_case_rows": MAX_CASE_ROWS,
            "max_result_rows": MAX_RESULT_ROWS,
            "max_registry_rows": MAX_REGISTRY_ROWS,
            "max_audit_rows": MAX_AUDIT_ROWS,
        }

    def cycle(self, contexts: Optional[Dict[str, Dict[str, Any]]] = None) -> Dict[str, Any]:
        self.ensure_schema()
        register = self.register_from_contexts(contexts)
        resolve = self.resolve_due()
        registry = self.refresh_registry()
        retention = self.retention()

        result = {
            "version": VERSION,
            "register": register,
            "resolve": resolve,
            "registry": registry,
            "retention": retention,
            "no_execution": True,
            "isolated_from_forward_tables": True,
        }

        self.audit("CYCLE", "INFO", "Universal shadow alpha V2 cycle completed", result)
        return result

    def latest_registry(self, limit: int = 30) -> List[Dict[str, Any]]:
        self.ensure_schema()
        return self.db.query("""
            SELECT
                symbol,
                side,
                setup,
                profile,
                horizon_min,
                context_bucket,
                n,
                ROUND(expectancy_r,4) AS expectancy_r,
                ROUND(winrate,3) AS winrate,
                ROUND(profit_factor,3) AS pf,
                ROUND(validation_exp_r,4) AS validation_r,
                ROUND(stability_score,3) AS stability,
                ROUND(quality_score,2) AS quality,
                state,
                recommendation
            FROM latest_universal_shadow_registry_v2
            ORDER BY quality_score DESC, expectancy_r DESC, n DESC
            LIMIT ?;
        """, (limit,))

    def pending_summary(self) -> List[Dict[str, Any]]:
        self.ensure_schema()
        return self.db.query("""
            SELECT
                symbol,
                side,
                setup,
                profile,
                horizon_min,
                status,
                COUNT(*) AS n,
                MIN(created_at) AS first_created,
                MAX(created_at) AS last_created
            FROM universal_shadow_cases_v2
            GROUP BY symbol, side, setup, profile, horizon_min, status
            ORDER BY last_created DESC
            LIMIT 50;
        """)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cycle", action="store_true")
    parser.add_argument("--register", action="store_true")
    parser.add_argument("--resolve", action="store_true")
    parser.add_argument("--registry", action="store_true")
    parser.add_argument("--latest", action="store_true")
    parser.add_argument("--pending", action="store_true")
    args = parser.parse_args()

    engine = UniversalShadowAlphaLoopV2()

    if args.cycle:
        print(json.dumps(engine.cycle(), indent=2, sort_keys=True))

    if args.register:
        print(json.dumps(engine.register_from_contexts(), indent=2, sort_keys=True))

    if args.resolve:
        print(json.dumps(engine.resolve_due(), indent=2, sort_keys=True))

    if args.registry:
        print(json.dumps(engine.refresh_registry(), indent=2, sort_keys=True))

    if args.pending:
        for r in engine.pending_summary():
            print(r)

    if args.latest:
        for r in engine.latest_registry():
            print(r)


if __name__ == "__main__":
    main()
