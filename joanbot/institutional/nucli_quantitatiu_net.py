from __future__ import annotations

import json
import math
import os
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple
from .aprenentatge_causal_net import MotorAprenentatgeCausalNet

VERSIO = "NUCLI_QUANTITATIU_NET_1"


def ara_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def num(x: Any, default: float = 0.0) -> float:
    try:
        if x is None:
            return default
        y = float(x)
        if math.isnan(y) or math.isinf(y):
            return default
        return y
    except Exception:
        return default


def llegir_json(x: Any, default: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if default is None:
        default = {}
    try:
        if isinstance(x, dict):
            return dict(x)
        if not x:
            return dict(default)
        v = json.loads(str(x))
        return v if isinstance(v, dict) else dict(default)
    except Exception:
        return dict(default)


def env_num(k: str, d: float) -> float:
    try:
        return float(os.getenv(k, d))
    except Exception:
        return float(d)


def env_bool(k: str, d: bool = True) -> bool:
    v = os.getenv(k)
    if v is None:
        return bool(d)
    return str(v).strip().lower() in {"1", "true", "yes", "on", "si", "sí"}


class NucliQuantitatiuNet:
    """
    Nucli quantitatiu net i natiu.

    Principis:
    - Les dades brutes no s'esborren.
    - Les mostres dolentes s'exclouen formalment.
    - La memòria neta es reconstrueix des del llibre de resultats.
    - L'execució queda bloquejada per evidència negativa específica, no per GLOBAL.
    - FORWARD i LIVE alimenten el mateix procés de promoció.
    """

    def __init__(self, db: Any):
        self.db = db
        self._reconstruccio_historica = False
        self.causal = MotorAprenentatgeCausalNet()
        self.assegura_esquema()

    def q(self, sql: str, params: Iterable[Any] = ()) -> List[Dict[str, Any]]:
        try:
            return [dict(r) for r in self.db.query(sql, tuple(params))]
        except Exception:
            return []

    def exec(self, sql: str, params: Iterable[Any] = ()) -> Any:
        return self.db.execute(sql, tuple(params))

    def assegura_esquema(self) -> None:
        self.exec("""
        CREATE TABLE IF NOT EXISTS resultats_quant_nets(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            font TEXT NOT NULL,
            font_id TEXT NOT NULL,
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
            resultat_r REAL,
            mfe_r REAL,
            mae_r REAL,
            regime TEXT,
            session TEXT,
            volatility_bucket TEXT,
            news_bucket TEXT,
            context_key TEXT,
            qualitat TEXT NOT NULL,
            estat_promocio TEXT,
            motiu TEXT,
            payload TEXT NOT NULL,
            UNIQUE(font, font_id)
        )
        """)
        self.exec("""
        CREATE TABLE IF NOT EXISTS exclusions_qualitat_dades(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            font TEXT NOT NULL,
            font_id TEXT NOT NULL,
            motiu TEXT NOT NULL,
            severitat TEXT NOT NULL,
            payload TEXT NOT NULL,
            UNIQUE(font, font_id, motiu)
        )
        """)
        self.exec("""
        CREATE TABLE IF NOT EXISTS memoria_edge_neta(
            key TEXT NOT NULL,
            font TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            n REAL NOT NULL,
            wins REAL NOT NULL,
            losses REAL NOT NULL,
            sum_r REAL NOT NULL,
            sum_pos_r REAL NOT NULL,
            sum_neg_r REAL NOT NULL,
            max_dd_r REAL NOT NULL,
            payload TEXT NOT NULL,
            PRIMARY KEY(key, font)
        )
        """)
        self.exec("""
        CREATE TABLE IF NOT EXISTS estat_promocio_quant(
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
            score_compost REAL NOT NULL,
            score_lcb REAL NOT NULL,
            estat TEXT NOT NULL,
            mida_recomanada_usd REAL NOT NULL,
            vetos TEXT NOT NULL,
            motius TEXT NOT NULL,
            payload TEXT NOT NULL
        )
        """)
        self.exec("""
        CREATE TABLE IF NOT EXISTS auditoria_quant_neta(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            event TEXT NOT NULL,
            symbol TEXT,
            side TEXT,
            setup TEXT,
            payload TEXT NOT NULL
        )
        """)
        self.exec("""
        CREATE TABLE IF NOT EXISTS estat_causal_quant(
            key TEXT PRIMARY KEY,
            updated_at TEXT NOT NULL,
            estat TEXT NOT NULL,
            live_n REAL NOT NULL,
            live_exp_r REAL NOT NULL,
            live_pf REAL NOT NULL,
            forward_n REAL NOT NULL,
            forward_exp_r REAL NOT NULL,
            forward_pf REAL NOT NULL,
            payload TEXT NOT NULL
        )
        """)

    def audita(self, event: str, payload: Dict[str, Any]) -> None:
        try:
            self.exec(
                "INSERT INTO auditoria_quant_neta(ts,event,symbol,side,setup,payload) VALUES(?,?,?,?,?,?)",
                (
                    ara_utc(),
                    event,
                    payload.get("symbol"),
                    payload.get("side"),
                    payload.get("setup"),
                    json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str),
                ),
            )
        except Exception:
            pass

    def afegeix_exclusio(self, font: str, font_id: str, motiu: str, severitat: str, payload: Dict[str, Any]) -> None:
        self.exec(
            """
            INSERT OR IGNORE INTO exclusions_qualitat_dades(ts,font,font_id,motiu,severitat,payload)
            VALUES(?,?,?,?,?,?)
            """,
            (ara_utc(), str(font), str(font_id), str(motiu), str(severitat), json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)),
        )

    def decisio_de_posicio(self, pos: Dict[str, Any]) -> Dict[str, Any]:
        p = dict(pos or {})
        meta = p.get("meta") or {}
        d = meta.get("decision") or {}
        return d if isinstance(d, dict) else {}

    def context_de_decisio(self, d: Dict[str, Any], symbol: str = "") -> Dict[str, Any]:
        fs = d.get("feature_summary") or {}
        if isinstance(fs, dict):
            return {
                "regime": str(fs.get("regime") or fs.get("market_regime") or "UNKNOWN").upper(),
                "session": str(fs.get("session") or "UNKNOWN").upper(),
                "volatility_bucket": str(fs.get("volatility_bucket") or fs.get("vol_bucket") or "UNKNOWN").upper(),
                "news_bucket": str(fs.get("news_bucket") or "UNKNOWN").upper(),
                "mapa_causal": fs.get("mapa_causal") or fs.get("causal") or {},
                "technical": fs.get("technical") or {},
                "levels": fs.get("levels") or {},
                "micro": fs.get("micro") or {},
                "derivatives": fs.get("derivatives") or {},
            }
        return {"regime": "UNKNOWN", "session": "UNKNOWN", "volatility_bucket": "UNKNOWN", "news_bucket": "UNKNOWN", "mapa_causal": {}}

    def carrega_cas_forward(self, case_id: Any) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        rows = self.q("SELECT * FROM forward_cases WHERE id=? LIMIT 1", (case_id,))
        if not rows:
            return {}, {}
        fc = rows[0]
        payload = llegir_json(fc.get("payload"))
        d = payload.get("decision") if isinstance(payload.get("decision"), dict) else {}
        ctx = self.context_de_decisio(d, str(fc.get("symbol") or ""))
        return fc, {"payload": payload, "decision": d, "context": ctx}

    def claus(self, symbol: str, side: str, setup: str, ctx: Dict[str, Any]) -> List[str]:
        symbol = str(symbol or "UNKNOWN").upper()
        side = str(side or "UNKNOWN").upper()
        setup = str(setup or "UNKNOWN").upper()
        regime = str(ctx.get("regime") or "UNKNOWN").upper()
        session = str(ctx.get("session") or "UNKNOWN").upper()
        vol = str(ctx.get("volatility_bucket") or "UNKNOWN").upper()
        news = str(ctx.get("news_bucket") or "UNKNOWN").upper()
        base = [
            f"SETUP|{symbol}|{side}|{setup}|{regime}|{session}|{vol}|{news}",
            f"SETUP|{symbol}|{side}|{setup}|{regime}|{session}",
            f"SETUP|{symbol}|{side}|{setup}|{regime}",
            f"SYM_SIDE_REGIME|{symbol}|{side}|{regime}",
            f"SYM_SIDE|{symbol}|{side}",
            f"SIDE_REGIME|{side}|{regime}",
            f"SIDE|{side}",
            "GLOBAL",
        ]
        mapa = ctx.get("mapa_causal") or {}
        causals = self.causal.claus_causals(symbol, side, setup, regime, mapa) if isinstance(mapa, dict) else []
        return [base[0]] + causals + base[1:]

    def calcula_r_live(self, pos: Dict[str, Any], trade: Dict[str, Any]) -> Dict[str, float]:
        pos = dict(pos or {})
        trade = dict(trade or {})
        pnl = num(trade.get("pnl_usd"))
        size = abs(num(trade.get("size_usd")))
        entry = num(trade.get("entry") or trade.get("entry_price") or pos.get("entry_price") or pos.get("entry"))
        d = self.decisio_de_posicio(pos)
        stop = num(pos.get("initial_stop_loss")) or num(pos.get("stop_loss")) or num(d.get("stop_loss"))
        risk_usd = 0.0
        if entry > 0 and stop > 0 and size > 0:
            risk_usd = abs(entry - stop) / entry * size
        if risk_usd <= 0:
            r = d.get("risk") or {}
            risk_usd = num(r.get("risk_usd")) * num(trade.get("close_pct"), 1.0)
        resultat_r = pnl / risk_usd if risk_usd > 0 else 0.0
        return {"resultat_r": resultat_r, "risk_usd": risk_usd}

    def font_id(self, font: str, obj: Dict[str, Any]) -> str:
        if font == "LIVE":
            return str(obj.get("id") or obj.get("position_id") or "")
        if font == "FORWARD":
            return str(obj.get("id") or obj.get("case_id") or f"{obj.get('symbol')}|{obj.get('resolved_at')}")
        return str(obj.get("id") or obj.get("font_id") or ara_utc())

    def classifica_qualitat(self, font: str, font_id: str, row: Dict[str, Any]) -> Tuple[str, Optional[str], Optional[str]]:
        payload_txt = json.dumps(row.get("payload_obj") or {}, ensure_ascii=False, sort_keys=True, default=str).upper()
        setup = str(row.get("setup") or "").upper()
        motiu = str(row.get("motiu") or "").upper()
        symbol = str(row.get("symbol") or "").upper()
        side = str(row.get("side") or "").upper()
        resultat_r = num(row.get("resultat_r"))
        risk_usd = num(row.get("risk_usd"))
        if "TEST" in setup or "SELF_TEST" in motiu or "V27_TEST" in payload_txt or "V27_2_TEST" in payload_txt:
            return "EXCLOS", "PROVA_SINTETICA", "ALTA"
        if not symbol or not side or symbol == "UNKNOWN" or side == "UNKNOWN":
            return "EXCLOS", "FALTA_SYMBOL_O_DIRECCIO", "ALTA"
        if abs(resultat_r) > env_num("MAX_R_ABSOLUTA_ADMESA", 8.0):
            return "EXCLOS", "R_INVERSEMBLANT", "ALTA"
        if font == "LIVE" and risk_usd <= 0:
            return "EXCLOS", "RISC_INICIAL_ABSENT", "ALTA"
        return "NET", None, None

    def insereix_resultat(self, row: Dict[str, Any]) -> bool:
        font = str(row["font"]).upper()
        font_id = str(row["font_id"])
        if self.q("SELECT id FROM resultats_quant_nets WHERE font=? AND font_id=? LIMIT 1", (font, font_id)):
            return False
        qualitat, motiu_exclusio, severitat = self.classifica_qualitat(font, font_id, row)
        row["qualitat"] = qualitat
        if motiu_exclusio:
            self.afegeix_exclusio(font, font_id, motiu_exclusio, severitat or "ALTA", {"row": row, "versio": VERSIO})
        self.exec(
            """
            INSERT OR IGNORE INTO resultats_quant_nets(
                ts,font,font_id,position_id,decision_id,symbol,side,setup,action,
                entry_ts,exit_ts,entry_price,exit_price,size_usd,pnl_usd,fees,risk_usd,resultat_r,
                mfe_r,mae_r,regime,session,volatility_bucket,news_bucket,context_key,
                qualitat,estat_promocio,motiu,payload
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                row.get("ts") or ara_utc(),
                font,
                font_id,
                row.get("position_id"),
                row.get("decision_id"),
                str(row.get("symbol") or "").upper(),
                str(row.get("side") or "").upper(),
                str(row.get("setup") or "UNKNOWN").upper(),
                row.get("action"),
                row.get("entry_ts"),
                row.get("exit_ts"),
                num(row.get("entry_price")),
                num(row.get("exit_price")),
                num(row.get("size_usd")),
                num(row.get("pnl_usd")),
                num(row.get("fees")),
                num(row.get("risk_usd")),
                num(row.get("resultat_r")),
                num(row.get("mfe_r")),
                num(row.get("mae_r")),
                row.get("regime"),
                row.get("session"),
                row.get("volatility_bucket"),
                row.get("news_bucket"),
                row.get("context_key"),
                qualitat,
                row.get("estat_promocio"),
                row.get("motiu"),
                json.dumps(row.get("payload_obj") or {}, ensure_ascii=False, sort_keys=True, default=str),
            ),
        )
        return True

    def actualitza_memoria_neta(self, key: str, font: str, resultat_r: float, payload: Dict[str, Any]) -> None:
        font = str(font).upper()
        rows = self.q("SELECT * FROM memoria_edge_neta WHERE key=? AND font=?", (key, font))
        if rows:
            r = rows[0]
            n = num(r.get("n")) + 1
            wins = num(r.get("wins")) + (1 if resultat_r > 0 else 0)
            losses = num(r.get("losses")) + (1 if resultat_r < 0 else 0)
            sum_r = num(r.get("sum_r")) + resultat_r
            pos = num(r.get("sum_pos_r")) + max(0.0, resultat_r)
            neg = num(r.get("sum_neg_r")) + min(0.0, resultat_r)
            dd = min(num(r.get("max_dd_r")), resultat_r)
            self.exec(
                """
                UPDATE memoria_edge_neta
                SET updated_at=?, n=?, wins=?, losses=?, sum_r=?, sum_pos_r=?, sum_neg_r=?, max_dd_r=?, payload=?
                WHERE key=? AND font=?
                """,
                (ara_utc(), n, wins, losses, sum_r, pos, neg, dd, json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str), key, font),
            )
        else:
            self.exec(
                """
                INSERT OR REPLACE INTO memoria_edge_neta(key,font,updated_at,n,wins,losses,sum_r,sum_pos_r,sum_neg_r,max_dd_r,payload)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    key,
                    font,
                    ara_utc(),
                    1,
                    1 if resultat_r > 0 else 0,
                    1 if resultat_r < 0 else 0,
                    resultat_r,
                    max(0.0, resultat_r),
                    min(0.0, resultat_r),
                    min(0.0, resultat_r),
                    json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str),
                ),
            )

    def actualitza_memoria_legacy(self, keys: List[str], font: str, resultat_r: float, payload: Dict[str, Any]) -> None:
        """Manté compatible la memòria antiga per a resultats nous.

        Durant la reconstrucció històrica no toca edge_memory antic: molts FORWARD/LIVE
        ja hi eren i sumar-los de nou duplicaria evidència. La memòria neta és la font
        institucional; la legacy només rep outcomes nous del runtime.
        """
        if getattr(self, "_reconstruccio_historica", False):
            return
        try:
            from joanbot.intelligence.memory import EdgeMemory
            EdgeMemory().update_many(keys, font, resultat_r, payload)
        except Exception:
            pass

    def stats(self, key: str, font: str) -> Dict[str, float]:
        rows = self.q("SELECT * FROM memoria_edge_neta WHERE key=? AND font=?", (key, font))
        if not rows:
            return {"n": 0.0, "exp": 0.0, "pf": 0.0, "wr": 0.0, "dd": 0.0}
        r = rows[0]
        n = num(r.get("n"))
        wins = num(r.get("wins"))
        pos = num(r.get("sum_pos_r"))
        neg = num(r.get("sum_neg_r"))
        return {
            "n": n,
            "exp": num(r.get("sum_r")) / max(1.0, n),
            "pf": pos / abs(neg) if neg < 0 else (999.0 if pos > 0 else 0.0),
            "wr": wins / max(1.0, n),
            "dd": num(r.get("max_dd_r")),
        }

    def lcb(self, exp: float, n: float) -> float:
        return exp - 0.35 / math.sqrt(max(1.0, n))

    def classifica_estat(self, live: Dict[str, float], forward: Dict[str, float]) -> Tuple[str, float, float, List[str], List[str]]:
        vetos: List[str] = []
        motius: List[str] = []
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
            vetos.append("EDGE_LIVE_NEGATIU")
        if live["n"] >= 8 and live["dd"] <= -1.50:
            vetos.append("CUA_NEGATIVA_LIVE")
        if forward["n"] >= 500 and forward["exp"] < -0.03 and forward["pf"] < 0.90:
            vetos.append("EDGE_FORWARD_NEGATIU")
        if forward["n"] >= 3000 and forward["exp"] < -0.01 and forward["pf"] < 0.98 and live["n"] < 3:
            vetos.append("PRIOR_FORWARD_NEGATIU_SENSE_LIVE")
        if vetos:
            return "QUARANTENA", score, min(live_lcb, fwd_lcb), vetos, motius
        if live["n"] >= 30 and live_lcb > 0.03 and live["pf"] > 1.20:
            return "VALIDAT", score, live_lcb, vetos, motius
        if live["n"] >= 12 and live_lcb > 0.00 and live["pf"] > 1.08:
            return "CANARI", score, live_lcb, vetos, motius
        if forward["n"] >= 500 and fwd_lcb > 0.00 and forward["pf"] > 1.03:
            return "EXPLORAR", score, fwd_lcb, vetos, motius
        return "RECERCA", score, min(live_lcb, fwd_lcb), vetos, motius

    def mida_per_estat(self, estat: str) -> float:
        if estat == "VALIDAT":
            return env_num("MIDA_VALIDAT_USD", 30000)
        if estat == "CANARI":
            return env_num("MIDA_CANARI_USD", 15000)
        if estat == "EXPLORAR":
            return env_num("MIDA_EXPLORAR_USD", 8000)
        if estat == "RECERCA":
            return env_num("MIDA_RECERCA_USD", 4000)
        return 0.0

    def refresca_promocio(self, key: str, meta: Dict[str, Any]) -> None:
        live = self.stats(key, "LIVE")
        forward = self.stats(key, "FORWARD")
        estat, score, score_lcb, vetos, motius = self.classifica_estat(live, forward)
        payload = {"versio": VERSIO, "key": key, "meta": meta, "live": live, "forward": forward, "estat": estat, "score": score, "score_lcb": score_lcb, "vetos": vetos, "motius": motius}
        if key.startswith("CAUSA") or key.startswith("ATRIBUT"):
            self.exec(
                """
                INSERT OR REPLACE INTO estat_causal_quant(key,updated_at,estat,live_n,live_exp_r,live_pf,forward_n,forward_exp_r,forward_pf,payload)
                VALUES(?,?,?,?,?,?,?,?,?,?)
                """,
                (key, ara_utc(), estat, live["n"], live["exp"], live["pf"], forward["n"], forward["exp"], forward["pf"], json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)),
            )
        self.exec(
            """
            INSERT OR REPLACE INTO estat_promocio_quant(
                key,updated_at,symbol,side,setup,regime,session,volatility_bucket,
                live_n,live_exp_r,live_pf,live_winrate,live_dd_r,
                forward_n,forward_exp_r,forward_pf,forward_winrate,
                score_compost,score_lcb,estat,mida_recomanada_usd,vetos,motius,payload
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                key,
                ara_utc(),
                meta.get("symbol"),
                meta.get("side"),
                meta.get("setup"),
                meta.get("regime"),
                meta.get("session"),
                meta.get("volatility_bucket"),
                live["n"], live["exp"], live["pf"], live["wr"], live["dd"],
                forward["n"], forward["exp"], forward["pf"], forward["wr"],
                score, score_lcb, estat, self.mida_per_estat(estat),
                json.dumps(vetos, ensure_ascii=False), json.dumps(motius, ensure_ascii=False),
                json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str),
            ),
        )

    def registra_operacio_live(self, pos: Dict[str, Any], trade_row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        pos = dict(pos or {})
        trade = dict(trade_row or {})
        payload_trade = llegir_json(trade.get("payload"))
        trade.update(payload_trade)
        font_id = self.font_id("LIVE", trade)
        symbol = str(trade.get("symbol") or pos.get("symbol") or "").upper()
        side = str(trade.get("side") or pos.get("side") or "").upper()
        setup = str(trade.get("setup") or pos.get("setup") or "UNKNOWN").upper()
        decisio = self.decisio_de_posicio(pos)
        ctx = self.context_de_decisio(decisio, symbol)
        r = self.calcula_r_live(pos, trade)
        keys = self.claus(symbol, side, setup, ctx)
        obj = {"versio": VERSIO, "font": "LIVE", "trade": trade, "position": pos, "decision": decisio, "context": ctx, "keys": keys, **r}
        inserted = self.insereix_resultat({
            "font": "LIVE", "font_id": font_id, "position_id": trade.get("position_id"), "decision_id": decisio.get("id") or decisio.get("decision_id"),
            "symbol": symbol, "side": side, "setup": setup, "action": "CLOSE", "entry_ts": pos.get("opened_at"), "exit_ts": trade.get("ts"),
            "entry_price": num(trade.get("entry")), "exit_price": num(trade.get("exit")), "size_usd": num(trade.get("size_usd")), "pnl_usd": num(trade.get("pnl_usd")),
            "fees": num(trade.get("fees")), "risk_usd": r["risk_usd"], "resultat_r": r["resultat_r"], "mfe_r": num(pos.get("mfe_r")), "mae_r": num(pos.get("mae_r")),
            "regime": ctx.get("regime"), "session": ctx.get("session"), "volatility_bucket": ctx.get("volatility_bucket"), "news_bucket": ctx.get("news_bucket"), "context_key": keys[0],
            "motiu": trade.get("reason"), "payload_obj": obj,
        })
        try:
            self.exec("UPDATE trades SET pnl_r=? WHERE id=?", (r["resultat_r"], trade.get("id")))
        except Exception:
            pass
        if inserted and self.classifica_qualitat("LIVE", font_id, {"symbol": symbol, "side": side, "setup": setup, "resultat_r": r["resultat_r"], "risk_usd": r["risk_usd"], "payload_obj": obj})[0] == "NET":
            for k in keys:
                self.actualitza_memoria_neta(k, "LIVE", r["resultat_r"], obj)
                self.refresca_promocio(k, {"symbol": symbol, "side": side, "setup": setup, **ctx})
            self.actualitza_memoria_legacy(keys, "LIVE", r["resultat_r"], obj)
        self.audita("LIVE_REGISTRAT", {"symbol": symbol, "side": side, "setup": setup, "font_id": font_id, "resultat_r": r["resultat_r"], "risk_usd": r["risk_usd"], "inserted": inserted})
        return {"resultat_r": r["resultat_r"], "risk_usd": r["risk_usd"], "inserted": inserted, "keys": keys}

    def registra_forward(self, result_row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        r = dict(result_row or {})
        payload_result = llegir_json(r.get("payload"))
        r.update({k: v for k, v in payload_result.items() if v is not None})
        fc, fmeta = self.carrega_cas_forward(r.get("case_id"))
        decisio = fmeta.get("decision") or {}
        ctx = fmeta.get("context") or self.context_de_decisio(decisio, str(r.get("symbol") or fc.get("symbol") or ""))
        symbol = str(r.get("symbol") or fc.get("symbol") or decisio.get("symbol") or "").upper()
        side = str(r.get("side") or fc.get("side") or decisio.get("side") or "").upper()
        setup = str(r.get("setup") or fc.get("setup") or decisio.get("setup") or "UNKNOWN").upper()
        if not symbol or not side:
            return None
        if not r.get("id"):
            ids = self.q("SELECT id FROM forward_results WHERE case_id=? ORDER BY id DESC LIMIT 1", (r.get("case_id"),))
            if ids:
                r["id"] = ids[0].get("id")
        font_id = self.font_id("FORWARD", r)
        keys = self.claus(symbol, side, setup, ctx)
        resultat_r = num(r.get("result_r"))
        obj = {"versio": VERSIO, "font": "FORWARD", "result": r, "forward_case": fc, "decision": decisio, "context": ctx, "keys": keys, "resultat_r": resultat_r}
        inserted = self.insereix_resultat({
            "font": "FORWARD", "font_id": font_id, "position_id": None, "decision_id": decisio.get("id") or decisio.get("decision_id"),
            "symbol": symbol, "side": side, "setup": setup, "action": "FORWARD_RESULT", "entry_ts": fc.get("created_at"), "exit_ts": r.get("resolved_at"),
            "entry_price": num(fc.get("entry") or r.get("entry_price")), "exit_price": num(r.get("exit_price")), "size_usd": num(r.get("size_usd")),
            "pnl_usd": 0.0, "fees": 0.0, "risk_usd": 1.0, "resultat_r": resultat_r, "mfe_r": num(r.get("mfe_r")), "mae_r": num(r.get("mae_r")),
            "regime": ctx.get("regime"), "session": ctx.get("session"), "volatility_bucket": ctx.get("volatility_bucket"), "news_bucket": ctx.get("news_bucket"), "context_key": keys[0],
            "motiu": r.get("outcome"), "payload_obj": obj,
        })
        if inserted:
            for k in keys:
                self.actualitza_memoria_neta(k, "FORWARD", resultat_r, obj)
                self.refresca_promocio(k, {"symbol": symbol, "side": side, "setup": setup, **ctx})
            self.actualitza_memoria_legacy(keys, "FORWARD", resultat_r, obj)
        return {"resultat_r": resultat_r, "inserted": inserted, "keys": keys, "symbol": symbol, "side": side, "setup": setup}

    def reconstrueix_tot(self) -> Dict[str, int]:
        self.exec("DELETE FROM memoria_edge_neta")
        self.exec("DELETE FROM estat_promocio_quant")
        rows = self.q("""
            SELECT * FROM resultats_quant_nets r
            WHERE qualitat='NET'
              AND NOT EXISTS (SELECT 1 FROM exclusions_qualitat_dades x WHERE x.font=r.font AND x.font_id=r.font_id)
            ORDER BY id ASC
        """)
        touched: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            payload = llegir_json(row.get("payload"))
            keys = payload.get("keys") or self.claus(row.get("symbol"), row.get("side"), row.get("setup"), row)
            rr = num(row.get("resultat_r"))
            font = str(row.get("font") or "").upper()
            meta = {"symbol": row.get("symbol"), "side": row.get("side"), "setup": row.get("setup"), "regime": row.get("regime"), "session": row.get("session"), "volatility_bucket": row.get("volatility_bucket")}
            for k in keys:
                self.actualitza_memoria_neta(k, font, rr, payload)
                touched[k] = meta
        for key, meta in touched.items():
            self.refresca_promocio(key, meta)
        self.audita("RECONSTRUCCIO_NETA", {"resultats_nets": len(rows), "keys": len(touched)})
        return {"resultats_nets": len(rows), "keys": len(touched)}

    def backfill(self, limit_forward: int = 100000) -> Dict[str, Any]:
        live_seen = 0
        forward_seen = 0
        old_mode = getattr(self, "_reconstruccio_historica", False)
        self._reconstruccio_historica = True
        try:
            for row in self.q("SELECT t.*, p.payload AS position_payload FROM trades t LEFT JOIN positions p ON p.id=t.position_id ORDER BY t.id ASC"):
                try:
                    pos = llegir_json(row.get("position_payload"))
                    if self.registra_operacio_live(pos, row):
                        live_seen += 1
                except Exception as e:
                    self.audita("ERROR_BACKFILL_LIVE", {"error": repr(e), "row": row})
            for row in self.q(f"SELECT * FROM forward_results ORDER BY id ASC LIMIT {int(limit_forward)}"):
                try:
                    if self.registra_forward(row):
                        forward_seen += 1
                except Exception as e:
                    self.audita("ERROR_BACKFILL_FORWARD", {"error": repr(e), "row": row})
        finally:
            self._reconstruccio_historica = old_mode
        rebuilt = self.reconstrueix_tot()
        self.audita("BACKFILL_COMPLET", {"live_seen": live_seen, "forward_seen": forward_seen, "rebuilt": rebuilt, "legacy_no_duplicada": True})
        return {"live_seen": live_seen, "forward_seen": forward_seen, "rebuilt": rebuilt, "legacy_no_duplicada": True}

    def files_promocio(self, keys: List[str]) -> Dict[str, Dict[str, Any]]:
        out: Dict[str, Dict[str, Any]] = {}
        for k in keys:
            rows = self.q("SELECT * FROM estat_promocio_quant WHERE key=? LIMIT 1", (k,))
            if rows:
                out[k] = rows[0]
        return out

    def vista_decisio(self, symbol: str, side: str, setup: str, ctx: Dict[str, Any]) -> Dict[str, Any]:
        keys = self.claus(symbol, side, setup, ctx)
        rows = self.files_promocio(keys)
        bloqueig = []
        for k in keys:
            if k == "GLOBAL":
                continue
            r = rows.get(k)
            if not r:
                continue
            estat = str(r.get("estat") or "")
            vetos = llegir_json(r.get("vetos"), []) if isinstance(llegir_json(r.get("vetos"), []), list) else []
            try:
                vetos = json.loads(r.get("vetos") or "[]")
            except Exception:
                vetos = []
            if estat == "QUARANTENA" and any(v in vetos for v in ["EDGE_LIVE_NEGATIU", "EDGE_FORWARD_NEGATIU", "PRIOR_FORWARD_NEGATIU_SENSE_LIVE", "CUA_NEGATIVA_LIVE"]):
                bloqueig.append({"key": k, "vetos": vetos})
        if bloqueig:
            return {"estat": "QUARANTENA", "block": True, "key": bloqueig[0]["key"], "vetos": bloqueig[0]["vetos"], "size": 0.0, "keys": keys}
        chosen = None
        chosen_key = None
        for k in keys:
            if k in rows:
                chosen = rows[k]
                chosen_key = k
                break
        if chosen:
            return {"estat": chosen.get("estat") or "RECERCA", "block": False, "key": chosen_key, "size": num(chosen.get("mida_recomanada_usd")), "keys": keys, "row": chosen}
        return {"estat": "RECERCA", "block": False, "key": keys[0] if keys else None, "size": self.mida_per_estat("RECERCA"), "keys": keys, "row": None}

    def ajusta_edge_candidat(self, cand: Any, ctx: Dict[str, Any], edge: Dict[str, Any]) -> Dict[str, Any]:
        out = dict(edge or {})
        vista = self.vista_decisio(getattr(cand, "symbol", None), getattr(cand, "side", None), getattr(cand, "setup", None), ctx)
        out["nucli_quantitatiu_net"] = vista
        reasons = list(out.get("reasons") or [])
        mapa_causal = ctx.get("mapa_causal") or {}
        score_causal = self.causal.score_per_costat(mapa_causal, getattr(cand, "side", "")) if isinstance(mapa_causal, dict) else 0.0
        if abs(score_causal) >= 2.0:
            ajust = max(-8.0, min(8.0, score_causal * 0.65))
            out["score_adjustment"] = num(out.get("score_adjustment")) + ajust
            reasons.append(f"AJUST_CAUSAL_NET_{ajust:.1f}")
            out["mapa_causal"] = mapa_causal
        estat = vista.get("estat")
        if vista.get("block"):
            out["status"] = "NEGATIVE"
            out["score_adjustment"] = num(out.get("score_adjustment")) - 40.0
            out["size_multiplier"] = 0.0
            reasons.append(f"BLOC_QUANT_NET_{vista.get('key')}")
        elif estat == "VALIDAT":
            out["score_adjustment"] = num(out.get("score_adjustment")) + 12.0
            out["size_multiplier"] = max(num(out.get("size_multiplier"), 0.6), 1.0)
            reasons.append("EDGE_NET_VALIDAT")
        elif estat == "CANARI":
            out["score_adjustment"] = num(out.get("score_adjustment")) + 8.0
            out["size_multiplier"] = max(num(out.get("size_multiplier"), 0.6), 0.9)
            reasons.append("EDGE_NET_CANARI")
        elif estat == "EXPLORAR":
            out["score_adjustment"] = num(out.get("score_adjustment")) + 5.0
            out["size_multiplier"] = max(num(out.get("size_multiplier"), 0.6), 0.8)
            reasons.append("EDGE_NET_EXPLORAR")
        elif estat == "QUARANTENA":
            # Pot venir només de GLOBAL; no bloqueja una entrada específica, però tampoc premia ni escala.
            reasons.append("AVIS_QUANT_GLOBAL_NEGATIU_NO_ESCALA")
        else:
            reasons.append("EDGE_NET_RECERCA")
        out["reasons"] = reasons
        return out

    def aplica_politica_decisio(self, d: Any, wallet: Dict[str, Any]) -> Any:
        if not env_bool("ENTRENAMENT_PAPER_NET_ACTIU", True):
            return d
        ctx = d.feature_summary if hasattr(d, "feature_summary") else {}
        vista = self.vista_decisio(getattr(d, "symbol", None), getattr(d, "side", None), getattr(d, "setup", None), ctx)
        try:
            d.edge["nucli_quantitatiu_net"] = vista
        except Exception:
            pass
        if vista.get("block"):
            d.action = "WAIT"
            d.size_usd = 0.0
            try:
                d.risk["allowed"] = False
                d.risk["size_usd"] = 0.0
                d.risk["bloqueig_quant_net"] = vista
                d.reasons.append(f"EXECUCIO_BLOQUEJADA_PER_EDGE_NET_{vista.get('key')}")
            except Exception:
                pass
            return d
        estat = vista.get("estat", "RECERCA")
        score = num(getattr(d, "final_score", 0.0))
        action = str(getattr(d, "action", "")).upper()
        if estat == "QUARANTENA":
            # Quarantena no específica, normalment GLOBAL: no força WAIT, però no converteix PROBE/WAIT en OPEN ni amplia mida.
            try:
                d.reasons.append("AVIS_QUANT_GLOBAL_NEGATIU_NO_PROMOCIONA")
            except Exception:
                pass
            return d
        if action == "PROBE" and score >= env_num("SCORE_MIN_OBRIR_RECERCA", 45):
            d.action = "OPEN"
            try:
                d.reasons.append(f"PROBE_A_OPEN_PER_ENTRENAMENT_NET_{estat}")
            except Exception:
                pass
        elif action == "WAIT" and estat == "RECERCA" and env_bool("RECERCA_OBRE_ENTRENAMENT_PAPER", True) and score >= env_num("SCORE_MIN_OBRIR_RECERCA", 42) and bool(getattr(d, "risk", {}).get("allowed", True)):
            d.action = "OPEN"
            try:
                d.reasons.append("WAIT_A_OPEN_PER_MOSTREIG_ACTIU_RECERCA")
            except Exception:
                pass
        elif action == "WAIT" and estat in {"EXPLORAR", "CANARI", "VALIDAT"} and score >= env_num("SCORE_MIN_OBRIR_EDGE_NET", 52):
            d.action = "OPEN"
            try:
                d.reasons.append(f"WAIT_A_OPEN_PER_EDGE_NET_{estat}")
            except Exception:
                pass
        if str(getattr(d, "action", "")).upper() == "OPEN":
            target = max(num(getattr(d, "size_usd", 0.0)), num(vista.get("size")), self.mida_per_estat("RECERCA"))
            target = min(target, env_num("MIDA_MAXIMA_POSICIO_USD", 50000))
            d.size_usd = target
            try:
                d.risk["size_usd"] = target
                d.risk["estat_quant_net"] = estat
                d.risk["mida_quant_neta"] = target
                d.reasons.append(f"MIDA_QUANT_NETA_{estat}_{target:.0f}")
            except Exception:
                pass
        return d

    def report(self) -> str:
        lines: List[str] = []
        lines.append("===== INFORME NUCLI QUANTITATIU NET =====")
        lines.append(f"UTC: {ara_utc()}")
        counts = {}
        for t in ["resultats_quant_nets", "exclusions_qualitat_dades", "memoria_edge_neta", "estat_promocio_quant", "estat_causal_quant", "auditoria_quant_neta", "trades", "positions", "forward_results"]:
            try:
                counts[t] = self.q(f"SELECT COUNT(*) c FROM {t}")[0]["c"]
            except Exception:
                counts[t] = None
        lines.append("COMPTADORS: " + json.dumps(counts, sort_keys=True))
        lines.append("")
        lines.append("QUALITAT:")
        for r in self.q("SELECT qualitat,font,COUNT(*) n FROM resultats_quant_nets GROUP BY qualitat,font ORDER BY qualitat,font"):
            lines.append(f"{r.get('qualitat')} {r.get('font')} n={r.get('n')}")
        lines.append("")
        lines.append("EXCLUSIONS:")
        for r in self.q("SELECT motiu,severitat,COUNT(*) n FROM exclusions_qualitat_dades GROUP BY motiu,severitat ORDER BY n DESC"):
            lines.append(f"{r.get('motiu')} severitat={r.get('severitat')} n={r.get('n')}")
        lines.append("")
        lines.append("ESTATS:")
        for r in self.q("SELECT estat,COUNT(*) n,ROUND(AVG(live_exp_r),5) live_exp,ROUND(AVG(forward_exp_r),5) forward_exp,ROUND(AVG(mida_recomanada_usd),2) mida FROM estat_promocio_quant GROUP BY estat ORDER BY n DESC"):
            lines.append(f"{r.get('estat')} n={r.get('n')} liveExp={r.get('live_exp')} forwardExp={r.get('forward_exp')} mida={r.get('mida')}")
        lines.append("")
        lines.append("TOP CAUSAL:")
        for r in self.q("""
        SELECT key,estat,live_n,ROUND(live_exp_r,4) live_exp,ROUND(live_pf,3) live_pf,
               forward_n,ROUND(forward_exp_r,4) fwd_exp,ROUND(forward_pf,3) fwd_pf
        FROM estat_causal_quant
        WHERE estat!='QUARANTENA'
        ORDER BY estat='VALIDAT' DESC, estat='CANARI' DESC, estat='EXPLORAR' DESC, live_n DESC, forward_n DESC
        LIMIT 20
        """):
            lines.append(f"{r.get('estat')} liveN={r.get('live_n')} liveExp={r.get('live_exp')} livePF={r.get('live_pf')} fwdN={r.get('forward_n')} fwdExp={r.get('fwd_exp')} fwdPF={r.get('fwd_pf')} key={r.get('key')}")
        lines.append("")
        lines.append("TOP EXECUTABLE:")
        rows = self.q("""
        SELECT key,estat,ROUND(score_compost,2) score,ROUND(score_lcb,4) lcb,live_n,ROUND(live_exp_r,4) live_exp,ROUND(live_pf,3) live_pf,
               forward_n,ROUND(forward_exp_r,4) fwd_exp,ROUND(forward_pf,3) fwd_pf,ROUND(mida_recomanada_usd,2) size
        FROM estat_promocio_quant
        WHERE estat!='QUARANTENA'
        ORDER BY estat='VALIDAT' DESC, estat='CANARI' DESC, estat='EXPLORAR' DESC, score_compost DESC
        LIMIT 25
        """)
        for r in rows:
            lines.append(f"{r.get('estat')} size={r.get('size')} score={r.get('score')} lcb={r.get('lcb')} liveN={r.get('live_n')} liveExp={r.get('live_exp')} livePF={r.get('live_pf')} fwdN={r.get('forward_n')} fwdExp={r.get('fwd_exp')} fwdPF={r.get('fwd_pf')} key={r.get('key')}")
        lines.append("")
        lines.append("QUARANTENA:")
        for r in self.q("SELECT key,live_n,ROUND(live_exp_r,4) live_exp,ROUND(live_pf,3) live_pf,forward_n,ROUND(forward_exp_r,4) fwd_exp,ROUND(forward_pf,3) fwd_pf,vetos FROM estat_promocio_quant WHERE estat='QUARANTENA' ORDER BY live_exp_r ASC, forward_exp_r ASC LIMIT 25"):
            lines.append(f"Q liveN={r.get('live_n')} liveExp={r.get('live_exp')} livePF={r.get('live_pf')} fwdN={r.get('forward_n')} fwdExp={r.get('fwd_exp')} fwdPF={r.get('fwd_pf')} vetos={r.get('vetos')} key={r.get('key')}")
        return "\n".join(lines)


def get_core(db: Any) -> NucliQuantitatiuNet:
    return NucliQuantitatiuNet(db)
