from pathlib import Path
import re, shutil, datetime

ROOT = Path("/storage/emulated/0/Download/joan_crypto_ai_pro_v14")
TS = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d_%H%M%S")
BK = ROOT / "backups" / f"gestio_posicio_v4_quant_{TS}"

BROKER = ROOT / "joanbot/execution/broker.py"
GESTIO = ROOT / "joanbot/institutional/gestio_posicio_institucional_neta.py"
TOOLS = ROOT / "tools"

if not BROKER.exists():
    raise SystemExit("ERROR: falta joanbot/execution/broker.py")
if not GESTIO.exists():
    raise SystemExit("ERROR: falta joanbot/institutional/gestio_posicio_institucional_neta.py")

for p in [BROKER, GESTIO]:
    dst = BK / p.relative_to(ROOT)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(p, dst)

txt = GESTIO.read_text(encoding="utf-8")

if "GESTIO_POSICIO_INSTITUCIONAL_NETA_V4_QUANT" not in txt:
    txt = re.sub(
        r"\n\ndef get_core\(db: Any\) -> GestioPosicioInstitucionalNeta:\n    return GestioPosicioInstitucionalNeta\(db\)\n?\s*$",
        "",
        txt,
        flags=re.S,
    )

    txt += r'''

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
'''

    GESTIO.write_text(txt, encoding="utf-8")

b = BROKER.read_text(encoding="utf-8")

if "GESTIO_POSICIO_V4_QUANT_UNICA" not in b:
    b = re.sub(
        r"    def mark_positions\(self, prices: Dict\[str, float\]\) -> List\[Dict\[str, Any\]\]:\n        self\.refresh\(\)\n        return self\.wallet\.get\('open', \[\]\)\n",
        "    def mark_positions(self, prices: Dict[str, float]) -> List[Dict[str, Any]]:\n"
        "        # GESTIO_POSICIO_V4_QUANT_UNICA: el broker delega en una única autoritat.\n"
        "        return self._manage_positions_v4_quant(prices)\n\n",
        b,
    )

    manage_code = r'''    def _manage_positions_v4_quant(self, prices: Dict[str, float]) -> List[Dict[str, Any]]:
        """Execució de sortida V4. No decideix edge; només aplica l'autoritat de gestió."""
        import json

        actions: List[Dict[str, Any]] = []
        positions = self.refresh().get('open', []) or []

        def update_payload(p: Dict[str, Any]) -> None:
            try:
                self.db.execute(
                    'UPDATE positions SET payload=? WHERE id=?',
                    (json.dumps(p, sort_keys=True), p.get('id'))
                )
            except Exception:
                pass

        for p in list(positions):
            try:
                sym = str(p.get('symbol') or '').upper()
                price = fnum(prices.get(sym) if isinstance(prices, dict) else None)
                if price <= 0:
                    continue

                side = str(p.get('side') or '').upper()
                entry = fnum(p.get('entry_price') or p.get('entry'))
                sl = fnum(p.get('stop_loss'))
                risk_abs = abs(entry - sl) if entry > 0 and sl > 0 else 0.0
                if risk_abs <= 0:
                    continue

                r = ((price - entry) / risk_abs) if side == 'LONG' else ((entry - price) / risk_abs)

                p['mfe_r'] = max(fnum(p.get('mfe_r')), r)
                p['mae_r'] = min(fnum(p.get('mae_r')), r)
                p['last_price'] = price
                p['gestio_mostres_n'] = int(fnum(p.get('gestio_mostres_n'), 0)) + 1

                stop_hit = (price <= fnum(p.get('stop_loss')) if side == 'LONG' else price >= fnum(p.get('stop_loss')))
                if stop_hit:
                    if self.gestio_posicio.reserva_accio(p, 'TANCAR_TOTAL', 'STOP_LOSS', 'hard_stop', 1.0, price, r):
                        actions.append(self.close_position(p, price, 'STOP_LOSS', 1.0))
                    continue

                decisio = self.gestio_posicio.decideix_accio(p, price, r, risk_abs)
                p['gestio_posicio_institucional_neta'] = decisio

                action = decisio.get('action') if isinstance(decisio, dict) else None
                reason = decisio.get('reason', 'GESTIO_POSICIO_V4') if isinstance(decisio, dict) else 'GESTIO_POSICIO_V4'
                stage = decisio.get('stage') or decisio.get('marca_accio') or 'stage'

                def update_stop_from_r(lock_r: float) -> None:
                    if side == 'LONG':
                        p['stop_loss'] = max(fnum(p.get('stop_loss')), entry + risk_abs * lock_r)
                    else:
                        old = fnum(p.get('stop_loss'))
                        candidate = entry - risk_abs * lock_r
                        p['stop_loss'] = min(old if old > 0 else candidate, candidate)

                    p.setdefault('gestio_accions', {})['lock_r'] = max(
                        fnum(p.get('gestio_accions', {}).get('lock_r'), -999.0),
                        lock_r
                    )

                if action == 'ACTUALITZAR_STOP':
                    update_stop_from_r(fnum(decisio.get('nou_stop_r'), 0.02))
                    p.setdefault('gestio_accions', {})[stage] = True
                    update_payload(p)
                    continue

                if action in {'TANCAR_TOTAL', 'TANCAR_PARCIAL'}:
                    close_pct = 1.0 if action == 'TANCAR_TOTAL' else clamp(
                        fnum(decisio.get('close_pct'), 0.25),
                        0.01,
                        0.90
                    )

                    if not self.gestio_posicio.reserva_accio(p, action, reason, stage, close_pct, price, r):
                        update_payload(p)
                        continue

                    p.setdefault('gestio_accions', {})[stage] = True

                    if decisio.get('nou_stop_r') is not None:
                        update_stop_from_r(fnum(decisio.get('nou_stop_r')))

                    actions.append(self.close_position(p, price, reason, close_pct))
                    continue

                update_payload(p)

            except Exception as e:
                try:
                    self.db.runtime_event(
                        'gestio_posicio_institucional_neta',
                        'ERROR',
                        'manage_v4_quant_fallit',
                        {'error': repr(e), 'position_id': p.get('id')}
                    )
                except Exception:
                    pass

        self.refresh()
        return actions

'''

    b = b.replace(
        "    def close_position(self, pos: Dict[str, Any], exit_price: float, reason: str, close_pct: float = 1.0) -> Dict[str, Any]:",
        manage_code + "    def close_position(self, pos: Dict[str, Any], exit_price: float, reason: str, close_pct: float = 1.0) -> Dict[str, Any]:",
    )

    pat = r"    def manage\(self, prices: Dict\[str, float\]\) -> List\[Dict\[str, Any\]\]:\n[\s\S]*?    def _update_payload"
    repl = (
        "    def manage(self, prices: Dict[str, float]) -> List[Dict[str, Any]]:\n"
        "        # Adaptador de compatibilitat: la decisió real és a PaperBroker._manage_positions_v4_quant.\n"
        "        return self.broker._manage_positions_v4_quant(prices)\n\n"
        "    def _update_payload"
    )

    b, n = re.subn(pat, repl, b, count=1)
    if n != 1:
        raise SystemExit("ERROR: no s'ha pogut convertir ProfitGuard en wrapper V4")

    BROKER.write_text(b, encoding="utf-8")

TOOLS.mkdir(exist_ok=True)

(TOOLS / "valida_gestio_posicio_v4_quant.py").write_text(r'''
from pathlib import Path
import json, sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from joanbot.storage import get_db
from joanbot.institutional.gestio_posicio_institucional_neta import get_core

broker = (ROOT / "joanbot/execution/broker.py").read_text(encoding="utf-8")
gestio = (ROOT / "joanbot/institutional/gestio_posicio_institucional_neta.py").read_text(encoding="utf-8")
db = get_db()
core = get_core(db)

checks = {
    "broker_mark_positions_v4": "GESTIO_POSICIO_V4_QUANT_UNICA" in broker,
    "broker_manage_v4": "_manage_positions_v4_quant" in broker,
    "profitguard_wrapper": "Adaptador de compatibilitat" in broker,
    "sense_sortides_laterals": "sortides_estadistiques" not in broker and not (ROOT / "joanbot/institutional/sortides_estadistiques_netes.py").exists(),
    "gestio_v4_class": "GestioPosicioInstitucionalNetaV4Quant" in gestio,
    "get_core_v4": "GestioPosicioInstitucionalNetaV4Quant(db)" in gestio,
    "policy_compiler": "optimitza_politica" in gestio and "GRID_MFE_MAE_GIVEBACK_V4" in gestio,
    "tp3_final": "partial3_after_r" in gestio and "final_after_r" in gestio,
    "idempotencia": "accions_gestio_posicio_neta" in gestio and "reserva_accio" in gestio,
    "higiene": "neteja_contaminacio_prova" in gestio,
}

for table in [
    "accions_gestio_posicio_neta",
    "recerca_politica_gestio_posicio_neta",
    "higiene_gestio_posicio_neta",
]:
    try:
        db.query(f"SELECT COUNT(*) c FROM {table}")
        checks[f"taula_{table}"] = True
    except Exception:
        checks[f"taula_{table}"] = False

print("VALIDACIO_GESTIO_POSICIO_V4_QUANT")
print(json.dumps(checks, indent=2, sort_keys=True, ensure_ascii=False))

if not all(checks.values()):
    raise SystemExit(1)

print("VALIDACIO_GESTIO_POSICIO_V4_QUANT_OK")
''', encoding="utf-8")

(TOOLS / "neteja_contaminacio_gestio_posicio_v4.py").write_text(r'''
from pathlib import Path
import json, sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from joanbot.storage import get_db
from joanbot.institutional.gestio_posicio_institucional_neta import get_core

core = get_core(get_db())
print(json.dumps(core.neteja_contaminacio_prova(), indent=2, sort_keys=True, ensure_ascii=False))
''', encoding="utf-8")

(TOOLS / "panell_gestio_posicio_v4_quant.py").write_text(r'''
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from joanbot.storage import get_db
from joanbot.institutional.gestio_posicio_institucional_neta import get_core

core = get_core(get_db())
txt = core.report()
(ROOT / "live_export").mkdir(exist_ok=True)
(ROOT / "live_export" / "panell_gestio_posicio_v4_quant.txt").write_text(txt, encoding="utf-8")
print(txt)
''', encoding="utf-8")

print("INSTAL_LACIO_GESTIO_POSICIO_V4_QUANT_OK")
print("BACKUP:", BK)
