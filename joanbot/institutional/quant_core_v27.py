
from __future__ import annotations

import json, os, math, datetime
from typing import Any, Dict, List, Tuple

VERSION = "V27_INSTITUTIONAL_QUANT_CORE"

def utc():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()

def f(x, default=0.0):
    try:
        if x is None:
            return default
        return float(x)
    except Exception:
        return default

def j(x, default=None):
    if default is None:
        default = {}
    try:
        if isinstance(x, dict):
            return x
        return json.loads(x or "{}")
    except Exception:
        return default

def envf(k, d):
    try:
        return float(os.getenv(k, d))
    except Exception:
        return float(d)

def envi(k, d):
    try:
        return int(float(os.getenv(k, d)))
    except Exception:
        return int(d)

def envb(k, d=True):
    v = os.getenv(k)
    if v is None:
        return bool(d)
    return str(v).lower() in ("1","true","yes","on")

class V27QuantCore:
    def __init__(self, db):
        self.db = db
        self.ensure_schema()

    def ensure_schema(self):
        self.db.execute("""
        CREATE TABLE IF NOT EXISTS outcome_ledger_v27(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            source TEXT NOT NULL,
            source_id TEXT NOT NULL,
            position_id TEXT,
            decision_id INTEGER,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,
            setup TEXT NOT NULL,
            action TEXT,
            entry_ts TEXT,
            exit_ts TEXT,
            entry_price REAL,
            exit_price REAL,
            size_usd REAL,
            pnl_usd REAL,
            fees REAL,
            risk_usd REAL,
            result_r REAL,
            mfe_r REAL,
            mae_r REAL,
            regime TEXT,
            session TEXT,
            volatility_bucket TEXT,
            news_bucket TEXT,
            context_key TEXT,
            quality_state TEXT,
            promotion_state TEXT,
            reason TEXT,
            payload TEXT NOT NULL,
            UNIQUE(source, source_id)
        )
        """)

        self.db.execute("""
        CREATE TABLE IF NOT EXISTS promotion_state_v27(
            key TEXT PRIMARY KEY,
            updated_at TEXT NOT NULL,
            symbol TEXT,
            side TEXT,
            setup TEXT,
            regime TEXT,
            session TEXT,
            volatility_bucket TEXT,
            live_n REAL NOT NULL,
            live_exp_r REAL NOT NULL,
            live_pf REAL NOT NULL,
            live_winrate REAL NOT NULL,
            live_dd_r REAL NOT NULL,
            forward_n REAL NOT NULL,
            forward_exp_r REAL NOT NULL,
            forward_pf REAL NOT NULL,
            forward_winrate REAL NOT NULL,
            combined_score REAL NOT NULL,
            state TEXT NOT NULL,
            recommended_size_usd REAL NOT NULL,
            hard_vetoes TEXT NOT NULL,
            reasons TEXT NOT NULL,
            payload TEXT NOT NULL
        )
        """)

        self.db.execute("""
        CREATE TABLE IF NOT EXISTS v27_quant_audit(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            event TEXT NOT NULL,
            symbol TEXT,
            side TEXT,
            setup TEXT,
            payload TEXT NOT NULL
        )
        """)

    def audit(self, event, payload):
        try:
            self.db.execute("""
            INSERT INTO v27_quant_audit(ts,event,symbol,side,setup,payload)
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

    def q(self, sql, params=()):
        try:
            return [dict(r) for r in self.db.query(sql, params)]
        except Exception:
            return []

    def decision_from_position(self, pos):
        meta = pos.get("meta") or {}
        d = meta.get("decision") or {}
        return d if isinstance(d, dict) else {}

    def context_from_decision(self, d, symbol):
        fs = d.get("feature_summary") or {}
        if isinstance(fs, dict):
            return {
                "regime": fs.get("regime") or "UNKNOWN",
                "session": fs.get("session") or "UNKNOWN",
                "volatility_bucket": fs.get("volatility_bucket") or fs.get("vol_bucket") or "UNKNOWN",
                "news_bucket": fs.get("news_bucket") or "UNKNOWN",
            }
        return {
            "regime": "UNKNOWN",
            "session": "UNKNOWN",
            "volatility_bucket": "UNKNOWN",
            "news_bucket": "UNKNOWN",
        }

    def context_from_forward_case(self, case_id, symbol):
        rows = self.q("SELECT payload FROM forward_cases WHERE id=? LIMIT 1", (case_id,))
        if rows:
            p = j(rows[0].get("payload"))
            d = p.get("decision") or p
            return self.context_from_decision(d, symbol), d
        return {
            "regime": "UNKNOWN",
            "session": "UNKNOWN",
            "volatility_bucket": "UNKNOWN",
            "news_bucket": "UNKNOWN",
        }, {}

    def keys_for(self, symbol, side, setup, ctx):
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

    def risk_from_position(self, pos, trade):
        pnl = f(trade.get("pnl_usd"))
        size = abs(f(trade.get("size_usd")))
        entry = f(trade.get("entry") or trade.get("entry_price") or pos.get("entry_price") or pos.get("entry"))
        d = self.decision_from_position(pos)

        stop = (
            f(pos.get("initial_stop_loss"))
            or f(pos.get("stop_loss"))
            or f(d.get("stop_loss"))
        )

        risk_usd = 0.0
        if entry > 0 and stop > 0 and size > 0:
            risk_usd = abs(entry - stop) / entry * size

        if risk_usd <= 0:
            r = d.get("risk") or {}
            risk_usd = f(r.get("risk_usd")) * f(trade.get("close_pct"), 1.0)

        if risk_usd <= 0:
            return 0.0, 0.0

        return pnl / risk_usd, risk_usd

    def update_edge_memory(self, key, source, result_r, payload):
        source = str(source).upper()
        rows = self.q("SELECT * FROM edge_memory WHERE key=? AND source=?", (key, source))

        if rows:
            r = rows[0]
            n = f(r.get("n")) + 1
            wins = f(r.get("wins")) + (1 if result_r > 0 else 0)
            losses = f(r.get("losses")) + (1 if result_r < 0 else 0)
            sum_r = f(r.get("sum_r")) + result_r
            sum_pos = f(r.get("sum_pos_r")) + max(0, result_r)
            sum_neg = f(r.get("sum_neg_r")) + min(0, result_r)
            dd = min(f(r.get("max_dd_r")), result_r)
            self.db.execute("""
            UPDATE edge_memory
            SET updated_at=?, n=?, wins=?, losses=?, sum_r=?, sum_pos_r=?, sum_neg_r=?, max_dd_r=?, payload=?
            WHERE key=? AND source=?
            """, (
                utc(), n, wins, losses, sum_r, sum_pos, sum_neg, dd,
                json.dumps(payload, sort_keys=True, default=str),
                key, source
            ))
        else:
            self.db.execute("""
            INSERT OR REPLACE INTO edge_memory(
                key,source,updated_at,n,wins,losses,sum_r,sum_pos_r,sum_neg_r,max_dd_r,payload
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
            """, (
                key, source, utc(), 1,
                1 if result_r > 0 else 0,
                1 if result_r < 0 else 0,
                result_r,
                max(0, result_r),
                min(0, result_r),
                min(0, result_r),
                json.dumps(payload, sort_keys=True, default=str)
            ))

    def source_stats(self, key, source):
        rows = self.q("SELECT * FROM edge_memory WHERE key=? AND source=?", (key, source))
        if not rows:
            return {"n": 0, "exp": 0, "pf": 0, "wr": 0, "dd": 0}
        r = rows[0]
        n = f(r.get("n"))
        wins = f(r.get("wins"))
        pos = f(r.get("sum_pos_r"))
        neg = f(r.get("sum_neg_r"))
        return {
            "n": n,
            "exp": f(r.get("sum_r")) / max(1, n),
            "pf": pos / abs(neg) if neg < 0 else (999.0 if pos > 0 else 0.0),
            "wr": wins / max(1, n),
            "dd": f(r.get("max_dd_r")),
        }

    def classify(self, live, forward):
        hard = []
        reasons = []

        score = 0
        score += min(30, live["n"] * 2.5)
        score += min(20, forward["n"] / 250)
        score += max(-35, min(35, live["exp"] * 140))
        score += max(-20, min(20, forward["exp"] * 90))
        score += max(-12, min(15, (live["pf"] - 1) * 10 if live["pf"] else -8))
        score += max(-8, min(10, (forward["pf"] - 1) * 6 if forward["pf"] else -5))

        if live["n"] >= 5 and live["exp"] < -0.08 and live["pf"] < 0.9:
            hard.append("LIVE_NEGATIVE_EDGE")
        if live["n"] >= 8 and live["dd"] <= -1.5:
            hard.append("LIVE_TAIL_LOSS")
        if forward["n"] >= 500 and forward["exp"] < -0.03 and forward["pf"] < 0.9:
            hard.append("FORWARD_NEGATIVE_EDGE")

        if hard:
            return "QUARANTINE", score, hard, reasons

        if live["n"] >= 30 and live["exp"] > 0.08 and live["pf"] > 1.25:
            return "VALIDATED", score, hard, reasons
        if live["n"] >= 12 and live["exp"] > 0.04 and live["pf"] > 1.10:
            return "CANARY", score, hard, reasons
        if forward["n"] >= 300 and forward["exp"] > 0.015 and forward["pf"] > 1.03:
            return "EXPLORE", score, hard, reasons

        return "RESEARCH", score, hard, reasons

    def size_for_state(self, state):
        if state == "VALIDATED":
            return envf("V27_VALIDATED_SIZE_USD", 30000)
        if state == "CANARY":
            return envf("V27_CANARY_SIZE_USD", 15000)
        if state == "EXPLORE":
            return envf("V27_EXPLORE_SIZE_USD", 8000)
        if state == "RESEARCH":
            return envf("V27_RESEARCH_SIZE_USD", 4000)
        return 0.0

    def refresh_promotion(self, key, symbol, side, setup, ctx):
        live = self.source_stats(key, "LIVE")
        forward = self.source_stats(key, "FORWARD")
        state, score, hard, reasons = self.classify(live, forward)
        size = self.size_for_state(state)

        payload = {
            "version": VERSION,
            "key": key,
            "context": ctx,
            "live": live,
            "forward": forward,
            "state": state,
            "score": score,
            "hard_vetoes": hard,
            "reasons": reasons,
        }

        self.db.execute("""
        INSERT OR REPLACE INTO promotion_state_v27(
            key,updated_at,symbol,side,setup,regime,session,volatility_bucket,
            live_n,live_exp_r,live_pf,live_winrate,live_dd_r,
            forward_n,forward_exp_r,forward_pf,forward_winrate,
            combined_score,state,recommended_size_usd,hard_vetoes,reasons,payload
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            key, utc(), symbol, side, setup,
            ctx.get("regime"), ctx.get("session"), ctx.get("volatility_bucket"),
            live["n"], live["exp"], live["pf"], live["wr"], live["dd"],
            forward["n"], forward["exp"], forward["pf"], forward["wr"],
            score, state, size, json.dumps(hard), json.dumps(reasons),
            json.dumps(payload, sort_keys=True, default=str)
        ))

        return state, size, hard

    def record_live_trade(self, pos, trade_row):
        pos = dict(pos or {})
        trade = dict(trade_row or {})
        payload = j(trade.get("payload"))
        trade.update(payload)

        symbol = str(trade.get("symbol") or pos.get("symbol") or "").upper()
        side = str(trade.get("side") or pos.get("side") or "").upper()
        setup = str(trade.get("setup") or pos.get("setup") or "UNKNOWN").upper()
        source_id = str(trade.get("id") or trade.get("position_id") or "")

        if not symbol or not side or not source_id:
            return None

        d = self.decision_from_position(pos)
        ctx = self.context_from_decision(d, symbol)
        result_r, risk_usd = self.risk_from_position(pos, trade)

        keys = self.keys_for(symbol, side, setup, ctx)
        main_key = keys[0]

        opayload = {
            "version": VERSION,
            "source": "LIVE",
            "trade": trade,
            "position": pos,
            "decision": d,
            "context": ctx,
            "result_r": result_r,
            "risk_usd": risk_usd,
        }

        for key in keys:
            self.update_edge_memory(key, "LIVE", result_r, opayload)
            self.refresh_promotion(key, symbol, side, setup, ctx)

        try:
            self.db.execute("UPDATE trades SET pnl_r=? WHERE id=?", (result_r, trade.get("id")))
        except Exception:
            pass

        state_rows = self.q("SELECT state FROM promotion_state_v27 WHERE key=?", (main_key,))
        state = state_rows[0]["state"] if state_rows else "UNKNOWN"

        self.db.execute("""
        INSERT OR IGNORE INTO outcome_ledger_v27(
            ts,source,source_id,position_id,decision_id,symbol,side,setup,action,
            entry_ts,exit_ts,entry_price,exit_price,size_usd,pnl_usd,fees,risk_usd,result_r,
            mfe_r,mae_r,regime,session,volatility_bucket,news_bucket,context_key,
            quality_state,promotion_state,reason,payload
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            utc(), "LIVE", source_id, trade.get("position_id"), d.get("id") or d.get("decision_id"),
            symbol, side, setup, "CLOSE",
            pos.get("opened_at"), trade.get("ts"),
            f(trade.get("entry")), f(trade.get("exit")), f(trade.get("size_usd")),
            f(trade.get("pnl_usd")), f(trade.get("fees")), risk_usd, result_r,
            f(pos.get("mfe_r")), f(pos.get("mae_r")),
            ctx.get("regime"), ctx.get("session"), ctx.get("volatility_bucket"), ctx.get("news_bucket"),
            main_key,
            "OK" if risk_usd > 0 else "RISK_UNAVAILABLE",
            state,
            trade.get("reason"),
            json.dumps(opayload, sort_keys=True, default=str)
        ))

        self.audit("LIVE_OUTCOME_RECORDED", {
            "symbol": symbol, "side": side, "setup": setup,
            "trade_id": trade.get("id"), "result_r": result_r,
            "risk_usd": risk_usd, "state": state
        })

        return {"result_r": result_r, "risk_usd": risk_usd, "state": state}

    def record_forward_result(self, r):
        r = dict(r or {})
        source_id = str(r.get("id") or r.get("case_id") or f"{r.get('symbol')}|{r.get('resolved_at')}")
        symbol = str(r.get("symbol") or "").upper()
        side = str(r.get("side") or "").upper()
        setup = str(r.get("setup") or "UNKNOWN").upper()

        if not symbol or not side:
            return None

        ctx, d = self.context_from_forward_case(r.get("case_id"), symbol)
        result_r = f(r.get("result_r"))

        keys = self.keys_for(symbol, side, setup, ctx)
        main_key = keys[0]

        payload = {
            "version": VERSION,
            "source": "FORWARD",
            "result": r,
            "decision": d,
            "context": ctx,
            "result_r": result_r,
        }

        for key in keys:
            self.update_edge_memory(key, "FORWARD", result_r, payload)
            self.refresh_promotion(key, symbol, side, setup, ctx)

        state_rows = self.q("SELECT state FROM promotion_state_v27 WHERE key=?", (main_key,))
        state = state_rows[0]["state"] if state_rows else "UNKNOWN"

        self.db.execute("""
        INSERT OR IGNORE INTO outcome_ledger_v27(
            ts,source,source_id,position_id,decision_id,symbol,side,setup,action,
            entry_ts,exit_ts,entry_price,exit_price,size_usd,pnl_usd,fees,risk_usd,result_r,
            mfe_r,mae_r,regime,session,volatility_bucket,news_bucket,context_key,
            quality_state,promotion_state,reason,payload
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            utc(), "FORWARD", source_id, None, d.get("id") or d.get("decision_id"),
            symbol, side, setup, "FORWARD_RESULT",
            None, r.get("resolved_at"),
            f(r.get("entry_price")), f(r.get("exit_price")), f(r.get("size_usd")),
            0.0, 0.0, 1.0, result_r,
            0.0, 0.0,
            ctx.get("regime"), ctx.get("session"), ctx.get("volatility_bucket"), ctx.get("news_bucket"),
            main_key,
            "OK", state, r.get("outcome"),
            json.dumps(payload, sort_keys=True, default=str)
        ))

        return {"result_r": result_r, "state": state}

    def best_state_for_decision(self, d):
        dd = d.to_dict() if hasattr(d, "to_dict") else dict(getattr(d, "__dict__", {}) or {})
        symbol = str(dd.get("symbol") or "").upper()
        side = str(dd.get("side") or "").upper()
        setup = str(dd.get("setup") or "UNKNOWN").upper()
        ctx = self.context_from_decision(dd, symbol)
        keys = self.keys_for(symbol, side, setup, ctx)

        best = None
        for key in keys:
            rows = self.q("SELECT * FROM promotion_state_v27 WHERE key=?", (key,))
            if rows:
                best = rows[0]
                break

        if not best:
            key = keys[0]
            state, size, hard = self.refresh_promotion(key, symbol, side, setup, ctx)
            best = {
                "key": key,
                "state": state,
                "recommended_size_usd": size,
                "hard_vetoes": json.dumps(hard),
            }

        return best

    def apply_training_policy(self, d, wallet):
        if not envb("V27_PAPER_TRAINING_ENABLED", True):
            return d

        state_row = self.best_state_for_decision(d)
        state = state_row.get("state", "RESEARCH")
        hard = j(state_row.get("hard_vetoes"), [])
        size = f(state_row.get("recommended_size_usd"), self.size_for_state(state))
        action = str(getattr(d, "action", "")).upper()
        score = f(getattr(d, "final_score", 0))

        if state == "QUARANTINE" or hard:
            try:
                d.reasons.append("V27_QUARANTINE_BLOCK")
            except Exception:
                pass
            return d

        promote_probe_score = envf("V27_PROBE_PROMOTE_MIN_SCORE", 45)
        train_wait_score = envf("V27_WAIT_TRAIN_MIN_SCORE", 52)

        if action == "PROBE" and score >= promote_probe_score:
            d.action = "OPEN"
            try:
                d.reasons.append(f"V27_PROBE_TO_OPEN_{state}")
            except Exception:
                pass

        if action == "WAIT" and state in ("EXPLORE", "CANARY", "VALIDATED") and score >= train_wait_score:
            d.action = "OPEN"
            try:
                d.reasons.append(f"V27_WAIT_TO_TRAINING_OPEN_{state}")
            except Exception:
                pass

        if str(getattr(d, "action", "")).upper() == "OPEN":
            max_size = envf("V27_MAX_POSITION_SIZE_USD", 50000)
            min_size = envf("V27_RESEARCH_SIZE_USD", 4000)
            target = min(max_size, max(min_size, size))
            original = f(getattr(d, "size_usd", 0))
            d.size_usd = max(original, target)

            try:
                d.risk["v27_state"] = state
                d.risk["v27_size_usd"] = d.size_usd
                d.risk["v27_policy"] = VERSION
            except Exception:
                pass

            try:
                d.reasons.append(f"V27_STAGE_{state}_SIZE_{d.size_usd:.0f}")
            except Exception:
                pass

        return d

    def backfill(self):
        done_live = 0
        done_forward = 0

        rows = self.q("""
        SELECT t.*, p.payload AS position_payload
        FROM trades t
        LEFT JOIN positions p ON p.id=t.position_id
        ORDER BY t.id ASC
        """)
        for r in rows:
            try:
                pos = j(r.get("position_payload"))
                self.record_live_trade(pos, r)
                done_live += 1
            except Exception as e:
                self.audit("BACKFILL_LIVE_ERROR", {"error": repr(e), "trade": r})

        rows = self.q("SELECT * FROM forward_results ORDER BY id ASC LIMIT 20000")
        for r in rows:
            try:
                self.record_forward_result(r)
                done_forward += 1
            except Exception as e:
                self.audit("BACKFILL_FORWARD_ERROR", {"error": repr(e), "result": r})

        self.audit("BACKFILL_DONE", {"live": done_live, "forward": done_forward})
        return {"live": done_live, "forward": done_forward}

    def report(self):
        lines = []
        lines.append("===== V27 INSTITUTIONAL QUANT REPORT =====")
        lines.append(f"UTC: {utc()}")

        counts = {}
        for t in ["outcome_ledger_v27","promotion_state_v27","v27_quant_audit","edge_memory","trades","positions","forward_results"]:
            try:
                counts[t] = self.q(f"SELECT COUNT(*) c FROM {t}")[0]["c"]
            except Exception:
                counts[t] = None
        lines.append("COUNTS: " + json.dumps(counts, sort_keys=True))

        lines.append("")
        lines.append("PROMOTION STATES:")
        rows = self.q("""
        SELECT state, COUNT(*) n,
               ROUND(AVG(live_exp_r),5) live_exp,
               ROUND(AVG(forward_exp_r),5) forward_exp,
               ROUND(AVG(recommended_size_usd),2) avg_size
        FROM promotion_state_v27
        GROUP BY state
        ORDER BY n DESC
        """)
        for r in rows:
            lines.append(f"{r.get('state')} n={r.get('n')} liveExp={r.get('live_exp')} forwardExp={r.get('forward_exp')} avgSize={r.get('avg_size')}")

        lines.append("")
        lines.append("TOP PROMOTABLE:")
        rows = self.q("""
        SELECT key,state,live_n,ROUND(live_exp_r,4) live_exp,ROUND(live_pf,3) live_pf,
               forward_n,ROUND(forward_exp_r,4) fwd_exp,ROUND(forward_pf,3) fwd_pf,
               ROUND(combined_score,2) score,ROUND(recommended_size_usd,2) size
        FROM promotion_state_v27
        WHERE state!='QUARANTINE'
        ORDER BY state='VALIDATED' DESC, state='CANARY' DESC, state='EXPLORE' DESC, combined_score DESC
        LIMIT 25
        """)
        for r in rows:
            lines.append(f"{r.get('state')} size={r.get('size')} score={r.get('score')} liveN={r.get('live_n')} liveExp={r.get('live_exp')} livePF={r.get('live_pf')} fwdN={r.get('forward_n')} fwdExp={r.get('fwd_exp')} fwdPF={r.get('fwd_pf')} key={r.get('key')}")

        lines.append("")
        lines.append("QUARANTINE:")
        rows = self.q("""
        SELECT key,live_n,ROUND(live_exp_r,4) live_exp,ROUND(live_pf,3) live_pf,
               forward_n,ROUND(forward_exp_r,4) fwd_exp,ROUND(forward_pf,3) fwd_pf, hard_vetoes
        FROM promotion_state_v27
        WHERE state='QUARANTINE'
        ORDER BY live_exp_r ASC, forward_exp_r ASC
        LIMIT 20
        """)
        for r in rows:
            lines.append(f"Q liveN={r.get('live_n')} liveExp={r.get('live_exp')} livePF={r.get('live_pf')} fwdN={r.get('forward_n')} fwdExp={r.get('fwd_exp')} fwdPF={r.get('fwd_pf')} hard={r.get('hard_vetoes')} key={r.get('key')}")

        return "\n".join(lines)

def get_core(db):
    return V27QuantCore(db)
