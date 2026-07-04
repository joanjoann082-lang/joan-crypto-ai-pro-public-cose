
from __future__ import annotations

import json, os, math, datetime
from typing import Any, Dict, List, Tuple

VERSION = "V27_2_INSTITUTIONAL_CLEAN_QUANT_CORE"

def utc():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()

def f(x, default=0.0):
    try:
        if x is None:
            return default
        y = float(x)
        if math.isnan(y) or math.isinf(y):
            return default
        return y
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

def envb(k, d=True):
    v = os.getenv(k)
    if v is None:
        return bool(d)
    return str(v).lower() in ("1", "true", "yes", "on")

class V272CleanQuantCore:
    """
    Institutional clean quant layer.

    RAW data is never destroyed.
    Synthetic/bad data is excluded through data_quality_exclusions_v27_2.
    Clean edge/promotion tables are rebuilt from clean ledger rows only.
    """

    def __init__(self, db):
        self.db = db
        self.ensure_schema()

    def q(self, sql, params=()):
        try:
            return [dict(r) for r in self.db.query(sql, params)]
        except Exception:
            return []

    def execute(self, sql, params=()):
        return self.db.execute(sql, params)

    def ensure_schema(self):
        self.execute("""
        CREATE TABLE IF NOT EXISTS outcome_ledger_v27_2(
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
            quality_state TEXT NOT NULL,
            promotion_state TEXT,
            reason TEXT,
            payload TEXT NOT NULL,
            UNIQUE(source, source_id)
        )
        """)

        self.execute("""
        CREATE TABLE IF NOT EXISTS data_quality_exclusions_v27_2(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            source TEXT NOT NULL,
            source_id TEXT NOT NULL,
            reason TEXT NOT NULL,
            severity TEXT NOT NULL,
            payload TEXT NOT NULL,
            UNIQUE(source, source_id, reason)
        )
        """)

        self.execute("""
        CREATE TABLE IF NOT EXISTS edge_memory_v27_2_clean(
            key TEXT NOT NULL,
            source TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            n REAL NOT NULL,
            wins REAL NOT NULL,
            losses REAL NOT NULL,
            sum_r REAL NOT NULL,
            sum_pos_r REAL NOT NULL,
            sum_neg_r REAL NOT NULL,
            max_dd_r REAL NOT NULL,
            payload TEXT NOT NULL,
            PRIMARY KEY(key, source)
        )
        """)

        self.execute("""
        CREATE TABLE IF NOT EXISTS promotion_state_v27_2(
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
            lcb_score REAL NOT NULL,
            state TEXT NOT NULL,
            recommended_size_usd REAL NOT NULL,
            hard_vetoes TEXT NOT NULL,
            reasons TEXT NOT NULL,
            payload TEXT NOT NULL
        )
        """)

        self.execute("""
        CREATE TABLE IF NOT EXISTS v27_2_quant_audit(
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
            self.execute("""
            INSERT INTO v27_2_quant_audit(ts,event,symbol,side,setup,payload)
            VALUES(?,?,?,?,?,?)
            """, (
                utc(), event,
                payload.get("symbol"),
                payload.get("side"),
                payload.get("setup"),
                json.dumps(payload, sort_keys=True, default=str),
            ))
        except Exception:
            pass

    def add_exclusion(self, source, source_id, reason, severity, payload):
        self.execute("""
        INSERT OR IGNORE INTO data_quality_exclusions_v27_2(ts,source,source_id,reason,severity,payload)
        VALUES(?,?,?,?,?,?)
        """, (
            utc(), str(source), str(source_id), str(reason), str(severity),
            json.dumps(payload, sort_keys=True, default=str),
        ))

    def is_excluded(self, source, source_id):
        rows = self.q("""
        SELECT reason,severity
        FROM data_quality_exclusions_v27_2
        WHERE source=? AND source_id=?
        LIMIT 1
        """, (str(source), str(source_id)))
        return bool(rows), rows[0] if rows else None

    def decision_from_position(self, pos):
        meta = pos.get("meta") or {}
        d = meta.get("decision") or {}
        return d if isinstance(d, dict) else {}

    def context_from_decision(self, d, symbol):
        fs = d.get("feature_summary") or {}
        if isinstance(fs, dict):
            return {
                "regime": fs.get("regime") or fs.get("market_regime") or "UNKNOWN",
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
        rows = self.q("SELECT * FROM forward_cases WHERE id=? LIMIT 1", (case_id,))
        if rows:
            fc = rows[0]
            p = j(fc.get("payload"))
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

    def source_id_for(self, source, obj):
        if source == "LIVE":
            return str(obj.get("id") or obj.get("position_id") or "")
        if source == "FORWARD":
            return str(obj.get("id") or obj.get("case_id") or f"{obj.get('symbol')}|{obj.get('resolved_at')}")
        return str(obj.get("id") or obj.get("source_id") or utc())

    def classify_quality(self, source, source_id, symbol, side, setup, result_r, risk_usd, reason, payload):
        text = json.dumps(payload, sort_keys=True, default=str).upper()
        setup_u = str(setup).upper()
        reason_u = str(reason or "").upper()

        if "V27_TEST" in setup_u or "V27_TEST" in reason_u or "V27_TEST" in text:
            return "EXCLUDED", "SYNTHETIC_SELF_TEST", "HIGH"

        if "TEST" == setup_u or setup_u.endswith("_TEST") or reason_u.endswith("_TEST"):
            return "EXCLUDED", "SYNTHETIC_SELF_TEST", "HIGH"

        if abs(f(result_r)) > envf("V27_2_MAX_ABS_R_ALLOWED", 8.0):
            return "EXCLUDED", "IMPLAUSIBLE_R", "HIGH"

        if source == "LIVE" and f(risk_usd) <= 0:
            return "EXCLUDED", "MISSING_OR_ZERO_RISK", "HIGH"

        if not symbol or not side:
            return "EXCLUDED", "MISSING_SYMBOL_OR_SIDE", "HIGH"

        return "CLEAN", None, None

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

    def insert_outcome(self, row):
        source = row["source"]
        source_id = str(row["source_id"])

        exists = self.q("SELECT id FROM outcome_ledger_v27_2 WHERE source=? AND source_id=? LIMIT 1", (source, source_id))
        if exists:
            return False

        quality, excl_reason, severity = self.classify_quality(
            source, source_id, row.get("symbol"), row.get("side"), row.get("setup"),
            row.get("result_r"), row.get("risk_usd"), row.get("reason"), row.get("payload_obj") or {}
        )
        row["quality_state"] = quality

        if excl_reason:
            self.add_exclusion(source, source_id, excl_reason, severity, {
                "row": row,
                "quality": quality,
                "version": VERSION,
            })

        self.execute("""
        INSERT OR IGNORE INTO outcome_ledger_v27_2(
            ts,source,source_id,position_id,decision_id,symbol,side,setup,action,
            entry_ts,exit_ts,entry_price,exit_price,size_usd,pnl_usd,fees,risk_usd,result_r,
            mfe_r,mae_r,regime,session,volatility_bucket,news_bucket,context_key,
            quality_state,promotion_state,reason,payload
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            row.get("ts") or utc(),
            source,
            source_id,
            row.get("position_id"),
            row.get("decision_id"),
            row.get("symbol"),
            row.get("side"),
            row.get("setup"),
            row.get("action"),
            row.get("entry_ts"),
            row.get("exit_ts"),
            f(row.get("entry_price")),
            f(row.get("exit_price")),
            f(row.get("size_usd")),
            f(row.get("pnl_usd")),
            f(row.get("fees")),
            f(row.get("risk_usd")),
            f(row.get("result_r")),
            f(row.get("mfe_r")),
            f(row.get("mae_r")),
            row.get("regime"),
            row.get("session"),
            row.get("volatility_bucket"),
            row.get("news_bucket"),
            row.get("context_key"),
            quality,
            row.get("promotion_state"),
            row.get("reason"),
            json.dumps(row.get("payload_obj") or {}, sort_keys=True, default=str),
        ))

        return True

    def update_clean_edge(self, key, source, result_r, payload):
        rows = self.q("SELECT * FROM edge_memory_v27_2_clean WHERE key=? AND source=?", (key, source))
        if rows:
            r = rows[0]
            n = f(r.get("n")) + 1
            wins = f(r.get("wins")) + (1 if result_r > 0 else 0)
            losses = f(r.get("losses")) + (1 if result_r < 0 else 0)
            sum_r = f(r.get("sum_r")) + result_r
            sum_pos = f(r.get("sum_pos_r")) + max(0.0, result_r)
            sum_neg = f(r.get("sum_neg_r")) + min(0.0, result_r)
            dd = min(f(r.get("max_dd_r")), result_r)
            self.execute("""
            UPDATE edge_memory_v27_2_clean
            SET updated_at=?, n=?, wins=?, losses=?, sum_r=?, sum_pos_r=?, sum_neg_r=?, max_dd_r=?, payload=?
            WHERE key=? AND source=?
            """, (
                utc(), n, wins, losses, sum_r, sum_pos, sum_neg, dd,
                json.dumps(payload, sort_keys=True, default=str),
                key, source,
            ))
        else:
            self.execute("""
            INSERT OR REPLACE INTO edge_memory_v27_2_clean(
                key,source,updated_at,n,wins,losses,sum_r,sum_pos_r,sum_neg_r,max_dd_r,payload
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
            """, (
                key, source, utc(), 1,
                1 if result_r > 0 else 0,
                1 if result_r < 0 else 0,
                result_r,
                max(0.0, result_r),
                min(0.0, result_r),
                min(0.0, result_r),
                json.dumps(payload, sort_keys=True, default=str),
            ))

    def stats(self, key, source):
        rows = self.q("SELECT * FROM edge_memory_v27_2_clean WHERE key=? AND source=?", (key, source))
        if not rows:
            return {"n": 0.0, "exp": 0.0, "pf": 0.0, "wr": 0.0, "dd": 0.0}
        r = rows[0]
        n = f(r.get("n"))
        wins = f(r.get("wins"))
        pos = f(r.get("sum_pos_r"))
        neg = f(r.get("sum_neg_r"))
        return {
            "n": n,
            "exp": f(r.get("sum_r")) / max(1.0, n),
            "pf": pos / abs(neg) if neg < 0 else (999.0 if pos > 0 else 0.0),
            "wr": wins / max(1.0, n),
            "dd": f(r.get("max_dd_r")),
        }

    def lcb(self, exp, n):
        # Conservative lower confidence proxy in R units.
        return exp - 0.35 / math.sqrt(max(1.0, n))

    def classify_state(self, live, forward):
        hard = []
        reasons = []

        live_lcb = self.lcb(live["exp"], live["n"]) if live["n"] > 0 else 0.0
        fwd_lcb = self.lcb(forward["exp"], forward["n"]) if forward["n"] > 0 else 0.0

        score = 0.0
        score += min(30.0, live["n"] * 2.5)
        score += min(20.0, forward["n"] / 300.0)
        score += max(-35.0, min(35.0, live_lcb * 150.0))
        score += max(-20.0, min(20.0, fwd_lcb * 100.0))
        score += max(-12.0, min(15.0, (live["pf"] - 1.0) * 10.0 if live["pf"] else -8.0))
        score += max(-8.0, min(10.0, (forward["pf"] - 1.0) * 6.0 if forward["pf"] else -5.0))

        if live["n"] >= 5 and live["exp"] < -0.08 and live["pf"] < 0.90:
            hard.append("LIVE_NEGATIVE_EDGE")
        if live["n"] >= 8 and live["dd"] <= -1.50:
            hard.append("LIVE_TAIL_LOSS")
        if forward["n"] >= 500 and forward["exp"] < -0.03 and forward["pf"] < 0.90:
            hard.append("FORWARD_NEGATIVE_EDGE")
        if forward["n"] >= 3000 and forward["exp"] < -0.01 and forward["pf"] < 0.98 and live["n"] < 3:
            hard.append("FORWARD_PRIOR_NEGATIVE_NO_LIVE_SUPPORT")

        if hard:
            return "QUARANTINE", score, min(live_lcb, fwd_lcb), hard, reasons

        if live["n"] >= 30 and live_lcb > 0.03 and live["pf"] > 1.20:
            return "VALIDATED", score, live_lcb, hard, reasons
        if live["n"] >= 12 and live_lcb > 0.00 and live["pf"] > 1.08:
            return "CANARY", score, live_lcb, hard, reasons
        if forward["n"] >= 500 and fwd_lcb > 0.00 and forward["pf"] > 1.03:
            return "EXPLORE", score, fwd_lcb, hard, reasons

        return "RESEARCH", score, min(live_lcb, fwd_lcb), hard, reasons

    def size_for_state(self, state):
        if state == "VALIDATED":
            return envf("V27_2_VALIDATED_SIZE_USD", 30000)
        if state == "CANARY":
            return envf("V27_2_CANARY_SIZE_USD", 15000)
        if state == "EXPLORE":
            return envf("V27_2_EXPLORE_SIZE_USD", 8000)
        if state == "RESEARCH":
            return envf("V27_2_RESEARCH_SIZE_USD", 4000)
        return 0.0

    def refresh_promotion_for_key(self, key, meta):
        live = self.stats(key, "LIVE")
        forward = self.stats(key, "FORWARD")
        state, score, lcb_score, hard, reasons = self.classify_state(live, forward)
        size = self.size_for_state(state)

        payload = {
            "version": VERSION,
            "key": key,
            "meta": meta,
            "live": live,
            "forward": forward,
            "state": state,
            "score": score,
            "lcb_score": lcb_score,
            "hard_vetoes": hard,
            "reasons": reasons,
        }

        self.execute("""
        INSERT OR REPLACE INTO promotion_state_v27_2(
            key,updated_at,symbol,side,setup,regime,session,volatility_bucket,
            live_n,live_exp_r,live_pf,live_winrate,live_dd_r,
            forward_n,forward_exp_r,forward_pf,forward_winrate,
            combined_score,lcb_score,state,recommended_size_usd,hard_vetoes,reasons,payload
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            key, utc(),
            meta.get("symbol"), meta.get("side"), meta.get("setup"),
            meta.get("regime"), meta.get("session"), meta.get("volatility_bucket"),
            live["n"], live["exp"], live["pf"], live["wr"], live["dd"],
            forward["n"], forward["exp"], forward["pf"], forward["wr"],
            score, lcb_score, state, size,
            json.dumps(hard), json.dumps(reasons),
            json.dumps(payload, sort_keys=True, default=str),
        ))

    def record_live_trade(self, pos, trade_row):
        pos = dict(pos or {})
        trade = dict(trade_row or {})
        payload = j(trade.get("payload"))
        trade.update(payload)

        symbol = str(trade.get("symbol") or pos.get("symbol") or "").upper()
        side = str(trade.get("side") or pos.get("side") or "").upper()
        setup = str(trade.get("setup") or pos.get("setup") or "UNKNOWN").upper()
        source_id = self.source_id_for("LIVE", trade)
        if not source_id:
            return None

        d = self.decision_from_position(pos)
        ctx = self.context_from_decision(d, symbol)
        result_r, risk_usd = self.risk_from_position(pos, trade)
        keys = self.keys_for(symbol, side, setup, ctx)
        main_key = keys[0]

        obj = {
            "version": VERSION,
            "source": "LIVE",
            "trade": trade,
            "position": pos,
            "decision": d,
            "context": ctx,
            "keys": keys,
            "result_r": result_r,
            "risk_usd": risk_usd,
        }

        inserted = self.insert_outcome({
            "source": "LIVE",
            "source_id": source_id,
            "position_id": trade.get("position_id"),
            "decision_id": d.get("id") or d.get("decision_id"),
            "symbol": symbol,
            "side": side,
            "setup": setup,
            "action": "CLOSE",
            "entry_ts": pos.get("opened_at"),
            "exit_ts": trade.get("ts"),
            "entry_price": f(trade.get("entry")),
            "exit_price": f(trade.get("exit")),
            "size_usd": f(trade.get("size_usd")),
            "pnl_usd": f(trade.get("pnl_usd")),
            "fees": f(trade.get("fees")),
            "risk_usd": risk_usd,
            "result_r": result_r,
            "mfe_r": f(pos.get("mfe_r")),
            "mae_r": f(pos.get("mae_r")),
            "regime": ctx.get("regime"),
            "session": ctx.get("session"),
            "volatility_bucket": ctx.get("volatility_bucket"),
            "news_bucket": ctx.get("news_bucket"),
            "context_key": main_key,
            "reason": trade.get("reason"),
            "payload_obj": obj,
        })

        try:
            self.execute("UPDATE trades SET pnl_r=? WHERE id=?", (result_r, trade.get("id")))
        except Exception:
            pass

        if inserted:
            self.audit("LIVE_OUTCOME_INSERTED", {
                "symbol": symbol, "side": side, "setup": setup,
                "source_id": source_id, "result_r": result_r, "risk_usd": risk_usd,
            })

        return {"result_r": result_r, "risk_usd": risk_usd, "inserted": inserted}

    def record_forward_result(self, r):
        r = dict(r or {})
        source_id = self.source_id_for("FORWARD", r)
        symbol = str(r.get("symbol") or "").upper()
        side = str(r.get("side") or "").upper()
        setup = str(r.get("setup") or "UNKNOWN").upper()
        if not source_id or not symbol or not side:
            return None

        ctx, d = self.context_from_forward_case(r.get("case_id"), symbol)
        result_r = f(r.get("result_r"))
        keys = self.keys_for(symbol, side, setup, ctx)
        main_key = keys[0]

        obj = {
            "version": VERSION,
            "source": "FORWARD",
            "result": r,
            "decision": d,
            "context": ctx,
            "keys": keys,
            "result_r": result_r,
        }

        inserted = self.insert_outcome({
            "source": "FORWARD",
            "source_id": source_id,
            "position_id": None,
            "decision_id": d.get("id") or d.get("decision_id"),
            "symbol": symbol,
            "side": side,
            "setup": setup,
            "action": "FORWARD_RESULT",
            "entry_ts": None,
            "exit_ts": r.get("resolved_at"),
            "entry_price": f(r.get("entry_price")),
            "exit_price": f(r.get("exit_price")),
            "size_usd": f(r.get("size_usd")),
            "pnl_usd": 0.0,
            "fees": 0.0,
            "risk_usd": 1.0,
            "result_r": result_r,
            "mfe_r": 0.0,
            "mae_r": 0.0,
            "regime": ctx.get("regime"),
            "session": ctx.get("session"),
            "volatility_bucket": ctx.get("volatility_bucket"),
            "news_bucket": ctx.get("news_bucket"),
            "context_key": main_key,
            "reason": r.get("outcome"),
            "payload_obj": obj,
        })

        return {"result_r": result_r, "inserted": inserted}

    def rebuild_clean_state(self):
        self.execute("DELETE FROM edge_memory_v27_2_clean")
        self.execute("DELETE FROM promotion_state_v27_2")

        rows = self.q("""
        SELECT *
        FROM outcome_ledger_v27_2
        WHERE quality_state='CLEAN'
          AND NOT EXISTS (
              SELECT 1 FROM data_quality_exclusions_v27_2 x
              WHERE x.source=outcome_ledger_v27_2.source
                AND x.source_id=outcome_ledger_v27_2.source_id
          )
        ORDER BY id ASC
        """)

        touched = {}

        for r in rows:
            p = j(r.get("payload"))
            keys = p.get("keys") or self.keys_for(
                r.get("symbol"), r.get("side"), r.get("setup"),
                {
                    "regime": r.get("regime"),
                    "session": r.get("session"),
                    "volatility_bucket": r.get("volatility_bucket"),
                    "news_bucket": r.get("news_bucket"),
                },
            )
            source = str(r.get("source")).upper()
            rr = f(r.get("result_r"))

            meta = {
                "symbol": r.get("symbol"),
                "side": r.get("side"),
                "setup": r.get("setup"),
                "regime": r.get("regime"),
                "session": r.get("session"),
                "volatility_bucket": r.get("volatility_bucket"),
            }

            for key in keys:
                self.update_clean_edge(key, source, rr, p)
                touched[key] = meta

        for key, meta in touched.items():
            self.refresh_promotion_for_key(key, meta)

        self.audit("CLEAN_REBUILD_DONE", {"rows": len(rows), "keys": len(touched)})
        return {"clean_rows": len(rows), "keys": len(touched)}

    def backfill(self):
        live = 0
        fwd = 0

        rows = self.q("""
        SELECT t.*, p.payload AS position_payload
        FROM trades t
        LEFT JOIN positions p ON p.id=t.position_id
        ORDER BY t.id ASC
        """)
        for r in rows:
            try:
                pos = j(r.get("position_payload"))
                res = self.record_live_trade(pos, r)
                if res:
                    live += 1
            except Exception as e:
                self.audit("BACKFILL_LIVE_ERROR", {"error": repr(e), "row": r})

        rows = self.q("SELECT * FROM forward_results ORDER BY id ASC LIMIT 50000")
        for r in rows:
            try:
                res = self.record_forward_result(r)
                if res:
                    fwd += 1
            except Exception as e:
                self.audit("BACKFILL_FORWARD_ERROR", {"error": repr(e), "row": r})

        rebuilt = self.rebuild_clean_state()
        self.audit("BACKFILL_DONE", {"live": live, "forward": fwd, "rebuilt": rebuilt})
        return {"live_seen": live, "forward_seen": fwd, "rebuilt": rebuilt}

    def promotion_rows_for_keys(self, keys):
        out = {}
        for k in keys:
            rows = self.q("SELECT * FROM promotion_state_v27_2 WHERE key=? LIMIT 1", (k,))
            if rows:
                out[k] = rows[0]
        return out

    def execution_policy_for_decision(self, d):
        dd = d.to_dict() if hasattr(d, "to_dict") else dict(getattr(d, "__dict__", {}) or {})
        symbol = str(dd.get("symbol") or "").upper()
        side = str(dd.get("side") or "").upper()
        setup = str(dd.get("setup") or "UNKNOWN").upper()
        ctx = self.context_from_decision(dd, symbol)
        keys = self.keys_for(symbol, side, setup, ctx)
        rows = self.promotion_rows_for_keys(keys)

        # Blocking hierarchy: broad negative evidence blocks execution, but not research/forward.
        blocking = []
        for k in keys:
            r = rows.get(k)
            if not r:
                continue
            state = r.get("state")
            hard = j(r.get("hard_vetoes"), [])
            if state == "QUARANTINE" and any(x in hard for x in [
                "LIVE_NEGATIVE_EDGE",
                "FORWARD_NEGATIVE_EDGE",
                "FORWARD_PRIOR_NEGATIVE_NO_LIVE_SUPPORT",
                "LIVE_TAIL_LOSS",
            ]):
                blocking.append((k, hard))

        if blocking:
            return {
                "state": "QUARANTINE",
                "size": 0.0,
                "block": True,
                "block_key": blocking[0][0],
                "hard_vetoes": blocking[0][1],
                "keys": keys,
            }

        # Use most specific available promotion row. If none, RESEARCH.
        chosen = None
        chosen_key = None
        for k in keys:
            if k in rows:
                chosen = rows[k]
                chosen_key = k
                break

        if chosen:
            return {
                "state": chosen.get("state") or "RESEARCH",
                "size": f(chosen.get("recommended_size_usd"), self.size_for_state(chosen.get("state") or "RESEARCH")),
                "block": False,
                "key": chosen_key,
                "hard_vetoes": j(chosen.get("hard_vetoes"), []),
                "keys": keys,
            }

        return {
            "state": "RESEARCH",
            "size": self.size_for_state("RESEARCH"),
            "block": False,
            "key": keys[0] if keys else None,
            "hard_vetoes": [],
            "keys": keys,
        }

    def size_for_state(self, state):
        if state == "VALIDATED":
            return envf("V27_2_VALIDATED_SIZE_USD", 30000)
        if state == "CANARY":
            return envf("V27_2_CANARY_SIZE_USD", 15000)
        if state == "EXPLORE":
            return envf("V27_2_EXPLORE_SIZE_USD", 8000)
        if state == "RESEARCH":
            return envf("V27_2_RESEARCH_SIZE_USD", 4000)
        return 0.0

    def apply_training_policy(self, d, wallet):
        if not envb("V27_2_PAPER_TRAINING_ENABLED", True):
            return d

        policy = self.execution_policy_for_decision(d)
        action = str(getattr(d, "action", "")).upper()
        score = f(getattr(d, "final_score", 0))

        if policy.get("block"):
            try:
                d.reasons.append(f"V27_2_EXEC_BLOCK_{policy.get('block_key')}")
                d.reasons.append("V27_2_RESEARCH_ONLY_CONTINUES")
            except Exception:
                pass
            return d

        state = policy.get("state", "RESEARCH")
        size = f(policy.get("size"), self.size_for_state(state))

        promote_probe_score = envf("V27_2_PROBE_PROMOTE_MIN_SCORE", 45)
        train_wait_score = envf("V27_2_WAIT_TRAIN_MIN_SCORE", 52)

        if action == "PROBE" and score >= promote_probe_score:
            d.action = "OPEN"
            try:
                d.reasons.append(f"V27_2_PROBE_TO_OPEN_{state}")
            except Exception:
                pass

        if action == "WAIT" and state in ("EXPLORE", "CANARY", "VALIDATED") and score >= train_wait_score:
            d.action = "OPEN"
            try:
                d.reasons.append(f"V27_2_WAIT_TO_TRAINING_OPEN_{state}")
            except Exception:
                pass

        if str(getattr(d, "action", "")).upper() == "OPEN":
            max_size = envf("V27_2_MAX_POSITION_SIZE_USD", 50000)
            min_size = envf("V27_2_RESEARCH_SIZE_USD", 4000)
            target = min(max_size, max(min_size, size))
            original = f(getattr(d, "size_usd", 0))
            d.size_usd = max(original, target)

            try:
                d.risk["v27_2_state"] = state
                d.risk["v27_2_size_usd"] = d.size_usd
                d.risk["v27_2_policy"] = VERSION
            except Exception:
                pass

            try:
                d.reasons.append(f"V27_2_STAGE_{state}_SIZE_{d.size_usd:.0f}")
            except Exception:
                pass

        return d

    def report(self):
        lines = []
        lines.append("===== V27.2 INSTITUTIONAL CLEAN QUANT REPORT =====")
        lines.append(f"UTC: {utc()}")

        counts = {}
        for t in [
            "outcome_ledger_v27_2",
            "data_quality_exclusions_v27_2",
            "edge_memory_v27_2_clean",
            "promotion_state_v27_2",
            "v27_2_quant_audit",
            "trades",
            "positions",
            "forward_results",
        ]:
            try:
                counts[t] = self.q(f"SELECT COUNT(*) c FROM {t}")[0]["c"]
            except Exception:
                counts[t] = None
        lines.append("COUNTS: " + json.dumps(counts, sort_keys=True))

        lines.append("")
        lines.append("QUALITY:")
        rows = self.q("""
        SELECT quality_state, source, COUNT(*) n
        FROM outcome_ledger_v27_2
        GROUP BY quality_state, source
        ORDER BY quality_state, source
        """)
        for r in rows:
            lines.append(f"{r.get('quality_state')} {r.get('source')} n={r.get('n')}")

        lines.append("")
        lines.append("EXCLUSIONS:")
        rows = self.q("""
        SELECT reason,severity,COUNT(*) n
        FROM data_quality_exclusions_v27_2
        GROUP BY reason,severity
        ORDER BY n DESC
        """)
        for r in rows:
            lines.append(f"{r.get('reason')} severity={r.get('severity')} n={r.get('n')}")

        lines.append("")
        lines.append("PROMOTION STATES:")
        rows = self.q("""
        SELECT state, COUNT(*) n,
               ROUND(AVG(live_exp_r),5) live_exp,
               ROUND(AVG(forward_exp_r),5) forward_exp,
               ROUND(AVG(recommended_size_usd),2) avg_size
        FROM promotion_state_v27_2
        GROUP BY state
        ORDER BY n DESC
        """)
        for r in rows:
            lines.append(f"{r.get('state')} n={r.get('n')} liveExp={r.get('live_exp')} forwardExp={r.get('forward_exp')} avgSize={r.get('avg_size')}")

        lines.append("")
        lines.append("TOP EXECUTABLE:")
        rows = self.q("""
        SELECT key,state,ROUND(combined_score,2) score,ROUND(lcb_score,4) lcb,
               live_n,ROUND(live_exp_r,4) live_exp,ROUND(live_pf,3) live_pf,
               forward_n,ROUND(forward_exp_r,4) fwd_exp,ROUND(forward_pf,3) fwd_pf,
               ROUND(recommended_size_usd,2) size
        FROM promotion_state_v27_2
        WHERE state!='QUARANTINE'
        ORDER BY state='VALIDATED' DESC, state='CANARY' DESC, state='EXPLORE' DESC,
                 combined_score DESC
        LIMIT 25
        """)
        for r in rows:
            lines.append(
                f"{r.get('state')} size={r.get('size')} score={r.get('score')} lcb={r.get('lcb')} "
                f"liveN={r.get('live_n')} liveExp={r.get('live_exp')} livePF={r.get('live_pf')} "
                f"fwdN={r.get('forward_n')} fwdExp={r.get('fwd_exp')} fwdPF={r.get('fwd_pf')} "
                f"key={r.get('key')}"
            )

        lines.append("")
        lines.append("QUARANTINE:")
        rows = self.q("""
        SELECT key,live_n,ROUND(live_exp_r,4) live_exp,ROUND(live_pf,3) live_pf,
               forward_n,ROUND(forward_exp_r,4) fwd_exp,ROUND(forward_pf,3) fwd_pf,
               hard_vetoes
        FROM promotion_state_v27_2
        WHERE state='QUARANTINE'
        ORDER BY live_exp_r ASC, forward_exp_r ASC
        LIMIT 25
        """)
        for r in rows:
            lines.append(
                f"Q liveN={r.get('live_n')} liveExp={r.get('live_exp')} livePF={r.get('live_pf')} "
                f"fwdN={r.get('forward_n')} fwdExp={r.get('fwd_exp')} fwdPF={r.get('fwd_pf')} "
                f"hard={r.get('hard_vetoes')} key={r.get('key')}"
            )

        return "\n".join(lines)

def get_core(db):
    return V272CleanQuantCore(db)
