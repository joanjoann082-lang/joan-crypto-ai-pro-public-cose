
from __future__ import annotations

import json, math, datetime
from typing import Any, Dict, Iterable, List, Tuple

VERSIO = "GESTIO_POSICIO_INSTITUCIONAL_NETA_V2"


def ara_utc() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


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


def js(x: Any, default: Dict[str, Any] | None = None) -> Dict[str, Any]:
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


def clamp(x: float, a: float, b: float) -> float:
    return max(a, min(b, x))


def jdump(x: Any) -> str:
    return json.dumps(x, ensure_ascii=False, sort_keys=True, default=str)


class GestioPosicioInstitucionalNeta:
    """Gestió institucional de posició per paper trading.

    Principis:
    - una única autoritat de gestió: hard stop/TP2 són protecció estructural; la resta passa per aquest motor;
    - aprèn la trajectòria interna del trade, no només el resultat final;
    - separa entrada bona de sortida dolenta amb MFE/MAE/R capturat;
    - política per jerarquia: causa > setup > símbol/costat > costat;
    - no sobreajusta: cada política es regularitza per mostra i confiança;
    - accions idempotents: no repeteix parcials ni empitjora stops;
    - close_pct sempre és fracció de la posició actual, no de la mida original.
    """

    def __init__(self, db: Any):
        self.db = db
        self.assegura_esquema()

    def q(self, sql: str, params: Iterable[Any] = ()) -> List[Dict[str, Any]]:
        try:
            return [dict(r) for r in self.db.query(sql, tuple(params))]
        except Exception:
            return []

    def exec(self, sql: str, params: Iterable[Any] = ()) -> Any:
        return self.db.execute(sql, tuple(params))

    def existeix_columna(self, table: str, col: str) -> bool:
        try:
            return any(dict(r).get("name") == col for r in self.db.query(f"PRAGMA table_info({table})"))
        except Exception:
            return False

    def afegeix_columna(self, table: str, col: str, decl: str) -> None:
        if not self.existeix_columna(table, col):
            try:
                self.exec(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
            except Exception:
                pass

    def assegura_esquema(self) -> None:
        self.exec("""
        CREATE TABLE IF NOT EXISTS mostres_posicio_neta(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            position_id TEXT NOT NULL,
            symbol TEXT,
            side TEXT,
            setup TEXT,
            price REAL,
            r_actual REAL,
            mfe_r REAL,
            mae_r REAL,
            retorn_des_de_mfe REAL,
            remaining_pct REAL,
            stop_loss REAL,
            risk_abs REAL,
            edat_mostres INTEGER,
            claus TEXT NOT NULL,
            payload TEXT NOT NULL
        )
        """)
        self.exec("""
        CREATE TABLE IF NOT EXISTS tancaments_posicio_neta(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            position_id TEXT NOT NULL,
            trade_id TEXT,
            symbol TEXT,
            side TEXT,
            setup TEXT,
            reason TEXT,
            close_pct REAL,
            resultat_r REAL,
            mfe_r REAL,
            mae_r REAL,
            captura_mfe REAL,
            retorn_des_de_mfe REAL,
            eficiencia_sortida REAL,
            etiqueta_sortida TEXT NOT NULL,
            claus TEXT NOT NULL,
            payload TEXT NOT NULL,
            UNIQUE(position_id, trade_id, reason, close_pct)
        )
        """)
        self.exec("""
        CREATE TABLE IF NOT EXISTS politica_gestio_posicio_neta(
            key TEXT PRIMARY KEY,
            updated_at TEXT NOT NULL,
            n_tancaments REAL NOT NULL,
            n_mostres REAL NOT NULL,
            avg_resultat_r REAL NOT NULL,
            avg_mfe_r REAL NOT NULL,
            avg_mae_r REAL NOT NULL,
            avg_captura_mfe REAL NOT NULL,
            avg_retorn_des_de_mfe REAL NOT NULL,
            pf REAL NOT NULL,
            sortida_dolenta_n REAL NOT NULL,
            cua_negativa_n REAL NOT NULL,
            proposta TEXT NOT NULL,
            partial_after_r REAL NOT NULL,
            partial_pct REAL NOT NULL,
            lock_after_r REAL NOT NULL,
            lock_r REAL NOT NULL,
            trail_after_r REAL NOT NULL,
            giveback_frac REAL NOT NULL,
            cut_if_no_mfe_r REAL NOT NULL,
            payload TEXT NOT NULL
        )
        """)
        for col, decl in [
            ("partial2_after_r", "REAL DEFAULT 1.60"),
            ("partial2_pct", "REAL DEFAULT 0.25"),
            ("time_stop_mostres", "REAL DEFAULT 8"),
            ("min_mfe_for_time_ok", "REAL DEFAULT 0.25"),
            ("confidence", "REAL DEFAULT 0"),
            ("max_loss_after_mfe_r", "REAL DEFAULT -0.20"),
        ]:
            self.afegeix_columna("politica_gestio_posicio_neta", col, decl)

        self.exec("""
        CREATE TABLE IF NOT EXISTS plans_gestio_posicio_neta(
            position_id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            symbol TEXT,
            side TEXT,
            setup TEXT,
            entry_price REAL,
            stop_loss REAL,
            risk_abs REAL,
            policy_key TEXT,
            estat TEXT NOT NULL,
            plan TEXT NOT NULL,
            payload TEXT NOT NULL
        )
        """)
        self.exec("""
        CREATE TABLE IF NOT EXISTS simulacions_sortida_neta(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            position_id TEXT NOT NULL,
            trade_id TEXT,
            model TEXT NOT NULL,
            resultat_simulat_r REAL NOT NULL,
            resultat_real_r REAL NOT NULL,
            delta_r REAL NOT NULL,
            payload TEXT NOT NULL,
            UNIQUE(position_id, trade_id, model)
        )
        """)
        self.exec("""
        CREATE TABLE IF NOT EXISTS decisions_gestio_posicio_neta(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            position_id TEXT,
            symbol TEXT,
            side TEXT,
            setup TEXT,
            action TEXT NOT NULL,
            reason TEXT NOT NULL,
            r_actual REAL,
            mfe_r REAL,
            payload TEXT NOT NULL
        )
        """)
        self.exec("""
        CREATE TABLE IF NOT EXISTS auditoria_gestio_posicio_neta(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            event TEXT NOT NULL,
            payload TEXT NOT NULL
        )
        """)

    def audit(self, event: str, payload: Dict[str, Any]) -> None:
        try:
            self.exec("INSERT INTO auditoria_gestio_posicio_neta(ts,event,payload) VALUES(?,?,?)", (ara_utc(), event, jdump(payload)))
        except Exception:
            pass

    def decisio_de_posicio(self, pos: Dict[str, Any]) -> Dict[str, Any]:
        meta = pos.get("meta") or {}
        d = meta.get("decision") or {}
        return d if isinstance(d, dict) else {}

    def mapa_causal_de_posicio(self, pos: Dict[str, Any]) -> Dict[str, Any]:
        d = self.decisio_de_posicio(pos)
        fs = d.get("feature_summary") or {}
        if isinstance(fs, dict) and isinstance(fs.get("mapa_causal"), dict):
            return fs.get("mapa_causal") or {}
        if isinstance(pos.get("mapa_causal"), dict):
            return pos.get("mapa_causal") or {}
        return {}

    def regime_de_posicio(self, pos: Dict[str, Any]) -> str:
        d = self.decisio_de_posicio(pos)
        fs = d.get("feature_summary") or {}
        if isinstance(fs, dict):
            return str(fs.get("regime") or fs.get("market_regime") or "UNKNOWN").upper()
        return "UNKNOWN"

    def claus_de_posicio(self, pos: Dict[str, Any], trade: Dict[str, Any] | None = None) -> List[str]:
        trade = trade or {}
        symbol = str(trade.get("symbol") or pos.get("symbol") or "UNKNOWN").upper()
        side = str(trade.get("side") or pos.get("side") or "UNKNOWN").upper()
        setup = str(trade.get("setup") or pos.get("setup") or "UNKNOWN").upper()
        regime = self.regime_de_posicio(pos)
        mapa = self.mapa_causal_de_posicio(pos)
        keys = [
            f"GESTIO|{symbol}|{side}|{setup}|{regime}",
            f"GESTIO|{symbol}|{side}|{setup}",
            f"GESTIO|{symbol}|{side}",
            f"GESTIO|{side}|{setup}",
            f"GESTIO|{side}|{regime}",
            f"GESTIO|{side}",
        ]
        camps = [
            "estructura_4h", "estructura_1h", "fractal", "zona", "nivell", "vwap",
            "poc", "cvd", "flux", "liquidacions", "sweep", "funding", "oi", "fase_ona",
        ]
        for camp in camps:
            v = str(mapa.get(camp) or "").upper()
            if v and v not in {"NONE", "UNKNOWN", "0", "NULL"}:
                keys.append(f"GESTIO_CAUSA|{side}|{setup}|{camp.upper()}|{v}")
        zona = str(mapa.get("zona") or mapa.get("nivell") or "UNKNOWN").upper()
        cvd = str(mapa.get("cvd") or "UNKNOWN").upper()
        liq = str(mapa.get("liquidacions") or "UNKNOWN").upper()
        fractal = str(mapa.get("fractal") or "UNKNOWN").upper()
        if zona != "UNKNOWN" and cvd != "UNKNOWN":
            keys.append(f"GESTIO_CONFLUENCIA|{side}|{setup}|{zona}|{cvd}")
        if liq != "UNKNOWN" and fractal != "UNKNOWN":
            keys.append(f"GESTIO_CONFLUENCIA|{side}|{setup}|{liq}|{fractal}")
        return list(dict.fromkeys(keys))

    def _risk_abs(self, pos: Dict[str, Any]) -> float:
        entry = num(pos.get("entry_price") or pos.get("entry"))
        sl = num(pos.get("stop_loss"))
        return abs(entry - sl) if entry > 0 and sl > 0 else 0.0

    def politica_base(self) -> Dict[str, Any]:
        return {
            "key": "BASE",
            "proposta": "BASE_INSTITUCIONAL_REGULARITZADA",
            "partial_after_r": 0.80,
            "partial_pct": 0.35,
            "partial2_after_r": 1.60,
            "partial2_pct": 0.25,
            "lock_after_r": 0.60,
            "lock_r": 0.10,
            "trail_after_r": 1.10,
            "giveback_frac": 0.48,
            "cut_if_no_mfe_r": -0.85,
            "time_stop_mostres": 8.0,
            "min_mfe_for_time_ok": 0.25,
            "max_loss_after_mfe_r": -0.20,
            "confidence": 0.0,
            "n_tancaments": 0.0,
            "n_mostres": 0.0,
        }

    def _barreja(self, base: Dict[str, Any], target: Dict[str, Any], c: float) -> Dict[str, Any]:
        out = dict(base)
        for k, v in target.items():
            if isinstance(v, (int, float)) and k in out:
                out[k] = num(out[k]) * (1.0 - c) + num(v) * c
            else:
                out[k] = v
        return out

    def crea_pla_inicial(self, pos: Dict[str, Any]) -> Dict[str, Any]:
        pos = dict(pos or {})
        pid = str(pos.get("id") or "")
        if not pid:
            return {"created": False, "reason": "SENSE_POSITION_ID"}
        risk_abs = self._risk_abs(pos)
        pol = self.politica_per_posicio(pos)
        plan = {
            "versio": VERSIO,
            "position_id": pid,
            "policy_key": pol.get("key"),
            "proposta": pol.get("proposta"),
            "risk_abs": risk_abs,
            "stages": {
                "partial_1": {"after_r": pol.get("partial_after_r"), "pct_current": pol.get("partial_pct")},
                "partial_2": {"after_r": pol.get("partial2_after_r"), "pct_current": pol.get("partial2_pct")},
                "lock": {"after_r": pol.get("lock_after_r"), "lock_r": pol.get("lock_r")},
                "trail": {"after_r": pol.get("trail_after_r"), "giveback_frac": pol.get("giveback_frac")},
                "time_stop": {"mostres": pol.get("time_stop_mostres"), "min_mfe": pol.get("min_mfe_for_time_ok")},
            },
            "principi_close_pct": "fraccio_sobre_posicio_actual",
        }
        payload = {"position": pos, "politica": pol, "plan": plan, "mapa_causal": self.mapa_causal_de_posicio(pos)}
        self.exec("""
        INSERT OR REPLACE INTO plans_gestio_posicio_neta(
            position_id,created_at,updated_at,symbol,side,setup,entry_price,stop_loss,risk_abs,policy_key,estat,plan,payload
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (pid, ara_utc(), ara_utc(), pos.get("symbol"), pos.get("side"), pos.get("setup"), num(pos.get("entry_price")), num(pos.get("stop_loss")), risk_abs, pol.get("key"), "ACTIU", jdump(plan), jdump(payload)))
        return {"created": True, "plan": plan, "politica": pol}

    def registra_mostra_oberta(self, pos: Dict[str, Any], price: float, r_actual: float, risk_abs: float) -> Dict[str, Any]:
        pos = dict(pos or {})
        pid = str(pos.get("id") or "")
        if not pid:
            return {"inserted": False, "reason": "SENSE_POSITION_ID"}
        mfe = max(num(pos.get("mfe_r")), r_actual)
        mae = min(num(pos.get("mae_r")), r_actual)
        retorn = max(0.0, mfe - r_actual)
        edat = int(num(pos.get("gestio_mostres_n"), 0)) + 1
        claus = self.claus_de_posicio(pos)
        payload = {
            "versio": VERSIO,
            "position_id": pid,
            "price": price,
            "r_actual": r_actual,
            "mfe_r": mfe,
            "mae_r": mae,
            "retorn_des_de_mfe": retorn,
            "edat_mostres": edat,
            "claus": claus,
            "mapa_causal": self.mapa_causal_de_posicio(pos),
        }
        self.exec("""
        INSERT INTO mostres_posicio_neta(
            ts,position_id,symbol,side,setup,price,r_actual,mfe_r,mae_r,retorn_des_de_mfe,
            remaining_pct,stop_loss,risk_abs,edat_mostres,claus,payload
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (ara_utc(), pid, pos.get("symbol"), pos.get("side"), pos.get("setup"), price, r_actual, mfe, mae, retorn, num(pos.get("remaining_pct"), 1.0), num(pos.get("stop_loss")), risk_abs, edat, jdump(claus), jdump(payload)))
        for k in claus[:8]:
            self.recalcula_politica(k)
        return {"inserted": True, "mfe_r": mfe, "mae_r": mae, "edat_mostres": edat, "claus": claus}

    def mfe_mae_des_de_mostres(self, position_id: str) -> Tuple[float, float, List[float]]:
        rows = self.q("SELECT r_actual FROM mostres_posicio_neta WHERE position_id=? ORDER BY id ASC", (position_id,))
        seq = [num(r.get("r_actual")) for r in rows]
        if not seq:
            return 0.0, 0.0, []
        return max(seq), min(seq), seq

    def mesura_tancament(self, pos: Dict[str, Any], trade: Dict[str, Any]) -> Dict[str, Any]:
        pid = str(trade.get("position_id") or pos.get("id") or "")
        resultat_r = num(trade.get("pnl_r") or trade.get("resultat_r"))
        mfe_seq, mae_seq, seq = self.mfe_mae_des_de_mostres(pid)
        mfe_r = max(num(pos.get("mfe_r")), resultat_r, mfe_seq, 0.0)
        mae_r = min(num(pos.get("mae_r")), resultat_r, mae_seq, 0.0)
        captura = resultat_r / mfe_r if mfe_r > 0 else 0.0
        retorn = max(0.0, mfe_r - resultat_r)
        eficiencia = captura - max(0.0, abs(mae_r) - 1.0) * 0.25 - max(0.0, retorn - 0.60) * 0.15
        if resultat_r < -1.15:
            etiqueta = "CUA_NEGATIVA_EXCESSIVA"
        elif mfe_r >= 0.8 and captura < 0.30:
            etiqueta = "GUANY_RETORNAT"
        elif resultat_r <= 0 and mfe_r >= 0.45:
            etiqueta = "ENTRADA_BONA_SORTIDA_DOLENTA"
        elif resultat_r > 0 and captura >= 0.60:
            etiqueta = "SORTIDA_EFICIENT"
        elif resultat_r > 0:
            etiqueta = "POSITIVA_MILLORABLE"
        else:
            etiqueta = "STOP_NORMAL_O_ENTRADA_DOLENTA"
        return {
            "resultat_r": resultat_r,
            "mfe_r": mfe_r,
            "mae_r": mae_r,
            "captura_mfe": captura,
            "retorn_des_de_mfe": retorn,
            "eficiencia_sortida": eficiencia,
            "etiqueta_sortida": etiqueta,
            "mostres_seq_n": len(seq),
        }

    def simula_sortides(self, position_id: str, trade_id: str, real_r: float) -> List[Dict[str, Any]]:
        _, _, seq = self.mfe_mae_des_de_mostres(position_id)
        if not seq:
            return []
        models = {
            "LOCK_0R_DESPRES_0_50R": {"lock_after": 0.50, "floor": 0.0},
            "LOCK_0_20R_DESPRES_0_80R": {"lock_after": 0.80, "floor": 0.20},
            "TRAIL_50_DESPRES_1R": {"trail_after": 1.0, "keep_frac": 0.50},
            "RETALL_SENSE_MFE_MENYS_0_65R": {"cut_no_mfe": -0.65, "min_mfe": 0.25},
        }
        out = []
        for name, cfg in models.items():
            mfe = -999.0
            armed_floor = None
            closed = None
            for r in seq:
                mfe = max(mfe, r)
                if "lock_after" in cfg and mfe >= cfg["lock_after"]:
                    armed_floor = cfg["floor"]
                if armed_floor is not None and r <= armed_floor:
                    closed = armed_floor
                    break
                if "trail_after" in cfg and mfe >= cfg["trail_after"] and r <= mfe * cfg["keep_frac"]:
                    closed = r
                    break
                if "cut_no_mfe" in cfg and r <= cfg["cut_no_mfe"] and mfe < cfg["min_mfe"]:
                    closed = r
                    break
            sim_r = real_r if closed is None else closed
            rec = {"model": name, "resultat_simulat_r": sim_r, "resultat_real_r": real_r, "delta_r": sim_r - real_r}
            self.exec("""
            INSERT OR IGNORE INTO simulacions_sortida_neta(ts,position_id,trade_id,model,resultat_simulat_r,resultat_real_r,delta_r,payload)
            VALUES(?,?,?,?,?,?,?,?)
            """, (ara_utc(), position_id, str(trade_id), name, sim_r, real_r, sim_r - real_r, jdump(rec)))
            out.append(rec)
        return out

    def registra_tancament(self, pos: Dict[str, Any], trade: Dict[str, Any]) -> Dict[str, Any]:
        pos = dict(pos or {})
        trade = dict(trade or {})
        pid = str(trade.get("position_id") or pos.get("id") or "")
        if not pid:
            return {"inserted": False, "reason": "SENSE_POSITION_ID"}
        trade_id = str(trade.get("id") or trade.get("ts") or ara_utc())
        m = self.mesura_tancament(pos, trade)
        claus = self.claus_de_posicio(pos, trade)
        sims = self.simula_sortides(pid, trade_id, m["resultat_r"])
        payload = {"versio": VERSIO, "position": pos, "trade": trade, "mesura": m, "claus": claus, "simulacions": sims}
        self.exec("""
        INSERT OR IGNORE INTO tancaments_posicio_neta(
            ts,position_id,trade_id,symbol,side,setup,reason,close_pct,resultat_r,mfe_r,mae_r,
            captura_mfe,retorn_des_de_mfe,eficiencia_sortida,etiqueta_sortida,claus,payload
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (ara_utc(), pid, trade_id, trade.get("symbol") or pos.get("symbol"), trade.get("side") or pos.get("side"), trade.get("setup") or pos.get("setup"), trade.get("reason"), num(trade.get("close_pct"), 1.0), m["resultat_r"], m["mfe_r"], m["mae_r"], m["captura_mfe"], m["retorn_des_de_mfe"], m["eficiencia_sortida"], m["etiqueta_sortida"], jdump(claus), jdump(payload)))
        for k in claus:
            self.recalcula_politica(k)
        self.exec("UPDATE plans_gestio_posicio_neta SET estat=?, updated_at=? WHERE position_id=?", ("TANCAT", ara_utc(), pid))
        self.audit("TANCAMENT_REGISTRAT", {"position_id": pid, "trade_id": trade_id, **m})
        return {"inserted": True, "mesura": m, "claus": claus, "simulacions": sims}

    def recalcula_politica(self, key: str) -> Dict[str, Any]:
        tanc = self.q("SELECT * FROM tancaments_posicio_neta WHERE claus LIKE ?", (f'%"{key}"%',))
        mostres = self.q("SELECT * FROM mostres_posicio_neta WHERE claus LIKE ?", (f'%"{key}"%',))
        if not tanc and not mostres:
            return {}
        n = float(len(tanc)); nm = float(len(mostres))
        pos_sum = sum(max(0.0, num(r.get("resultat_r"))) for r in tanc)
        neg_sum = abs(sum(min(0.0, num(r.get("resultat_r"))) for r in tanc))
        pf = pos_sum / neg_sum if neg_sum > 0 else (999.0 if pos_sum > 0 else 0.0)
        avg_r = sum(num(r.get("resultat_r")) for r in tanc) / max(1.0, n)
        avg_mfe = sum(num(r.get("mfe_r")) for r in tanc) / max(1.0, n)
        avg_mae = sum(num(r.get("mae_r")) for r in tanc) / max(1.0, n)
        avg_cap = sum(num(r.get("captura_mfe")) for r in tanc) / max(1.0, n)
        avg_ret = sum(num(r.get("retorn_des_de_mfe")) for r in tanc) / max(1.0, n)
        dolentes = sum(1 for r in tanc if r.get("etiqueta_sortida") in {"GUANY_RETORNAT", "ENTRADA_BONA_SORTIDA_DOLENTA"})
        cues = sum(1 for r in tanc if r.get("etiqueta_sortida") == "CUA_NEGATIVA_EXCESSIVA")
        confidence = clamp((n / 12.0) + (nm / 240.0), 0.0, 1.0)

        base = self.politica_base()
        target = dict(base)
        proposta = "BASE_INSTITUCIONAL_REGULARITZADA"
        if n >= 3 and (dolentes / max(1.0, n) >= 0.33 or avg_ret >= 0.70):
            proposta = "CAPTURA_AVANCADA_GUANYS"
            target.update({"partial_after_r": 0.55, "partial_pct": 0.50, "partial2_after_r": 1.20, "partial2_pct": 0.30, "lock_after_r": 0.40, "lock_r": 0.06, "trail_after_r": 0.85, "giveback_frac": 0.62})
        if n >= 3 and (cues / max(1.0, n) >= 0.25 or avg_r < -0.25 or pf < 0.80):
            proposta = "RETALL_CUA_NEGATIVA"
            target.update({"partial_after_r": 0.50, "partial_pct": 0.55, "partial2_after_r": 1.10, "partial2_pct": 0.25, "lock_after_r": 0.35, "lock_r": 0.04, "trail_after_r": 0.75, "giveback_frac": 0.70, "cut_if_no_mfe_r": -0.65, "time_stop_mostres": 6.0, "max_loss_after_mfe_r": -0.05})
        if n >= 5 and avg_cap >= 0.58 and avg_r > 0.05 and pf > 1.15:
            proposta = "DEIXAR_CORRER_GUANYADOR"
            target.update({"partial_after_r": 1.05, "partial_pct": 0.30, "partial2_after_r": 2.00, "partial2_pct": 0.20, "lock_after_r": 0.75, "lock_r": 0.18, "trail_after_r": 1.45, "giveback_frac": 0.38, "cut_if_no_mfe_r": -0.90})
        pol = self._barreja(base, target, confidence)
        pol["proposta"] = proposta
        pol["confidence"] = confidence

        payload = {
            "versio": VERSIO,
            "key": key,
            "n_tancaments": n,
            "n_mostres": nm,
            "avg_resultat_r": avg_r,
            "avg_mfe_r": avg_mfe,
            "avg_mae_r": avg_mae,
            "avg_captura_mfe": avg_cap,
            "avg_retorn_des_de_mfe": avg_ret,
            "pf": pf,
            "sortida_dolenta_n": dolentes,
            "cua_negativa_n": cues,
            "proposta": proposta,
            "confidence": confidence,
            "politica": pol,
        }
        self.exec("""
        INSERT OR REPLACE INTO politica_gestio_posicio_neta(
            key,updated_at,n_tancaments,n_mostres,avg_resultat_r,avg_mfe_r,avg_mae_r,avg_captura_mfe,
            avg_retorn_des_de_mfe,pf,sortida_dolenta_n,cua_negativa_n,proposta,partial_after_r,partial_pct,
            lock_after_r,lock_r,trail_after_r,giveback_frac,cut_if_no_mfe_r,payload,partial2_after_r,partial2_pct,
            time_stop_mostres,min_mfe_for_time_ok,confidence,max_loss_after_mfe_r
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (key, ara_utc(), n, nm, avg_r, avg_mfe, avg_mae, avg_cap, avg_ret, pf, dolentes, cues, proposta, pol["partial_after_r"], pol["partial_pct"], pol["lock_after_r"], pol["lock_r"], pol["trail_after_r"], pol["giveback_frac"], pol["cut_if_no_mfe_r"], jdump(payload), pol["partial2_after_r"], pol["partial2_pct"], pol["time_stop_mostres"], pol["min_mfe_for_time_ok"], confidence, pol["max_loss_after_mfe_r"]))
        return payload

    def politica_per_posicio(self, pos: Dict[str, Any]) -> Dict[str, Any]:
        base = self.politica_base()
        for k in self.claus_de_posicio(pos):
            rows = self.q("SELECT * FROM politica_gestio_posicio_neta WHERE key=? AND (n_tancaments>=3 OR n_mostres>=20) LIMIT 1", (k,))
            if rows:
                r = rows[0]
                out = dict(base)
                out.update({
                    "key": k,
                    "proposta": r.get("proposta"),
                    "partial_after_r": num(r.get("partial_after_r"), base["partial_after_r"]),
                    "partial_pct": num(r.get("partial_pct"), base["partial_pct"]),
                    "partial2_after_r": num(r.get("partial2_after_r"), base["partial2_after_r"]),
                    "partial2_pct": num(r.get("partial2_pct"), base["partial2_pct"]),
                    "lock_after_r": num(r.get("lock_after_r"), base["lock_after_r"]),
                    "lock_r": num(r.get("lock_r"), base["lock_r"]),
                    "trail_after_r": num(r.get("trail_after_r"), base["trail_after_r"]),
                    "giveback_frac": num(r.get("giveback_frac"), base["giveback_frac"]),
                    "cut_if_no_mfe_r": num(r.get("cut_if_no_mfe_r"), base["cut_if_no_mfe_r"]),
                    "time_stop_mostres": num(r.get("time_stop_mostres"), base["time_stop_mostres"]),
                    "min_mfe_for_time_ok": num(r.get("min_mfe_for_time_ok"), base["min_mfe_for_time_ok"]),
                    "max_loss_after_mfe_r": num(r.get("max_loss_after_mfe_r"), base["max_loss_after_mfe_r"]),
                    "confidence": num(r.get("confidence")),
                    "n_tancaments": num(r.get("n_tancaments")),
                    "n_mostres": num(r.get("n_mostres")),
                })
                return out
        return base

    def _accions(self, pos: Dict[str, Any]) -> Dict[str, Any]:
        a = pos.get("gestio_accions")
        return a if isinstance(a, dict) else {}

    def decideix_accio(self, pos: Dict[str, Any], price: float, r_actual: float, risk_abs: float) -> Dict[str, Any]:
        pos = dict(pos or {})
        pid = str(pos.get("id") or "")
        mostra = self.registra_mostra_oberta(pos, price, r_actual, risk_abs)
        pol = self.politica_per_posicio(pos)
        mfe = max(num(pos.get("mfe_r")), r_actual)
        mae = min(num(pos.get("mae_r")), r_actual)
        retorn = max(0.0, mfe - r_actual)
        edat = int(num(mostra.get("edat_mostres"), num(pos.get("gestio_mostres_n"), 0)))
        accions = self._accions(pos)
        action = "MANTENIR"; reason = "SENSE_ACCIO"; close_pct = 0.0; nou_stop_r = None; marca = None

        if r_actual <= num(pol.get("cut_if_no_mfe_r"), -0.85) and mfe < 0.25 and edat >= 3:
            action = "TANCAR_TOTAL"; reason = "RETALL_CUA_SENSE_MFE"; close_pct = 1.0; marca = "tancament_cua"
        elif edat >= num(pol.get("time_stop_mostres"), 8.0) and mfe < num(pol.get("min_mfe_for_time_ok"), 0.25) and r_actual <= num(pol.get("max_loss_after_mfe_r"), -0.20):
            action = "TANCAR_TOTAL"; reason = "TIME_STOP_SENSE_MFE"; close_pct = 1.0; marca = "time_stop"
        elif not accions.get("partial_1") and r_actual >= num(pol.get("partial_after_r"), 0.80):
            action = "TANCAR_PARCIAL"; reason = "PARCIAL_1_INSTITUCIONAL"; close_pct = clamp(num(pol.get("partial_pct"), 0.35), 0.10, 0.75); marca = "partial_1"
        elif not accions.get("partial_2") and r_actual >= num(pol.get("partial2_after_r"), 1.60):
            action = "TANCAR_PARCIAL"; reason = "PARCIAL_2_INSTITUCIONAL"; close_pct = clamp(num(pol.get("partial2_pct"), 0.25), 0.10, 0.60); marca = "partial_2"
        elif mfe >= num(pol.get("lock_after_r"), 0.60):
            prior_lock = num(accions.get("lock_r"), -999.0)
            wanted = num(pol.get("lock_r"), 0.10)
            if wanted > prior_lock + 0.02:
                action = "ACTUALITZAR_STOP"; reason = "LOCK_BENEFICI_INSTITUCIONAL"; nou_stop_r = wanted; marca = "lock"

        if action in {"MANTENIR", "ACTUALITZAR_STOP"} and mfe >= num(pol.get("trail_after_r"), 1.10):
            capture_floor = mfe * num(pol.get("giveback_frac"), 0.48)
            if r_actual < capture_floor and not accions.get("giveback_final"):
                action = "TANCAR_TOTAL" if accions.get("partial_1") else "TANCAR_PARCIAL"
                reason = "GIVEBACK_INSTITUCIONAL"
                close_pct = 1.0 if action == "TANCAR_TOTAL" else 0.50
                marca = "giveback_final" if action == "TANCAR_TOTAL" else "partial_1"

        payload = {
            "versio": VERSIO,
            "position_id": pid,
            "symbol": pos.get("symbol"),
            "side": pos.get("side"),
            "setup": pos.get("setup"),
            "price": price,
            "r_actual": r_actual,
            "mfe_r": mfe,
            "mae_r": mae,
            "retorn_des_de_mfe": retorn,
            "edat_mostres": edat,
            "politica": pol,
            "mostra": mostra,
            "action": action,
            "reason": reason,
            "close_pct": close_pct,
            "nou_stop_r": nou_stop_r,
            "marca_accio": marca,
            "principi_close_pct": "fraccio_sobre_posicio_actual",
        }
        try:
            self.exec("INSERT INTO decisions_gestio_posicio_neta(ts,position_id,symbol,side,setup,action,reason,r_actual,mfe_r,payload) VALUES(?,?,?,?,?,?,?,?,?,?)", (ara_utc(), pid, pos.get("symbol"), pos.get("side"), pos.get("setup"), action, reason, r_actual, mfe, jdump(payload)))
        except Exception:
            pass
        return payload

    def reconstrueix_des_de_trades(self) -> Dict[str, Any]:
        rows = self.q("""
            SELECT t.*, p.payload AS pos_payload
            FROM trades t
            LEFT JOIN positions p ON p.id=t.position_id
            ORDER BY t.id ASC
        """)
        n = 0
        for r in rows:
            try:
                pos = js(r.get("pos_payload"))
                if pos:
                    self.crea_pla_inicial(pos)
                self.registra_tancament(pos, dict(r))
                n += 1
            except Exception as e:
                self.audit("ERROR_RECONSTRUCCIO_TANCAMENT", {"error": repr(e), "trade": dict(r)})
        return {
            "tancaments_processats": n,
            "plans": len(self.q("SELECT position_id FROM plans_gestio_posicio_neta")),
            "politiques": len(self.q("SELECT key FROM politica_gestio_posicio_neta")),
            "mostres": len(self.q("SELECT id FROM mostres_posicio_neta")),
            "simulacions": len(self.q("SELECT id FROM simulacions_sortida_neta")),
        }

    def report(self) -> str:
        lines: List[str] = []
        lines.append("===== INFORME GESTIO POSICIO INSTITUCIONAL NETA =====")
        lines.append(f"UTC: {ara_utc()}")
        counts = {}
        for t in ["plans_gestio_posicio_neta", "mostres_posicio_neta", "tancaments_posicio_neta", "simulacions_sortida_neta", "politica_gestio_posicio_neta", "decisions_gestio_posicio_neta", "auditoria_gestio_posicio_neta", "trades", "positions"]:
            try:
                counts[t] = self.q(f"SELECT COUNT(*) c FROM {t}")[0]["c"]
            except Exception:
                counts[t] = None
        lines.append("COMPTADORS: " + jdump(counts))
        lines.append("")
        lines.append("ETIQUETES SORTIDA:")
        for r in self.q("SELECT etiqueta_sortida, COUNT(*) n, ROUND(AVG(resultat_r),4) avgR, ROUND(AVG(captura_mfe),4) captura, ROUND(AVG(retorn_des_de_mfe),4) retorn FROM tancaments_posicio_neta GROUP BY etiqueta_sortida ORDER BY n DESC"):
            lines.append(f"{r.get('etiqueta_sortida')} n={r.get('n')} avgR={r.get('avgR')} captura={r.get('captura')} retorn={r.get('retorn')}")
        lines.append("")
        lines.append("MILLORS SIMULACIONS SORTIDA:")
        for r in self.q("SELECT model, COUNT(*) n, ROUND(AVG(delta_r),4) delta_mig, ROUND(AVG(resultat_simulat_r),4) simR, ROUND(AVG(resultat_real_r),4) realR FROM simulacions_sortida_neta GROUP BY model ORDER BY delta_mig DESC LIMIT 10"):
            lines.append(f"{r.get('model')} n={r.get('n')} delta={r.get('delta_mig')} simR={r.get('simR')} realR={r.get('realR')}")
        lines.append("")
        lines.append("POLITIQUES APRESES:")
        for r in self.q("SELECT key,n_tancaments,n_mostres,ROUND(avg_resultat_r,4) avgR,ROUND(avg_mfe_r,4) mfe,ROUND(avg_captura_mfe,4) cap,ROUND(pf,3) pf,ROUND(confidence,3) conf,proposta,partial_after_r,partial_pct,partial2_after_r,partial2_pct,lock_after_r,lock_r,trail_after_r,giveback_frac,cut_if_no_mfe_r FROM politica_gestio_posicio_neta ORDER BY n_tancaments DESC, n_mostres DESC, avg_resultat_r ASC LIMIT 35"):
            lines.append(
                f"{r.get('proposta')} nT={r.get('n_tancaments')} nM={r.get('n_mostres')} avgR={r.get('avgR')} mfe={r.get('mfe')} cap={r.get('cap')} pf={r.get('pf')} conf={r.get('conf')} "
                f"p1>{r.get('partial_after_r')} pct={r.get('partial_pct')} p2>{r.get('partial2_after_r')} pct2={r.get('partial2_pct')} lock>{r.get('lock_after_r')} lockR={r.get('lock_r')} trail>{r.get('trail_after_r')} give={r.get('giveback_frac')} cut={r.get('cut_if_no_mfe_r')} key={r.get('key')}"
            )
        lines.append("")
        lines.append("DECISIONS RECENTS:")
        for r in self.q("SELECT ts,position_id,action,reason,ROUND(r_actual,4) r,ROUND(mfe_r,4) mfe,symbol,side,setup FROM decisions_gestio_posicio_neta ORDER BY id DESC LIMIT 20"):
            lines.append(f"{r.get('ts')} {r.get('action')} {r.get('reason')} r={r.get('r')} mfe={r.get('mfe')} {r.get('symbol')} {r.get('side')} {r.get('setup')} pos={r.get('position_id')}")
        return "\n".join(lines)


# =====================================================================
# GESTIO_POSICIO_INSTITUCIONAL_NETA_V4_QUANT
# Autoritat única de sortida: policy compiler + execució idempotent.
# No crea cap motor lateral. Substitueix el get_core oficial.
# =====================================================================

VERSIO_V4_QUANT = "GESTIO_POSICIO_INSTITUCIONAL_NETA_V4_QUANT"


class GestioPosicioInstitucionalNetaV4Quant(GestioPosicioInstitucionalNeta):
    MARCADORS_CONTAMINATS = ("PROVA", "TEST", "SINTETIC", "SYNTHETIC", "SELF_TEST", "PROVA_GESTIO")

    def es_contaminat(self, *vals: Any) -> bool:
        s = " ".join(str(v or "") for v in vals).upper()
        return any(m in s for m in self.MARCADORS_CONTAMINATS)

    def pctl(self, vals: Iterable[Any], p: float, default: float = 0.0) -> float:
        xs = sorted([num(v) for v in vals if math.isfinite(num(v))])
        if not xs:
            return default
        if len(xs) == 1:
            return xs[0]
        k = (len(xs) - 1) * clamp(float(p), 0.0, 1.0)
        f = int(math.floor(k))
        c = int(math.ceil(k))
        if f == c:
            return xs[f]
        return xs[f] * (c - k) + xs[c] * (k - f)

    def assegura_esquema(self) -> None:
        super().assegura_esquema()

        for col, decl in [
            ("partial3_after_r", "REAL DEFAULT 2.10"),
            ("partial3_pct", "REAL DEFAULT 0.20"),
            ("final_after_r", "REAL DEFAULT 3.20"),
            ("break_even_after_r", "REAL DEFAULT 0.42"),
            ("break_even_r", "REAL DEFAULT 0.02"),
            ("emergency_cut_r", "REAL DEFAULT -0.72"),
            ("mfe_p50", "REAL DEFAULT 0"),
            ("mfe_p70", "REAL DEFAULT 0"),
            ("mfe_p85", "REAL DEFAULT 0"),
            ("mfe_p90", "REAL DEFAULT 0"),
            ("mae_p20", "REAL DEFAULT 0"),
            ("giveback_p70", "REAL DEFAULT 0"),
            ("policy_score", "REAL DEFAULT 0"),
            ("policy_lcb", "REAL DEFAULT 0"),
            ("mode_aplicacio", "TEXT DEFAULT 'PAPER_ACTIVE_GUARDED'"),
            ("policy_quality", "TEXT DEFAULT 'BASE'"),
            ("optimizer_model", "TEXT DEFAULT 'NO_OPTIMIZER_YET'"),
        ]:
            self.afegeix_columna("politica_gestio_posicio_neta", col, decl)

        self.exec("""
        CREATE TABLE IF NOT EXISTS accions_gestio_posicio_neta(
            action_key TEXT PRIMARY KEY,
            ts TEXT NOT NULL,
            position_id TEXT NOT NULL,
            action TEXT NOT NULL,
            reason TEXT,
            stage TEXT,
            close_pct REAL,
            price REAL,
            r_actual REAL,
            payload TEXT NOT NULL
        )
        """)

        self.exec("""
        CREATE TABLE IF NOT EXISTS recerca_politica_gestio_posicio_neta(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            key TEXT NOT NULL,
            model TEXT NOT NULL,
            n INTEGER NOT NULL,
            avg_delta_r REAL NOT NULL,
            avg_sim_r REAL NOT NULL,
            avg_real_r REAL NOT NULL,
            score REAL NOT NULL,
            lcb REAL NOT NULL,
            params TEXT NOT NULL,
            payload TEXT NOT NULL,
            UNIQUE(key, model)
        )
        """)

        self.exec("""
        CREATE TABLE IF NOT EXISTS higiene_gestio_posicio_neta(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            event TEXT NOT NULL,
            table_name TEXT,
            rows_affected INTEGER,
            payload TEXT NOT NULL
        )
        """)

    def claus_de_posicio(self, pos: Dict[str, Any], trade: Dict[str, Any] | None = None) -> List[str]:
        if self.es_contaminat(pos, trade):
            return []
        keys = super().claus_de_posicio(pos, trade)
        return [k for k in keys if not self.es_contaminat(k)]

    def politica_base(self) -> Dict[str, Any]:
        base = super().politica_base()
        base.update({
            "key": "BASE",
            "proposta": "BASE_QUANT_V4_GUARDED",
            "partial_after_r": 0.55,
            "partial_pct": 0.28,
            "partial2_after_r": 1.05,
            "partial2_pct": 0.25,
            "partial3_after_r": 1.85,
            "partial3_pct": 0.20,
            "final_after_r": 3.00,
            "break_even_after_r": 0.42,
            "break_even_r": 0.02,
            "lock_after_r": 0.72,
            "lock_r": 0.16,
            "trail_after_r": 1.15,
            "giveback_frac": 0.58,
            "cut_if_no_mfe_r": -0.68,
            "emergency_cut_r": -0.92,
            "time_stop_mostres": 6.0,
            "min_mfe_for_time_ok": 0.25,
            "max_loss_after_mfe_r": -0.05,
            "confidence": 0.0,
            "mfe_p50": 0.0,
            "mfe_p70": 0.0,
            "mfe_p85": 0.0,
            "mfe_p90": 0.0,
            "mae_p20": 0.0,
            "giveback_p70": 0.0,
            "policy_score": 0.0,
            "policy_lcb": 0.0,
            "mode_aplicacio": "PAPER_ACTIVE_GUARDED",
            "policy_quality": "BASE",
            "optimizer_model": "BASE",
        })
        return base

    def _evidence_stats(self, key: str) -> Dict[str, Any]:
        tanc = [
            r for r in self.q("SELECT * FROM tancaments_posicio_neta WHERE claus LIKE ?", (f'%"{key}"%',))
            if not self.es_contaminat(r.get("payload"), r.get("setup"), r.get("claus"))
        ]
        mostres = [
            r for r in self.q("SELECT * FROM mostres_posicio_neta WHERE claus LIKE ?", (f'%"{key}"%',))
            if not self.es_contaminat(r.get("payload"), r.get("setup"), r.get("claus"))
        ]

        vals = [num(r.get("resultat_r")) for r in tanc]
        wins = [v for v in vals if v > 0]
        losses = [v for v in vals if v < 0]
        gross_win = sum(wins)
        gross_loss = abs(sum(losses))
        n = len(tanc)
        nm = len(mostres)
        avg_r = sum(vals) / max(1, n)
        pf = gross_win / gross_loss if gross_loss > 1e-12 else (999.0 if gross_win > 0 else 0.0)

        mfe_vals = [num(r.get("mfe_r")) for r in tanc] + [num(r.get("mfe_r")) for r in mostres]
        mae_vals = [num(r.get("mae_r")) for r in tanc] + [num(r.get("mae_r")) for r in mostres]
        gb_vals = [num(r.get("retorn_des_de_mfe")) for r in tanc] + [num(r.get("retorn_des_de_mfe")) for r in mostres]
        cap_vals = [num(r.get("captura_mfe")) for r in tanc]

        dolenta = sum(
            1 for r in tanc
            if str(r.get("etiqueta_sortida")) in {
                "GUANY_RETORNAT",
                "GUANY_GRAN_RETORNAT",
                "ENTRADA_BONA_SORTIDA_DOLENTA",
                "TP3_TP4_NO_CAPTURAT",
            }
        )
        cua = sum(
            1 for r in tanc
            if str(r.get("etiqueta_sortida")) in {
                "CUA_NEGATIVA_EXCESSIVA",
                "STOP_TARDA_CUA_NEGATIVA",
            }
        )

        confidence = clamp((n / 18.0) + (nm / 420.0), 0.0, 1.0)

        se = 0.0
        if n > 1:
            mean = avg_r
            var = sum((v - mean) ** 2 for v in vals) / max(1, n - 1)
            se = math.sqrt(var / max(1, n))
        lcb = avg_r - 1.20 * se

        return {
            "tanc": tanc,
            "mostres": mostres,
            "vals": vals,
            "n": n,
            "nm": nm,
            "avg_r": avg_r,
            "pf": pf,
            "gross_win": gross_win,
            "gross_loss": gross_loss,
            "avg_mfe": sum(mfe_vals) / max(1, len(mfe_vals)),
            "avg_mae": sum(mae_vals) / max(1, len(mae_vals)),
            "avg_cap": sum(cap_vals) / max(1, len(cap_vals)),
            "avg_giveback": sum(gb_vals) / max(1, len(gb_vals)),
            "mfe_p50": self.pctl(mfe_vals, 0.50, 0.75),
            "mfe_p70": self.pctl(mfe_vals, 0.70, 1.15),
            "mfe_p85": self.pctl(mfe_vals, 0.85, 1.75),
            "mfe_p90": self.pctl(mfe_vals, 0.90, 2.20),
            "mae_p20": self.pctl(mae_vals, 0.20, -0.65),
            "giveback_p70": self.pctl(gb_vals, 0.70, 0.55),
            "dolenta": dolenta,
            "cua": cua,
            "confidence": confidence,
            "lcb": lcb,
        }

    def _seq_per_key(self, key: str) -> Dict[str, Dict[str, Any]]:
        rows = self.q(
            "SELECT * FROM mostres_posicio_neta WHERE claus LIKE ? ORDER BY position_id,id ASC",
            (f'%"{key}"%',),
        )
        by: Dict[str, Dict[str, Any]] = {}
        for r in rows:
            if self.es_contaminat(r.get("payload"), r.get("setup"), r.get("claus")):
                continue
            pid = str(r.get("position_id") or "")
            if not pid:
                continue
            by.setdefault(pid, {"seq": [], "row": r})["seq"].append(num(r.get("r_actual")))

        finals = self.q(
            "SELECT position_id,resultat_r FROM tancaments_posicio_neta WHERE claus LIKE ?",
            (f'%"{key}"%',),
        )
        final_map = {
            str(r.get("position_id")): num(r.get("resultat_r"))
            for r in finals
            if not self.es_contaminat(r)
        }

        return {
            pid: {
                **v,
                "real_r": final_map.get(pid, v["seq"][-1] if v["seq"] else 0.0),
            }
            for pid, v in by.items()
            if v.get("seq")
        }

    def _simula_model(self, seq: List[float], real_r: float, params: Dict[str, float]) -> float:
        mfe = -999.0
        realized = 0.0
        remaining = 1.0
        floor = None
        done = set()

        for r in seq:
            mfe = max(mfe, r)

            if mfe >= params.get("be_after", 999.0):
                floor = max(num(floor, -999.0), params.get("be_r", 0.0))

            if mfe >= params.get("lock_after", 999.0):
                floor = max(num(floor, -999.0), params.get("lock_r", 0.0))

            for tag, lvl, pct in [
                ("p1", params.get("p1", 999.0), params.get("pct1", 0.0)),
                ("p2", params.get("p2", 999.0), params.get("pct2", 0.0)),
                ("p3", params.get("p3", 999.0), params.get("pct3", 0.0)),
            ]:
                if tag not in done and r >= lvl and remaining > 0.0:
                    realized += r * pct * remaining
                    remaining *= max(0.0, 1.0 - pct)
                    done.add(tag)

            if r >= params.get("final", 999.0):
                return realized + remaining * r

            if floor is not None and r <= floor:
                return realized + remaining * floor

            if mfe >= params.get("trail_after", 999.0) and r <= mfe * params.get("giveback", 0.5):
                return realized + remaining * r

            if mfe < params.get("min_mfe", 0.25) and r <= params.get("cut", -999.0):
                return realized + remaining * r

            if mfe >= params.get("mfe_to_loss_after", 0.50) and r <= params.get("max_loss_after_mfe", -999.0):
                return realized + remaining * r

        return realized + remaining * real_r

    def optimitza_politica(self, key: str, stats: Dict[str, Any]) -> Dict[str, Any]:
        seqs = self._seq_per_key(key)
        if len(seqs) < 3:
            return {"model": "SENSE_SEQ_SUFFICIENT", "score": 0.0, "lcb": 0.0, "params": {}}

        mfe50 = max(0.35, stats.get("mfe_p50", 0.75))
        mfe70 = max(0.60, stats.get("mfe_p70", 1.10))
        mfe85 = max(0.90, stats.get("mfe_p85", 1.70))
        mfe90 = max(1.10, stats.get("mfe_p90", 2.20))

        candidates = []
        pct_sets = [
            (0.25, 0.25, 0.20),
            (0.35, 0.25, 0.15),
            (0.20, 0.20, 0.20),
        ]

        for pct1, pct2, pct3 in pct_sets:
            for be_after in [0.35, 0.45, 0.60]:
                for giveback in [0.45, 0.55, 0.65, 0.72]:
                    params = {
                        "p1": clamp(mfe50 * 0.55, 0.35, 0.85),
                        "pct1": pct1,
                        "p2": clamp(mfe70 * 0.78, 0.75, 1.60),
                        "pct2": pct2,
                        "p3": clamp(mfe85 * 0.88, 1.15, 2.65),
                        "pct3": pct3,
                        "final": clamp(mfe90 * 0.96, 1.80, 4.20),
                        "be_after": be_after,
                        "be_r": 0.02,
                        "lock_after": clamp(mfe50 * 0.80, 0.55, 1.15),
                        "lock_r": 0.12,
                        "trail_after": clamp(mfe70 * 0.90, 0.85, 2.20),
                        "giveback": giveback,
                        "cut": clamp(max(-0.95, stats.get("mae_p20", -0.65) * 0.90), -0.95, -0.45),
                        "min_mfe": 0.25,
                        "mfe_to_loss_after": 0.50,
                        "max_loss_after_mfe": -0.05,
                    }

                    deltas = []
                    sims = []
                    reals = []

                    for _, info in seqs.items():
                        sim = self._simula_model(info["seq"], info["real_r"], params)
                        sims.append(sim)
                        reals.append(info["real_r"])
                        deltas.append(sim - info["real_r"])

                    n = len(deltas)
                    avg_delta = sum(deltas) / max(1, n)
                    avg_sim = sum(sims) / max(1, n)
                    avg_real = sum(reals) / max(1, n)
                    var = sum((x - avg_delta) ** 2 for x in deltas) / max(1, n - 1) if n > 1 else 0.0
                    se = math.sqrt(var / max(1, n)) if n > 1 else 0.0
                    lcb = avg_delta - 1.0 * se
                    bad_rate = sum(1 for d in deltas if d < -0.10) / max(1, n)
                    score = lcb + 0.25 * avg_sim - 0.20 * bad_rate

                    candidates.append((score, lcb, avg_delta, avg_sim, avg_real, n, params, bad_rate))

        candidates.sort(key=lambda x: x[0], reverse=True)
        best = candidates[0]
        model = "GRID_MFE_MAE_GIVEBACK_V4"

        rec = {
            "model": model,
            "score": best[0],
            "lcb": best[1],
            "avg_delta_r": best[2],
            "avg_sim_r": best[3],
            "avg_real_r": best[4],
            "n": best[5],
            "params": best[6],
            "bad_rate": best[7],
        }

        try:
            self.exec("""
            INSERT OR REPLACE INTO recerca_politica_gestio_posicio_neta(
                ts,key,model,n,avg_delta_r,avg_sim_r,avg_real_r,score,lcb,params,payload
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
            """, (
                ara_utc(),
                key,
                model,
                rec["n"],
                rec["avg_delta_r"],
                rec["avg_sim_r"],
                rec["avg_real_r"],
                rec["score"],
                rec["lcb"],
                jdump(rec["params"]),
                jdump(rec),
            ))
        except Exception:
            pass

        return rec

    def recalcula_politica(self, key: str) -> Dict[str, Any]:
        if self.es_contaminat(key):
            return {"updated": False, "reason": "KEY_CONTAMINADA"}

        stats = self._evidence_stats(key)
        if stats["n"] == 0 and stats["nm"] == 0:
            return {}

        base = self.politica_base()
        opt = self.optimitza_politica(key, stats)
        target = dict(base)
        proposta = "BASE_QUANT_V4_GUARDED"
        quality = "RECERCA"
        params = opt.get("params") or {}

        if params and opt.get("n", 0) >= 3 and opt.get("lcb", 0.0) > -0.05:
            target.update({
                "partial_after_r": params["p1"],
                "partial_pct": params["pct1"],
                "partial2_after_r": params["p2"],
                "partial2_pct": params["pct2"],
                "partial3_after_r": params["p3"],
                "partial3_pct": params["pct3"],
                "final_after_r": params["final"],
                "break_even_after_r": params["be_after"],
                "break_even_r": params["be_r"],
                "lock_after_r": params["lock_after"],
                "lock_r": params["lock_r"],
                "trail_after_r": params["trail_after"],
                "giveback_frac": params["giveback"],
                "cut_if_no_mfe_r": params["cut"],
                "max_loss_after_mfe_r": params["max_loss_after_mfe"],
            })
            proposta = "OPTIMITZADA_GRID_MFE_MAE_V4"
            quality = "OPTIMITZADA_SHADOW" if stats["confidence"] < 0.45 else "OPTIMITZADA_GUARDED"

        if stats["n"] >= 3 and (
            stats["avg_r"] < -0.15
            or stats["pf"] < 0.85
            or stats["cua"] / max(1, stats["n"]) >= 0.25
        ):
            target.update({
                "partial_after_r": min(num(target.get("partial_after_r")), 0.48),
                "partial_pct": max(num(target.get("partial_pct")), 0.36),
                "partial2_after_r": min(num(target.get("partial2_after_r")), 0.92),
                "partial3_after_r": min(num(target.get("partial3_after_r")), 1.45),
                "final_after_r": min(num(target.get("final_after_r")), 2.15),
                "break_even_after_r": 0.34,
                "break_even_r": 0.01,
                "lock_after_r": 0.50,
                "lock_r": 0.10,
                "trail_after_r": 0.78,
                "giveback_frac": max(num(target.get("giveback_frac")), 0.68),
                "cut_if_no_mfe_r": max(num(target.get("cut_if_no_mfe_r")), -0.62),
                "max_loss_after_mfe_r": -0.03,
            })
            proposta = "DEFENSIVA_R_NEGATIU_V4"
            quality = "DEFENSIVA"

        if stats["n"] >= 5 and stats["avg_cap"] >= 0.58 and stats["avg_r"] > 0.05 and stats["pf"] > 1.15:
            target.update({
                "partial_after_r": max(num(target.get("partial_after_r")), 0.75),
                "partial_pct": min(num(target.get("partial_pct")), 0.24),
                "partial2_after_r": max(num(target.get("partial2_after_r")), 1.35),
                "partial2_pct": min(num(target.get("partial2_pct")), 0.22),
                "partial3_after_r": max(num(target.get("partial3_after_r")), 2.10),
                "partial3_pct": min(num(target.get("partial3_pct")), 0.18),
                "final_after_r": max(num(target.get("final_after_r")), 3.20),
                "trail_after_r": max(num(target.get("trail_after_r")), 1.55),
                "giveback_frac": min(num(target.get("giveback_frac")), 0.48),
                "cut_if_no_mfe_r": min(num(target.get("cut_if_no_mfe_r")), -0.80),
            })
            proposta = "EXPANSIVA_EDGE_VALIDAT_V4"
            quality = "VALIDADA_EXPANSIVA"

        confidence = stats["confidence"]
        pol = self._barreja(base, target, confidence)

        pol.update({
            "key": key,
            "proposta": proposta,
            "confidence": confidence,
            "mfe_p50": stats["mfe_p50"],
            "mfe_p70": stats["mfe_p70"],
            "mfe_p85": stats["mfe_p85"],
            "mfe_p90": stats["mfe_p90"],
            "mae_p20": stats["mae_p20"],
            "giveback_p70": stats["giveback_p70"],
            "policy_score": opt.get("score", 0.0),
            "policy_lcb": opt.get("lcb", 0.0),
            "mode_aplicacio": "PAPER_ACTIVE_GUARDED",
            "policy_quality": quality,
            "optimizer_model": opt.get("model", "NO_OPTIMIZER"),
        })

        payload = {
            "versio": VERSIO_V4_QUANT,
            "key": key,
            "stats": {k: v for k, v in stats.items() if k not in {"tanc", "mostres", "vals"}},
            "optimizer": opt,
            "politica": pol,
        }

        self.exec("""
        INSERT OR REPLACE INTO politica_gestio_posicio_neta(
            key,updated_at,n_tancaments,n_mostres,avg_resultat_r,avg_mfe_r,avg_mae_r,avg_captura_mfe,
            avg_retorn_des_de_mfe,pf,sortida_dolenta_n,cua_negativa_n,proposta,partial_after_r,partial_pct,
            lock_after_r,lock_r,trail_after_r,giveback_frac,cut_if_no_mfe_r,payload,partial2_after_r,partial2_pct,
            time_stop_mostres,min_mfe_for_time_ok,confidence,max_loss_after_mfe_r,
            partial3_after_r,partial3_pct,final_after_r,break_even_after_r,break_even_r,emergency_cut_r,
            mfe_p50,mfe_p70,mfe_p85,mfe_p90,mae_p20,giveback_p70,policy_score,policy_lcb,mode_aplicacio,policy_quality,optimizer_model
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            key,
            ara_utc(),
            stats["n"],
            stats["nm"],
            stats["avg_r"],
            stats["avg_mfe"],
            stats["avg_mae"],
            stats["avg_cap"],
            stats["avg_giveback"],
            stats["pf"],
            stats["dolenta"],
            stats["cua"],
            proposta,
            pol["partial_after_r"],
            pol["partial_pct"],
            pol["lock_after_r"],
            pol["lock_r"],
            pol["trail_after_r"],
            pol["giveback_frac"],
            pol["cut_if_no_mfe_r"],
            jdump(payload),
            pol["partial2_after_r"],
            pol["partial2_pct"],
            pol["time_stop_mostres"],
            pol["min_mfe_for_time_ok"],
            confidence,
            pol["max_loss_after_mfe_r"],
            pol["partial3_after_r"],
            pol["partial3_pct"],
            pol["final_after_r"],
            pol["break_even_after_r"],
            pol["break_even_r"],
            pol["emergency_cut_r"],
            stats["mfe_p50"],
            stats["mfe_p70"],
            stats["mfe_p85"],
            stats["mfe_p90"],
            stats["mae_p20"],
            stats["giveback_p70"],
            pol["policy_score"],
            pol["policy_lcb"],
            pol["mode_aplicacio"],
            pol["policy_quality"],
            pol["optimizer_model"],
        ))

        return payload

    def politica_per_posicio(self, pos: Dict[str, Any]) -> Dict[str, Any]:
        base = self.politica_base()

        if self.es_contaminat(pos):
            return base

        for k in self.claus_de_posicio(pos):
            rows = self.q(
                "SELECT * FROM politica_gestio_posicio_neta WHERE key=? AND (n_tancaments>=3 OR n_mostres>=20) LIMIT 1",
                (k,),
            )
            if rows:
                r = rows[0]
                out = dict(base)
                for kk in base.keys():
                    if kk in r and r.get(kk) is not None:
                        out[kk] = num(r.get(kk), base.get(kk)) if isinstance(base.get(kk), (int, float)) else r.get(kk)
                out.update({
                    "key": k,
                    "proposta": r.get("proposta") or out.get("proposta"),
                    "policy_quality": r.get("policy_quality") or out.get("policy_quality"),
                    "mode_aplicacio": r.get("mode_aplicacio") or out.get("mode_aplicacio"),
                    "optimizer_model": r.get("optimizer_model") or out.get("optimizer_model"),
                })
                return out

        return base

    def crea_pla_inicial(self, pos: Dict[str, Any]) -> Dict[str, Any]:
        if self.es_contaminat(pos):
            self.audit("PLA_EXCLOS_CONTAMINACIO", {"position_id": (pos or {}).get("id")})
            return {"created": False, "reason": "CONTAMINACIO_PROVA_TEST"}

        pos = dict(pos or {})
        pid = str(pos.get("id") or "")
        if not pid:
            return {"created": False, "reason": "SENSE_POSITION_ID"}

        risk_abs = self._risk_abs(pos)
        pol = self.politica_per_posicio(pos)

        plan = {
            "versio": VERSIO_V4_QUANT,
            "position_id": pid,
            "policy_key": pol.get("key"),
            "proposta": pol.get("proposta"),
            "policy_quality": pol.get("policy_quality"),
            "optimizer_model": pol.get("optimizer_model"),
            "risk_abs": risk_abs,
            "stages": {
                "break_even": {"after_r": pol.get("break_even_after_r"), "stop_r": pol.get("break_even_r")},
                "partial_1": {"after_r": pol.get("partial_after_r"), "pct_current": pol.get("partial_pct")},
                "partial_2": {"after_r": pol.get("partial2_after_r"), "pct_current": pol.get("partial2_pct")},
                "partial_3": {"after_r": pol.get("partial3_after_r"), "pct_current": pol.get("partial3_pct")},
                "final": {"after_r": pol.get("final_after_r"), "pct_current": 1.0},
                "lock": {"after_r": pol.get("lock_after_r"), "lock_r": pol.get("lock_r")},
                "trail": {"after_r": pol.get("trail_after_r"), "giveback_frac": pol.get("giveback_frac")},
                "tail_cut": {
                    "emergency_cut_r": pol.get("emergency_cut_r"),
                    "cut_if_no_mfe_r": pol.get("cut_if_no_mfe_r"),
                    "max_loss_after_mfe_r": pol.get("max_loss_after_mfe_r"),
                },
                "time_stop": {"mostres": pol.get("time_stop_mostres"), "min_mfe": pol.get("min_mfe_for_time_ok")},
            },
            "autoritat_unica": "gestio_posicio_institucional_neta",
            "principi_close_pct": "fraccio_sobre_posicio_actual",
        }

        payload = {
            "position": pos,
            "politica": pol,
            "plan": plan,
            "mapa_causal": self.mapa_causal_de_posicio(pos),
        }

        self.exec("""
        INSERT OR REPLACE INTO plans_gestio_posicio_neta(
            position_id,created_at,updated_at,symbol,side,setup,entry_price,stop_loss,risk_abs,policy_key,estat,plan,payload
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            pid,
            ara_utc(),
            ara_utc(),
            pos.get("symbol"),
            pos.get("side"),
            pos.get("setup"),
            num(pos.get("entry_price")),
            num(pos.get("stop_loss")),
            risk_abs,
            pol.get("key"),
            "ACTIU",
            jdump(plan),
            jdump(payload),
        ))

        return {"created": True, "plan": plan, "politica": pol}

    def registra_mostra_oberta(self, pos: Dict[str, Any], price: float, r_actual: float, risk_abs: float) -> Dict[str, Any]:
        if self.es_contaminat(pos):
            return {"inserted": False, "reason": "CONTAMINACIO_PROVA_TEST"}
        return super().registra_mostra_oberta(pos, price, r_actual, risk_abs)

    def reserva_accio(
        self,
        pos: Dict[str, Any],
        action: str,
        reason: str,
        stage: str,
        close_pct: float,
        price: float,
        r_actual: float,
    ) -> bool:
        pid = str((pos or {}).get("id") or "")
        if not pid:
            return True

        action_key = f"{pid}|{str(action).upper()}|{str(stage).upper()}|{round(num(close_pct), 6)}"
        payload = {
            "versio": VERSIO_V4_QUANT,
            "position_id": pid,
            "action": action,
            "reason": reason,
            "stage": stage,
            "close_pct": close_pct,
            "price": price,
            "r_actual": r_actual,
        }

        try:
            self.exec("""
            INSERT INTO accions_gestio_posicio_neta(
                action_key,ts,position_id,action,reason,stage,close_pct,price,r_actual,payload
            ) VALUES(?,?,?,?,?,?,?,?,?,?)
            """, (
                action_key,
                ara_utc(),
                pid,
                action,
                reason,
                stage,
                num(close_pct),
                num(price),
                num(r_actual),
                jdump(payload),
            ))
            return True
        except Exception:
            return False

    def decideix_accio(self, pos: Dict[str, Any], price: float, r_actual: float, risk_abs: float) -> Dict[str, Any]:
        pos = dict(pos or {})
        pid = str(pos.get("id") or "")

        if self.es_contaminat(pos):
            return {
                "versio": VERSIO_V4_QUANT,
                "position_id": pid,
                "action": "MANTENIR",
                "reason": "CONTAMINACIO_EXCLOSA",
            }

        mostra = self.registra_mostra_oberta(pos, price, r_actual, risk_abs)
        pol = self.politica_per_posicio(pos)

        mfe = max(num(pos.get("mfe_r")), r_actual)
        mae = min(num(pos.get("mae_r")), r_actual)
        retorn = max(0.0, mfe - r_actual)
        edat = int(num(mostra.get("edat_mostres"), num(pos.get("gestio_mostres_n"), 0)))
        accions = self._accions(pos)

        action = "MANTENIR"
        reason = "SENSE_ACCIO"
        close_pct = 0.0
        nou_stop_r = None
        stage = "NONE"

        if r_actual <= num(pol.get("emergency_cut_r"), -0.92):
            action = "TANCAR_TOTAL"
            reason = "EMERGENCY_CUT_R_V4"
            close_pct = 1.0
            stage = "emergency_cut"

        elif r_actual <= num(pol.get("cut_if_no_mfe_r"), -0.68) and mfe < num(pol.get("min_mfe_for_time_ok"), 0.25) and edat >= 2:
            action = "TANCAR_TOTAL"
            reason = "TAIL_CUT_SENSE_MFE_V4"
            close_pct = 1.0
            stage = "tail_cut"

        elif mfe >= 0.50 and r_actual <= num(pol.get("max_loss_after_mfe_r"), -0.05) and not accions.get("mfe_to_loss_v4"):
            action = "TANCAR_TOTAL"
            reason = "MFE_CONVERTIT_EN_PERDUA_V4"
            close_pct = 1.0
            stage = "mfe_to_loss_v4"

        elif r_actual >= num(pol.get("final_after_r"), 3.0) and not accions.get("final_v4"):
            action = "TANCAR_TOTAL"
            reason = "FINAL_TP_V4"
            close_pct = 1.0
            stage = "final_v4"

        elif not accions.get("partial_1_v4") and r_actual >= num(pol.get("partial_after_r"), 0.55):
            action = "TANCAR_PARCIAL"
            reason = "PARCIAL_1_V4"
            close_pct = clamp(num(pol.get("partial_pct"), 0.28), 0.08, 0.65)
            stage = "partial_1_v4"
            nou_stop_r = max(num(pol.get("break_even_r"), 0.02), 0.0)

        elif not accions.get("partial_2_v4") and r_actual >= num(pol.get("partial2_after_r"), 1.05):
            action = "TANCAR_PARCIAL"
            reason = "PARCIAL_2_V4"
            close_pct = clamp(num(pol.get("partial2_pct"), 0.25), 0.06, 0.55)
            stage = "partial_2_v4"
            nou_stop_r = max(num(pol.get("lock_r"), 0.16), 0.05)

        elif not accions.get("partial_3_v4") and r_actual >= num(pol.get("partial3_after_r"), 1.85):
            action = "TANCAR_PARCIAL"
            reason = "PARCIAL_3_V4"
            close_pct = clamp(num(pol.get("partial3_pct"), 0.20), 0.05, 0.45)
            stage = "partial_3_v4"
            nou_stop_r = max(num(pol.get("lock_r"), 0.16), r_actual * 0.28)

        elif edat >= num(pol.get("time_stop_mostres"), 6.0) and mfe < num(pol.get("min_mfe_for_time_ok"), 0.25) and r_actual <= num(pol.get("max_loss_after_mfe_r"), -0.05):
            action = "TANCAR_TOTAL"
            reason = "TIME_STOP_SENSE_MFE_V4"
            close_pct = 1.0
            stage = "time_stop_v4"

        elif mfe >= num(pol.get("trail_after_r"), 1.15) and r_actual <= mfe * num(pol.get("giveback_frac"), 0.58) and not accions.get("giveback_v4"):
            action = "TANCAR_TOTAL" if accions.get("partial_1_v4") or accions.get("partial_1") else "TANCAR_PARCIAL"
            reason = "GIVEBACK_MFE_V4"
            close_pct = 1.0 if action == "TANCAR_TOTAL" else 0.50
            stage = "giveback_v4" if action == "TANCAR_TOTAL" else "partial_1_v4"

        elif mfe >= num(pol.get("break_even_after_r"), 0.42):
            wanted = max(num(pol.get("break_even_r"), 0.02), 0.0)
            if wanted > num(accions.get("lock_r"), -999.0) + 0.005:
                action = "ACTUALITZAR_STOP"
                reason = "BREAK_EVEN_V4"
                close_pct = 0.0
                stage = "break_even_v4"
                nou_stop_r = wanted

        elif mfe >= num(pol.get("lock_after_r"), 0.72):
            wanted = num(pol.get("lock_r"), 0.16)
            if wanted > num(accions.get("lock_r"), -999.0) + 0.01:
                action = "ACTUALITZAR_STOP"
                reason = "LOCK_PROFIT_V4"
                close_pct = 0.0
                stage = "lock_profit_v4"
                nou_stop_r = wanted

        payload = {
            "versio": VERSIO_V4_QUANT,
            "position_id": pid,
            "symbol": pos.get("symbol"),
            "side": pos.get("side"),
            "setup": pos.get("setup"),
            "price": price,
            "r_actual": r_actual,
            "mfe_r": mfe,
            "mae_r": mae,
            "retorn_des_de_mfe": retorn,
            "edat_mostres": edat,
            "politica": pol,
            "mostra": mostra,
            "action": action,
            "reason": reason,
            "close_pct": close_pct,
            "nou_stop_r": nou_stop_r,
            "marca_accio": stage,
            "stage": stage,
            "autoritat_unica": "gestio_posicio_institucional_neta",
        }

        try:
            self.exec("""
            INSERT INTO decisions_gestio_posicio_neta(
                ts,position_id,symbol,side,setup,action,reason,r_actual,mfe_r,payload
            ) VALUES(?,?,?,?,?,?,?,?,?,?)
            """, (
                ara_utc(),
                pid,
                pos.get("symbol"),
                pos.get("side"),
                pos.get("setup"),
                action,
                reason,
                r_actual,
                mfe,
                jdump(payload),
            ))
        except Exception:
            pass

        return payload

    def mesura_tancament(self, pos: Dict[str, Any], trade: Dict[str, Any]) -> Dict[str, Any]:
        out = super().mesura_tancament(pos, trade)

        resultat_r = num(out.get("resultat_r"))
        mfe_r = num(out.get("mfe_r"))
        mae_r = num(out.get("mae_r"))
        retorn = num(out.get("retorn_des_de_mfe"))
        captura = num(out.get("captura_mfe"))

        if mfe_r >= 2.0 and captura < 0.45:
            out["etiqueta_sortida"] = "TP3_TP4_NO_CAPTURAT"
        if mfe_r >= 1.2 and retorn >= 0.85:
            out["etiqueta_sortida"] = "GUANY_GRAN_RETORNAT"
        if mae_r <= -1.05 and resultat_r < -0.75:
            out["etiqueta_sortida"] = "STOP_TARDA_CUA_NEGATIVA"

        return out

    def registra_tancament(self, pos: Dict[str, Any], trade: Dict[str, Any]) -> Dict[str, Any]:
        if self.es_contaminat(pos, trade):
            self.audit("TANCAMENT_EXCLOS_CONTAMINACIO", {
                "position_id": (pos or {}).get("id"),
                "trade": trade,
            })
            return {"inserted": False, "reason": "CONTAMINACIO_PROVA_TEST"}

        return super().registra_tancament(pos, trade)

    def neteja_contaminacio_prova(self) -> Dict[str, Any]:
        tables = [
            "plans_gestio_posicio_neta",
            "mostres_posicio_neta",
            "tancaments_posicio_neta",
            "politica_gestio_posicio_neta",
            "decisions_gestio_posicio_neta",
            "simulacions_sortida_neta",
            "auditoria_gestio_posicio_neta",
            "accions_gestio_posicio_neta",
            "recerca_politica_gestio_posicio_neta",
        ]
        markers = list(self.MARCADORS_CONTAMINATS)
        total = 0
        detail = {}

        for table in tables:
            try:
                cols = [dict(r).get("name") for r in self.db.query(f"PRAGMA table_info({table})")]
                parts = []
                params = []
                for c in cols:
                    if not c:
                        continue
                    for m in markers:
                        parts.append(f'UPPER(CAST("{c}" AS TEXT)) LIKE ?')
                        params.append(f"%{m}%")

                if not parts:
                    continue

                where = " OR ".join(parts)
                n = self.q(f"SELECT COUNT(*) c FROM {table} WHERE {where}", params)[0]["c"]

                if n:
                    self.exec(f"DELETE FROM {table} WHERE {where}", params)

                detail[table] = n
                total += int(n or 0)

            except Exception as e:
                detail[table] = f"ERROR:{repr(e)}"

        try:
            self.exec("""
            INSERT INTO higiene_gestio_posicio_neta(
                ts,event,table_name,rows_affected,payload
            ) VALUES(?,?,?,?,?)
            """, (
                ara_utc(),
                "NETEJA_CONTAMINACIO_PROVA_TEST_V4",
                "*",
                total,
                jdump(detail),
            ))
        except Exception:
            pass

        return {"eliminats": total, "detall": detail}

    def report(self) -> str:
        base = super().report()
        lines = [base, "", "===== V4 QUANT POLICY COMPILER ====="]

        for r in self.q("""
            SELECT key,proposta,policy_quality,optimizer_model,n_tancaments,n_mostres,
                   ROUND(avg_resultat_r,4) avgR,ROUND(pf,3) pf,ROUND(confidence,3) conf,
                   ROUND(policy_score,4) score,ROUND(policy_lcb,4) lcb,
                   ROUND(partial_after_r,3) p1,ROUND(partial2_after_r,3) p2,ROUND(partial3_after_r,3) p3,ROUND(final_after_r,3) final,
                   ROUND(break_even_after_r,3) be_after,ROUND(trail_after_r,3) trail,ROUND(giveback_frac,3) give,ROUND(cut_if_no_mfe_r,3) cut
            FROM politica_gestio_posicio_neta
            ORDER BY n_tancaments DESC,n_mostres DESC,avg_resultat_r ASC
            LIMIT 35
        """):
            lines.append(jdump(dict(r)))

        lines.append("===== ACCIONS RECENTS V4 =====")
        for r in self.q("""
            SELECT ts,position_id,action,reason,stage,close_pct,ROUND(price,2) price,ROUND(r_actual,4) r
            FROM accions_gestio_posicio_neta
            ORDER BY ts DESC
            LIMIT 25
        """):
            lines.append(jdump(dict(r)))

        return "\n".join(lines)


def get_core(db: Any) -> GestioPosicioInstitucionalNeta:
    return GestioPosicioInstitucionalNetaV4Quant(db)
