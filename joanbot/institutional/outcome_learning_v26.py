
from __future__ import annotations

import json, os, math, datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

VERSION = "V26_INSTITUTIONAL_LEARNING_CORE"

def utc() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()

def fnum(x, default=0.0) -> float:
    try:
        if x is None:
            return default
        return float(x)
    except Exception:
        return default

def jloads(x, default=None):
    if default is None:
        default = {}
    try:
        if isinstance(x, dict):
            return x
        return json.loads(x or "{}")
    except Exception:
        return default

def getenv_float(name, default):
    try:
        return float(os.getenv(name, default))
    except Exception:
        return float(default)

def getenv_int(name, default):
    try:
        return int(float(os.getenv(name, default)))
    except Exception:
        return int(default)

def getenv_bool(name, default):
    v = os.getenv(name)
    if v is None:
        return bool(default)
    return str(v).lower() in ("1", "true", "yes", "on")

class V26InstitutionalLearningCore:
    def __init__(self, db):
        self.db = db
        self.ensure_schema()

    def ensure_schema(self):
        self.db.execute("""
        CREATE TABLE IF NOT EXISTS outcome_ledger_v26(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            source TEXT NOT NULL,
            trade_id INTEGER,
            position_id TEXT,
            decision_id INTEGER,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,
            setup TEXT NOT NULL,
            entry_ts TEXT,
            exit_ts TEXT,
            entry_price REAL,
            exit_price REAL,
            size_usd REAL,
            fees REAL,
            pnl_usd REAL,
            risk_usd REAL,
            result_r REAL,
            mfe_r REAL,
            mae_r REAL,
            context_key TEXT,
            regime TEXT,
            session TEXT,
            volatility_bucket TEXT,
            news_bucket TEXT,
            reason TEXT,
            quality_state TEXT,
            promotion_state TEXT,
            payload TEXT NOT NULL,
            UNIQUE(source, trade_id)
        )
        """)

        self.db.execute("""
        CREATE TABLE IF NOT EXISTS promotion_state_v26(
            key TEXT PRIMARY KEY,
            updated_at TEXT NOT NULL,
            symbol TEXT,
            side TEXT,
            setup TEXT,
            regime TEXT,
            live_n REAL NOT NULL,
            live_exp_r REAL NOT NULL,
            live_pf REAL NOT NULL,
            live_dd_r REAL NOT NULL,
            forward_n REAL NOT NULL,
            forward_exp_r REAL NOT NULL,
            forward_pf REAL NOT NULL,
            combined_score REAL NOT NULL,
            state TEXT NOT NULL,
            recommended_size_usd REAL NOT NULL,
            hard_vetoes TEXT NOT NULL,
            payload TEXT NOT NULL
        )
        """)

        self.db.execute("""
        CREATE TABLE IF NOT EXISTS v26_learning_audit(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            event TEXT NOT NULL,
            symbol TEXT,
            side TEXT,
            setup TEXT,
            payload TEXT NOT NULL
        )
        """)

    def audit(self, event: str, payload: Dict[str, Any]):
        try:
            self.db.execute("""
                INSERT INTO v26_learning_audit(ts,event,symbol,side,setup,payload)
                VALUES(?,?,?,?,?,?)
            """, (
                utc(), event,
                payload.get("symbol"),
                payload.get("side"),
                payload.get("setup"),
                json.dumps(payload, sort_keys=True, default=str)
            ))
        except Exception:
            pass

    def latest_feature_context(self, symbol: str, ts: str | None = None) -> Dict[str, Any]:
        try:
            rows = self.db.query("""
                SELECT *
                FROM features
                WHERE symbol=? AND ts<=?
                ORDER BY ts DESC
                LIMIT 1
            """, (symbol, ts or utc()))
            if rows:
                r = rows[0]
                return {
                    "regime": r.get("regime") or "UNKNOWN",
                    "session": r.get("session") or "UNKNOWN",
                    "volatility_bucket": r.get("volatility_bucket") or "UNKNOWN",
                    "news_bucket": r.get("news_bucket") or "UNKNOWN",
                    "feature_payload": jloads(r.get("payload")),
                }
        except Exception:
            pass

        return {
            "regime": "UNKNOWN",
            "session": "UNKNOWN",
            "volatility_bucket": "UNKNOWN",
            "news_bucket": "UNKNOWN",
            "feature_payload": {},
        }

    def context_from_decision(self, decision: Dict[str, Any], symbol: str, ts: str | None = None) -> Dict[str, Any]:
        fs = decision.get("feature_summary") or {}
        if isinstance(fs, dict):
            regime = fs.get("regime") or fs.get("market_regime")
            session = fs.get("session")
            vol = fs.get("volatility_bucket") or fs.get("vol_bucket")
            news = fs.get("news_bucket")
            if any([regime, session, vol, news]):
                return {
                    "regime": regime or "UNKNOWN",
                    "session": session or "UNKNOWN",
                    "volatility_bucket": vol or "UNKNOWN",
                    "news_bucket": news or "UNKNOWN",
                    "feature_payload": fs,
                }
        return self.latest_feature_context(symbol, ts)

    def position_decision(self, pos: Dict[str, Any]) -> Dict[str, Any]:
        meta = pos.get("meta") or {}
        decision = meta.get("decision") or {}
        if isinstance(decision, dict):
            return decision
        return {}

    def keys_for(self, symbol: str, side: str, setup: str, ctx: Dict[str, Any]) -> List[str]:
        regime = ctx.get("regime") or "UNKNOWN"
        session = ctx.get("session") or "UNKNOWN"
        vol = ctx.get("volatility_bucket") or "UNKNOWN"
        news = ctx.get("news_bucket") or "UNKNOWN"
        return [
            f"SETUP|{symbol}|{side}|{setup}|{regime}|{session}|{vol}|{news}",
            f"SETUP|{symbol}|{side}|{setup}|{regime}|{session}",
            f"SETUP|{symbol}|{side}|{setup}|{regime}",
            f"SYM_SIDE_REGIME|{symbol}|{side}|{regime}",
            f"SYM_SIDE|{symbol}|{side}",
            f"SIDE_REGIME|{side}|{regime}",
            f"SIDE|{side}",
            "GLOBAL",
        ]

    def compute_trade_r(self, pos: Dict[str, Any], trade: Dict[str, Any]) -> Tuple[float, float]:
        pnl = fnum(trade.get("pnl_usd"))
        size = abs(fnum(trade.get("size_usd")))
        entry = fnum(trade.get("entry") or pos.get("entry_price") or pos.get("entry"))
        decision = self.position_decision(pos)

        stop = (
            fnum(pos.get("initial_stop_loss"))
            or fnum((decision or {}).get("stop_loss"))
            or fnum(pos.get("stop_loss"))
        )

        risk_usd = 0.0
        if entry > 0 and stop > 0 and size > 0:
            risk_usd = abs(entry - stop) / entry * size

        if risk_usd <= 0:
            risk_usd = fnum(((decision.get("risk") or {}).get("risk_usd"))) * fnum(trade.get("close_pct"), 1.0)

        if risk_usd <= 0:
            return 0.0, 0.0

        return pnl / risk_usd, risk_usd

    def update_edge(self, key: str, source: str, result_r: float, payload: Dict[str, Any]):
        source = source.upper()
        rows = self.db.query("SELECT * FROM edge_memory WHERE key=? AND source=?", (key, source))

        if rows:
            r = rows[0]
            n = fnum(r.get("n")) + 1
            wins = fnum(r.get("wins")) + (1 if result_r > 0 else 0)
            losses = fnum(r.get("losses")) + (1 if result_r < 0 else 0)
            sum_r = fnum(r.get("sum_r")) + result_r
            pos = fnum(r.get("sum_pos_r")) + max(0.0, result_r)
            neg = fnum(r.get("sum_neg_r")) + min(0.0, result_r)
            dd = min(fnum(r.get("max_dd_r")), result_r)
            self.db.execute("""
                UPDATE edge_memory
                SET updated_at=?, n=?, wins=?, losses=?, sum_r=?, sum_pos_r=?, sum_neg_r=?, max_dd_r=?, payload=?
                WHERE key=? AND source=?
            """, (
                utc(), n, wins, losses, sum_r, pos, neg, dd,
                json.dumps(payload, sort_keys=True, default=str),
                key, source
            ))
        else:
            self.db.execute("""
                INSERT OR REPLACE INTO edge_memory(
                    key,source,updated_at,n,wins,losses,sum_r,sum_pos_r,sum_neg_r,max_dd_r,payload
                )
                VALUES(?,?,?,?,?,?,?,?,?,?,?)
            """, (
                key, source, utc(), 1,
                1 if result_r > 0 else 0,
                1 if result_r < 0 else 0,
                result_r,
                max(0.0, result_r),
                min(0.0, result_r),
                min(0.0, result_r),
                json.dumps(payload, sort_keys=True, default=str)
            ))

    def aggregate_source(self, key: str, source: str) -> Dict[str, float]:
        rows = self.db.query("SELECT * FROM edge_memory WHERE key=? AND source=?", (key, source))
        if not rows:
            return {"n": 0.0, "exp": 0.0, "pf": 0.0, "dd": 0.0, "wins": 0.0, "losses": 0.0}
        r = rows[0]
        n = fnum(r.get("n"))
        pos = fnum(r.get("sum_pos_r"))
        neg = fnum(r.get("sum_neg_r"))
        return {
            "n": n,
            "exp": fnum(r.get("sum_r")) / max(1.0, n),
            "pf": pos / abs(neg) if neg < 0 else (999.0 if pos > 0 else 0.0),
            "dd": fnum(r.get("max_dd_r")),
            "wins": fnum(r.get("wins")),
            "losses": fnum(r.get("losses")),
        }

    def state_from_stats(self, live: Dict[str, float], forward: Dict[str, float]) -> Tuple[str, float, List[str]]:
        hard = []

        ln = live["n"]
        fn = forward["n"]
        lexp = live["exp"]
        fexp = forward["exp"]
        lpf = live["pf"]
        fpf = forward["pf"]

        score = 0.0
        score += min(35.0, ln * 2.0)
        score += min(20.0, fn / 250.0)
        score += max(-30.0, min(30.0, lexp * 120.0))
        score += max(-18.0, min(18.0, fexp * 80.0))
        score += max(-10.0, min(15.0, (lpf - 1.0) * 10.0 if lpf else -8.0))
        score += max(-8.0, min(10.0, (fpf - 1.0) * 6.0 if fpf else -6.0))

        if ln >= 6 and lexp < -0.10 and lpf < 0.85:
            hard.append("LIVE_NEGATIVE_EDGE")
        if ln >= 10 and live["dd"] <= -1.5:
            hard.append("LIVE_TAIL_RISK")

        if hard:
            return "QUARANTINE", score, hard

        if ln >= 30 and lexp > 0.08 and lpf > 1.25:
            return "VALIDATED", score, hard
        if ln >= 12 and lexp > 0.04 and lpf > 1.10:
            return "CANARY", score, hard
        if fn >= 800 and fexp > 0.02 and fpf > 1.05:
            return "EXPLORE", score, hard
        return "RESEARCH", score, hard

    def recommended_size(self, state: str) -> float:
        if state == "VALIDATED":
            return getenv_float("V26_VALIDATED_SIZE_USD", 25000)
        if state == "CANARY":
            return getenv_float("V26_CANARY_SIZE_USD", 12000)
        if state == "EXPLORE":
            return getenv_float("V26_EXPLORE_SIZE_USD", 6000)
        if state == "RESEARCH":
            return getenv_float("V26_RESEARCH_SIZE_USD", 3000)
        return 0.0

    def refresh_promotion_state(self, key: str, symbol: str, side: str, setup: str, ctx: Dict[str, Any]):
        live = self.aggregate_source(key, "LIVE")
        forward = self.aggregate_source(key, "FORWARD")
        state, score, hard = self.state_from_stats(live, forward)
        size = self.recommended_size(state)

        payload = {
            "version": VERSION,
            "key": key,
            "live": live,
            "forward": forward,
            "state": state,
            "score": score,
            "hard_vetoes": hard,
            "context": ctx,
        }

        self.db.execute("""
            INSERT OR REPLACE INTO promotion_state_v26(
                key,updated_at,symbol,side,setup,regime,
                live_n,live_exp_r,live_pf,live_dd_r,
                forward_n,forward_exp_r,forward_pf,
                combined_score,state,recommended_size_usd,hard_vetoes,payload
            )
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            key, utc(), symbol, side, setup, ctx.get("regime"),
            live["n"], live["exp"], live["pf"], live["dd"],
            forward["n"], forward["exp"], forward["pf"],
            score, state, size, json.dumps(hard),
            json.dumps(payload, sort_keys=True, default=str)
        ))

        return state, size, hard

    def record_live_close(self, pos: Dict[str, Any], trade: Dict[str, Any]) -> Dict[str, Any]:
        symbol = str(trade.get("symbol") or pos.get("symbol") or "").upper()
        side = str(trade.get("side") or pos.get("side") or "").upper()
        setup = str(trade.get("setup") or pos.get("setup") or "UNKNOWN").upper()
        decision = self.position_decision(pos)
        ctx = self.context_from_decision(decision, symbol, pos.get("opened_at"))

        result_r, risk_usd = self.compute_trade_r(pos, trade)
        trade_id = trade.get("id")

        payload = {
            "version": VERSION,
            "source": "LIVE",
            "trade": trade,
            "position": pos,
            "decision": decision,
            "context": ctx,
            "result_r": result_r,
            "risk_usd": risk_usd,
        }

        keys = self.keys_for(symbol, side, setup, ctx)
        for k in keys:
            self.update_edge(k, "LIVE", result_r, payload)
            self.refresh_promotion_state(k, symbol, side, setup, ctx)

        main_key = keys[0]
        state_rows = self.db.query("SELECT state FROM promotion_state_v26 WHERE key=?", (main_key,))
        state = state_rows[0]["state"] if state_rows else "UNKNOWN"

        try:
            self.db.execute("UPDATE trades SET pnl_r=? WHERE id=?", (result_r, trade_id))
        except Exception:
            pass

        self.db.execute("""
            INSERT OR IGNORE INTO outcome_ledger_v26(
                ts,source,trade_id,position_id,decision_id,symbol,side,setup,
                entry_ts,exit_ts,entry_price,exit_price,size_usd,fees,pnl_usd,risk_usd,result_r,
                mfe_r,mae_r,context_key,regime,session,volatility_bucket,news_bucket,
                reason,quality_state,promotion_state,payload
            )
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            utc(), "LIVE", trade_id, trade.get("position_id"),
            decision.get("id") or decision.get("decision_id"),
            symbol, side, setup,
            pos.get("opened_at"), trade.get("ts"),
            fnum(trade.get("entry")), fnum(trade.get("exit")),
            fnum(trade.get("size_usd")), fnum(trade.get("fees")),
            fnum(trade.get("pnl_usd")), risk_usd, result_r,
            fnum(pos.get("mfe_r")), fnum(pos.get("mae_r")),
            main_key, ctx.get("regime"), ctx.get("session"), ctx.get("volatility_bucket"), ctx.get("news_bucket"),
            trade.get("reason"), "OK" if risk_usd > 0 else "RISK_UNAVAILABLE", state,
            json.dumps(payload, sort_keys=True, default=str)
        ))

        self.audit("LIVE_OUTCOME_RECORDED", {
            "symbol": symbol, "side": side, "setup": setup,
            "trade_id": trade_id, "result_r": result_r, "risk_usd": risk_usd,
            "promotion_state": state,
        })

        return {"result_r": result_r, "risk_usd": risk_usd, "promotion_state": state}

    def contextual_forward_keys(self, result_payload: Dict[str, Any]) -> Tuple[List[str], Dict[str, Any]]:
        symbol = str(result_payload.get("symbol") or "").upper()
        side = str(result_payload.get("side") or "").upper()
        setup = str(result_payload.get("setup") or "UNKNOWN").upper()
        case_id = result_payload.get("case_id")

        decision = {}
        created_at = result_payload.get("resolved_at")

        try:
            rows = self.db.query("SELECT * FROM forward_cases WHERE id=? LIMIT 1", (case_id,))
            if rows:
                fc = rows[0]
                p = jloads(fc.get("payload"))
                decision = p.get("decision") or {}
                created_at = fc.get("created_at") or created_at
        except Exception:
            pass

        ctx = self.context_from_decision(decision, symbol, created_at)
        result_payload = dict(result_payload)
        result_payload["v26_context"] = ctx
        result_payload["v26_version"] = VERSION
        return self.keys_for(symbol, side, setup, ctx), result_payload

    def paper_loss_used(self) -> float:
        try:
            rows = self.db.query("SELECT SUM(pnl_usd) x FROM trades")
            pnl = fnum(rows[0].get("x") if rows else 0)
            return abs(min(0.0, pnl))
        except Exception:
            return 0.0

    def budget_available(self) -> bool:
        budget = getenv_float("V26_TRAINING_LOSS_BUDGET_USD", 50000)
        return self.paper_loss_used() < budget

    def decision_stage_and_size(self, d) -> Tuple[str, float, List[str]]:
        dd = d.to_dict() if hasattr(d, "to_dict") else dict(getattr(d, "__dict__", {}) or {})
        symbol = str(dd.get("symbol") or "").upper()
        side = str(dd.get("side") or "").upper()
        setup = str(dd.get("setup") or "UNKNOWN").upper()
        ctx = self.context_from_decision(dd, symbol, dd.get("ts"))

        keys = self.keys_for(symbol, side, setup, ctx)
        chosen = None

        for k in keys:
            rows = self.db.query("SELECT * FROM promotion_state_v26 WHERE key=?", (k,))
            if rows:
                chosen = rows[0]
                break

        if not chosen:
            for k in keys:
                state, size, hard = self.refresh_promotion_state(k, symbol, side, setup, ctx)
                chosen = {"state": state, "recommended_size_usd": size, "hard_vetoes": json.dumps(hard)}
                break

        state = chosen.get("state", "RESEARCH")
        size = fnum(chosen.get("recommended_size_usd"), self.recommended_size(state))
        hard = jloads(chosen.get("hard_vetoes"), [])

        return state, size, hard

    def apply_training_policy_to_decision(self, d, wallet: Dict[str, Any]):
        if not getenv_bool("V26_PAPER_TRAINING_ENABLED", True):
            return d

        if not self.budget_available():
            try:
                d.reasons.append("V26_TRAINING_BUDGET_EXHAUSTED")
            except Exception:
                pass
            return d

        action = str(getattr(d, "action", "")).upper()
        score = fnum(getattr(d, "final_score", 0))
        state, target_size, hard = self.decision_stage_and_size(d)

        if hard and state == "QUARANTINE":
            try:
                d.reasons.append("V26_QUARANTINE_NO_TRAINING_SIZE")
            except Exception:
                pass
            return d

        promote_probe_min = getenv_float("V26_PROBE_PROMOTE_MIN_SCORE", 48)

        if action == "PROBE" and score >= promote_probe_min:
            d.action = "OPEN"
            try:
                d.reasons.append("V26_PROBE_PROMOTED_TO_PAPER_OPEN")
            except Exception:
                pass

        if str(getattr(d, "action", "")).upper() == "OPEN":
            max_size = getenv_float("V26_MAX_POSITION_SIZE_USD", 50000)
            target_size = min(max_size, max(target_size, getenv_float("V26_RESEARCH_SIZE_USD", 3000)))

            original_size = fnum(getattr(d, "size_usd", 0))
            d.size_usd = max(original_size, target_size)

            try:
                d.risk["v26_stage"] = state
                d.risk["v26_target_size_usd"] = d.size_usd
                d.risk["v26_policy"] = VERSION
            except Exception:
                pass

            try:
                d.reasons.append(f"V26_STAGE_{state}_SIZE_{d.size_usd:.0f}")
            except Exception:
                pass

        return d

    def report(self) -> str:
        lines = []
        lines.append("===== V26 INSTITUTIONAL LEARNING REPORT =====")
        lines.append(f"UTC: {utc()}")
        try:
            counts = {}
            for t in ["outcome_ledger_v26", "promotion_state_v26", "edge_memory", "trades", "positions", "forward_results"]:
                try:
                    counts[t] = self.db.query(f"SELECT COUNT(*) c FROM {t}")[0]["c"]
                except Exception:
                    counts[t] = None
            lines.append("COUNTS: " + json.dumps(counts, sort_keys=True))
        except Exception as e:
            lines.append(f"COUNTS_ERROR: {e!r}")

        rows = []
        try:
            rows = self.db.query("""
                SELECT state, COUNT(*) n,
                       ROUND(AVG(live_exp_r),5) avg_live_exp,
                       ROUND(AVG(forward_exp_r),5) avg_forward_exp,
                       ROUND(AVG(recommended_size_usd),2) avg_size
                FROM promotion_state_v26
                GROUP BY state
                ORDER BY n DESC
            """)
        except Exception:
            rows = []

        lines.append("")
        lines.append("PROMOTION STATES:")
        for r in rows:
            lines.append(f"{r.get('state')} n={r.get('n')} liveExp={r.get('avg_live_exp')} forwardExp={r.get('avg_forward_exp')} avgSize={r.get('avg_size')}")

        lines.append("")
        lines.append("TOP VALID/EXPLORE:")
        try:
            rows = self.db.query("""
                SELECT key,state,live_n,ROUND(live_exp_r,4) live_exp,ROUND(live_pf,3) live_pf,
                       forward_n,ROUND(forward_exp_r,4) forward_exp,ROUND(forward_pf,3) forward_pf,
                       ROUND(recommended_size_usd,2) size
                FROM promotion_state_v26
                WHERE state!='QUARANTINE'
                ORDER BY state='VALIDATED' DESC, state='CANARY' DESC, state='EXPLORE' DESC,
                         combined_score DESC
                LIMIT 20
            """)
            for r in rows:
                lines.append(f"{r.get('state')} size={r.get('size')} liveN={r.get('live_n')} liveExp={r.get('live_exp')} livePF={r.get('live_pf')} fwdN={r.get('forward_n')} fwdExp={r.get('forward_exp')} fwdPF={r.get('forward_pf')} key={r.get('key')}")
        except Exception as e:
            lines.append(f"TOP_ERROR: {e!r}")

        lines.append("")
        lines.append("QUARANTINE:")
        try:
            rows = self.db.query("""
                SELECT key,state,live_n,ROUND(live_exp_r,4) live_exp,ROUND(live_pf,3) live_pf,hard_vetoes
                FROM promotion_state_v26
                WHERE state='QUARANTINE'
                ORDER BY live_exp_r ASC
                LIMIT 20
            """)
            for r in rows:
                lines.append(f"QUARANTINE liveN={r.get('live_n')} liveExp={r.get('live_exp')} livePF={r.get('live_pf')} hard={r.get('hard_vetoes')} key={r.get('key')}")
        except Exception as e:
            lines.append(f"Q_ERROR: {e!r}")

        return "\n".join(lines)

def get_core(db):
    return V26InstitutionalLearningCore(db)
