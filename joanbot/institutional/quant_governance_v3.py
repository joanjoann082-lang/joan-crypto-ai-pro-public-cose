from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Iterable, Tuple
import sqlite3, json, math, random, datetime, subprocess

ROOT = Path(__file__).resolve().parents[2]
DB = ROOT / "data" / "joanbot_v14.sqlite"
VERSION = "QUANT_GOVERNANCE_V3_INSTITUTIONAL"

BAD = ("PROVA", "TEST", "SINTETIC", "SYNTHETIC", "SELF_TEST", "PROVA_GESTIO")

def utc() -> str:
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

def jdump(x: Any) -> str:
    return json.dumps(x, sort_keys=True, ensure_ascii=False, default=str)

def contaminated(*xs: Any) -> bool:
    s = " ".join(str(x or "") for x in xs).upper()
    return any(b in s for b in BAD)

def mean(xs: Iterable[float]) -> float:
    xs = list(xs)
    return sum(xs) / len(xs) if xs else 0.0

def stdev(xs: Iterable[float]) -> float:
    xs = list(xs)
    if len(xs) < 2:
        return 0.0
    m = mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / max(1, len(xs) - 1))

def pf(xs: Iterable[float]) -> float:
    xs = list(xs)
    gw = sum(x for x in xs if x > 0)
    gl = abs(sum(x for x in xs if x < 0))
    if gl <= 1e-12:
        return 999.0 if gw > 0 else 0.0
    return gw / gl

def wr(xs: Iterable[float]) -> float:
    xs = list(xs)
    return sum(1 for x in xs if x > 0) / len(xs) if xs else 0.0

def max_dd(xs: Iterable[float]) -> float:
    eq = 0.0
    peak = 0.0
    worst = 0.0
    for x in xs:
        eq += x
        peak = max(peak, eq)
        worst = min(worst, eq - peak)
    return worst

def pctl(xs: Iterable[float], p: float, default: float = 0.0) -> float:
    xs = sorted([float(x) for x in xs if math.isfinite(float(x))])
    if not xs:
        return default
    if len(xs) == 1:
        return xs[0]
    k = (len(xs) - 1) * max(0.0, min(1.0, p))
    f = int(math.floor(k))
    c = int(math.ceil(k))
    if f == c:
        return xs[f]
    return xs[f] * (c - k) + xs[c] * (k - f)

def lcb(xs: List[float], z: float = 1.35) -> float:
    if not xs:
        return 0.0
    return mean(xs) - z * (stdev(xs) / math.sqrt(max(1, len(xs))))

def es95(xs: List[float]) -> float:
    losses = sorted([x for x in xs])
    if not losses:
        return 0.0
    k = max(1, int(len(losses) * 0.05))
    return mean(losses[:k])

def git_head() -> str:
    try:
        return subprocess.check_output(
            "git rev-parse HEAD",
            cwd=str(ROOT),
            shell=True,
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=5,
        ).strip()
    except Exception:
        return ""

class QuantGovernanceV3:
    def __init__(self, db_path: Path | str = DB):
        self.db_path = Path(db_path)
        self.ensure()

    def con(self):
        c = sqlite3.connect(str(self.db_path), timeout=60)
        c.row_factory = sqlite3.Row
        return c

    def ensure(self):
        with self.con() as con:
            con.executescript("""
            CREATE TABLE IF NOT EXISTS quant_governance_runs_v3(
                run_id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                version TEXT NOT NULL,
                git_head TEXT,
                config TEXT NOT NULL,
                payload TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS quant_governance_metrics_v3(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                run_id TEXT NOT NULL,
                key TEXT NOT NULL,
                font TEXT NOT NULL,
                n INTEGER NOT NULL,
                avg_r REAL NOT NULL,
                lcb_r REAL NOT NULL,
                pf REAL NOT NULL,
                winrate REAL NOT NULL,
                mdd_r REAL NOT NULL,
                es95_r REAL NOT NULL,
                std_r REAL NOT NULL,
                block_boot_p05 REAL NOT NULL,
                block_boot_p50 REAL NOT NULL,
                block_boot_p95 REAL NOT NULL,
                prob_mean_le_zero REAL NOT NULL,
                mc_end_p05 REAL NOT NULL,
                mc_mdd_p05 REAL NOT NULL,
                payload TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS quant_governance_walkforward_v3(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                run_id TEXT NOT NULL,
                key TEXT NOT NULL,
                font TEXT NOT NULL,
                fold INTEGER NOT NULL,
                train_n INTEGER NOT NULL,
                test_n INTEGER NOT NULL,
                train_avg REAL NOT NULL,
                test_avg REAL NOT NULL,
                test_pf REAL NOT NULL,
                test_mdd REAL NOT NULL,
                embargo_rows INTEGER NOT NULL,
                payload TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS quant_governance_cost_v3(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                run_id TEXT NOT NULL,
                key TEXT NOT NULL,
                font TEXT NOT NULL,
                cost_r REAL NOT NULL,
                n INTEGER NOT NULL,
                avg_net REAL NOT NULL,
                lcb_net REAL NOT NULL,
                pf_net REAL NOT NULL,
                mdd_net REAL NOT NULL,
                pass_net INTEGER NOT NULL,
                payload TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS quant_governance_decision_v3(
                key TEXT PRIMARY KEY,
                updated_at TEXT NOT NULL,
                run_id TEXT NOT NULL,
                version TEXT NOT NULL,
                state TEXT NOT NULL,
                grade TEXT NOT NULL,
                score REAL NOT NULL,
                q_value REAL NOT NULL,
                pbo_proxy REAL NOT NULL,
                live_n INTEGER NOT NULL,
                live_avg_net05 REAL NOT NULL,
                live_lcb_net05 REAL NOT NULL,
                live_pf_net05 REAL NOT NULL,
                live_stability REAL NOT NULL,
                forward_n INTEGER NOT NULL,
                forward_avg_net05 REAL NOT NULL,
                forward_lcb_net05 REAL NOT NULL,
                forward_pf_net05 REAL NOT NULL,
                forward_stability REAL NOT NULL,
                divergence_penalty REAL NOT NULL,
                reasons TEXT NOT NULL,
                payload TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS quant_governance_policy_v3(
                name TEXT PRIMARY KEY,
                updated_at TEXT NOT NULL,
                payload TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS quant_governance_audit_v3(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                event TEXT NOT NULL,
                severity TEXT NOT NULL,
                payload TEXT NOT NULL
            );
            """)
            con.commit()

            self.add_column_if_missing(con, "estat_promocio_quant", "governance_state", "TEXT")
            self.add_column_if_missing(con, "estat_promocio_quant", "governance_score", "REAL")
            self.add_column_if_missing(con, "estat_promocio_quant", "governance_payload", "TEXT")

            if not con.execute("SELECT 1 FROM quant_governance_policy_v3 WHERE name='default'").fetchone():
                policy = {
                    "min_live_candidate": 25,
                    "min_live_validable": 75,
                    "min_live_validated": 150,
                    "min_forward_explore": 500,
                    "cost_anchor_r": 0.05,
                    "max_q_candidate": 0.35,
                    "max_q_validable": 0.20,
                    "max_q_validated": 0.10,
                    "max_pbo_validated": 0.45,
                    "min_stability_validated": 0.60,
                    "live_weight": 0.72,
                    "forward_weight": 0.28,
                    "forward_only_max_state": "EXPLORAR",
                    "validated_requires_live": True
                }
                con.execute(
                    "INSERT OR REPLACE INTO quant_governance_policy_v3(name,updated_at,payload) VALUES(?,?,?)",
                    ("default", utc(), jdump(policy)),
                )
                con.commit()

    def add_column_if_missing(self, con, table: str, col: str, decl: str):
        try:
            cols = [r[1] for r in con.execute(f"PRAGMA table_info({table})").fetchall()]
            if col not in cols:
                con.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
                con.commit()
        except Exception:
            pass

    def audit(self, event: str, severity: str = "INFO", payload: Dict[str, Any] | None = None):
        with self.con() as con:
            con.execute(
                "INSERT INTO quant_governance_audit_v3(ts,event,severity,payload) VALUES(?,?,?,?)",
                (utc(), event, severity, jdump(payload or {})),
            )
            con.commit()

    def policy(self) -> Dict[str, Any]:
        with self.con() as con:
            r = con.execute("SELECT payload FROM quant_governance_policy_v3 WHERE name='default'").fetchone()
            return json.loads(r["payload"]) if r else {}

    def cols(self, table: str) -> List[str]:
        with self.con() as con:
            try:
                return [r[1] for r in con.execute(f"PRAGMA table_info({table})").fetchall()]
            except Exception:
                return []

    def table_exists(self, table: str) -> bool:
        with self.con() as con:
            return bool(con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            ).fetchone())

    def load_results(self) -> List[Dict[str, Any]]:
        if not self.table_exists("resultats_quant_nets"):
            return []

        cols = self.cols("resultats_quant_nets")
        r_col = next((c for c in ["resultat_r", "pnl_r", "r", "R"] if c in cols), None)
        if not r_col:
            return []

        order_col = next((c for c in ["ts", "exit_ts", "closed_at", "created_at", "id"] if c in cols), "rowid")

        sql = "SELECT rowid AS _rowid, * FROM resultats_quant_nets"
        where = []
        if "qualitat" in cols:
            where.append("qualitat='NET'")
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += f" ORDER BY {order_col} ASC LIMIT 200000"

        out = []
        with self.con() as con:
            for raw in con.execute(sql).fetchall():
                d = dict(raw)
                if contaminated(d.get("payload"), d.get("setup"), d.get("motiu")):
                    continue

                rr = num(d.get(r_col), None)
                if rr is None:
                    continue

                risk_usd = num(d.get("risk_usd"))
                fees = abs(num(d.get("fees")))
                size = abs(num(d.get("size_usd")))
                fee_cost_r = fees / risk_usd if risk_usd > 0 else 0.0

                d["_r"] = rr
                d["_fee_cost_r"] = fee_cost_r
                d["_notional"] = size
                d["_font"] = str(d.get("font") or d.get("source") or "UNKNOWN").upper()
                d["_symbol"] = str(d.get("symbol") or "UNKNOWN").upper()
                d["_side"] = str(d.get("side") or "UNKNOWN").upper()
                d["_setup"] = str(d.get("setup") or "UNKNOWN").upper()
                d["_regime"] = str(d.get("regime") or "UNKNOWN").upper()
                d["_session"] = str(d.get("session") or "UNKNOWN").upper()
                d["_vol"] = str(d.get("volatility_bucket") or "UNKNOWN").upper()
                d["_idx"] = int(d.get("_rowid") or len(out))
                out.append(d)

        return out

    def keys_for(self, r: Dict[str, Any]) -> List[str]:
        sym, side, setup = r["_symbol"], r["_side"], r["_setup"]
        regime, session, vol = r["_regime"], r["_session"], r["_vol"]
        keys = [
            "GLOBAL",
            f"SIDE|{side}",
            f"SYM_SIDE|{sym}|{side}",
            f"SETUP|{setup}",
            f"SYM_SIDE_SETUP|{sym}|{side}|{setup}",
            f"REGIME_SIDE|{regime}|{side}",
            f"REGIME_SETUP|{regime}|{setup}",
            f"REGIME_SIDE_SETUP|{regime}|{side}|{setup}",
            f"SESSION_SIDE|{session}|{side}",
            f"VOL_SIDE|{vol}|{side}",
        ]
        return [k for k in keys if not contaminated(k)]

    def block_bootstrap(self, xs: List[float], n_boot: int = 700) -> Dict[str, float]:
        if not xs:
            return {"p05": 0.0, "p50": 0.0, "p95": 0.0, "p_le0": 1.0}

        rng = random.Random(10101 + len(xs))
        n = len(xs)
        block = max(1, int(math.sqrt(n)))
        vals = []
        for _ in range(n_boot):
            sample = []
            while len(sample) < n:
                start = rng.randrange(n)
                for j in range(block):
                    sample.append(xs[(start + j) % n])
                    if len(sample) >= n:
                        break
            vals.append(mean(sample))

        return {
            "p05": pctl(vals, 0.05),
            "p50": pctl(vals, 0.50),
            "p95": pctl(vals, 0.95),
            "p_le0": sum(1 for v in vals if v <= 0.0) / len(vals),
        }

    def monte_carlo_blocks(self, xs: List[float], n_mc: int = 700) -> Dict[str, float]:
        if not xs:
            return {"end_p05": 0.0, "mdd_p05": 0.0}

        rng = random.Random(20202 + len(xs))
        n = len(xs)
        block = max(1, int(math.sqrt(n)))
        blocks = [xs[i:i+block] for i in range(0, n, block)]
        ends, mdds = [], []

        for _ in range(n_mc):
            seq = []
            while len(seq) < n:
                seq.extend(rng.choice(blocks))
            seq = seq[:n]
            ends.append(sum(seq))
            mdds.append(max_dd(seq))

        return {
            "end_p05": pctl(ends, 0.05),
            "mdd_p05": pctl(mdds, 0.05),
        }

    def metrics(self, xs: List[float]) -> Dict[str, float]:
        b = self.block_bootstrap(xs)
        mc = self.monte_carlo_blocks(xs)
        return {
            "n": len(xs),
            "avg_r": mean(xs),
            "lcb_r": lcb(xs),
            "pf": pf(xs),
            "winrate": wr(xs),
            "mdd_r": max_dd(xs),
            "es95_r": es95(xs),
            "std_r": stdev(xs),
            "block_boot_p05": b["p05"],
            "block_boot_p50": b["p50"],
            "block_boot_p95": b["p95"],
            "prob_mean_le_zero": b["p_le0"],
            "mc_end_p05": mc["end_p05"],
            "mc_mdd_p05": mc["mdd_p05"],
        }

    def walkforward(self, xs: List[float], folds: int = 6) -> Dict[str, Any]:
        n = len(xs)
        if n < 24:
            return {"folds": [], "stability": 0.0, "pbo_proxy": 1.0, "reason": "N_INSUFICIENT"}

        folds = min(folds, max(2, n // 10))
        fold_size = max(1, n // folds)
        embargo = max(2, int(n * 0.04))
        rows = []

        for i in range(folds):
            a = i * fold_size
            b = n if i == folds - 1 else min(n, (i + 1) * fold_size)

            test = xs[a:b]
            train = xs[:max(0, a - embargo)] + xs[min(n, b + embargo):]

            if len(test) < 3 or len(train) < 5:
                continue

            rows.append({
                "fold": i,
                "train_n": len(train),
                "test_n": len(test),
                "train_avg": mean(train),
                "test_avg": mean(test),
                "test_pf": pf(test),
                "test_mdd": max_dd(test),
                "embargo_rows": embargo,
            })

        if not rows:
            return {"folds": [], "stability": 0.0, "pbo_proxy": 1.0, "reason": "NO_VALID_FOLDS"}

        good = sum(1 for r in rows if r["test_avg"] > 0 and r["test_pf"] >= 1.0)
        stability = good / len(rows)
        divergence = mean([abs(r["train_avg"] - r["test_avg"]) for r in rows])
        pbo_proxy = max(0.0, min(1.0, (1.0 - stability) + min(0.5, divergence)))

        return {
            "folds": rows,
            "stability": stability,
            "pbo_proxy": pbo_proxy,
            "reason": "OK",
        }

    def cost_grid(self, rows: List[Dict[str, Any]]) -> List[float]:
        observed = [num(r.get("_fee_cost_r")) for r in rows if num(r.get("_fee_cost_r")) > 0]
        base = [0.0, 0.02, 0.05, 0.10, 0.15]
        if observed:
            base.extend([
                pctl(observed, 0.50, 0.02),
                pctl(observed, 0.75, 0.05),
                pctl(observed, 0.90, 0.10),
            ])
        return sorted(set(round(max(0.0, min(0.30, x)), 5) for x in base))

    def apply_cost(self, xs: List[float], cost: float) -> Dict[str, Any]:
        ys = [x - cost for x in xs]
        return {
            "n": len(ys),
            "avg_net": mean(ys),
            "lcb_net": lcb(ys),
            "pf_net": pf(ys),
            "mdd_net": max_dd(ys),
            "pass_net": int(len(ys) >= 5 and mean(ys) > 0 and pf(ys) >= 1.0 and lcb(ys) > -0.05),
        }

    def fdr_q_values(self, pvals: Dict[str, float]) -> Dict[str, float]:
        items = sorted(pvals.items(), key=lambda kv: kv[1])
        m = max(1, len(items))
        q = {}
        prev = 1.0
        for rank_rev, (key, p) in enumerate(reversed(items), start=1):
            rank = m - rank_rev + 1
            val = min(prev, p * m / max(1, rank))
            q[key] = val
            prev = val
        return q

    def state_for(self, key: str, live: Dict[str, Any], fwd: Dict[str, Any], live_wf: Dict[str, Any], fwd_wf: Dict[str, Any], live_cost: Dict[str, Any], fwd_cost: Dict[str, Any], q_value: float) -> Dict[str, Any]:
        pol = self.policy()

        live_n = int(live.get("n", 0))
        fwd_n = int(fwd.get("n", 0))

        live_avg = num(live_cost.get("avg_net"))
        live_lcb = num(live_cost.get("lcb_net"))
        live_pf = num(live_cost.get("pf_net"))
        live_stab = num(live_wf.get("stability"))
        fwd_avg = num(fwd_cost.get("avg_net"))
        fwd_lcb = num(fwd_cost.get("lcb_net"))
        fwd_pf = num(fwd_cost.get("pf_net"))
        fwd_stab = num(fwd_wf.get("stability"))

        pbo = max(num(live_wf.get("pbo_proxy"), 1.0), num(fwd_wf.get("pbo_proxy"), 1.0))
        divergence = abs(live_avg - fwd_avg) if live_n and fwd_n else 0.0

        score = (
            num(pol.get("live_weight", 0.72)) * (2.6 * live_lcb + 1.0 * live_avg + 0.25 * min(live_pf, 3.0) + 0.25 * live_stab)
            + num(pol.get("forward_weight", 0.28)) * (1.2 * fwd_lcb + 0.6 * fwd_avg + 0.12 * min(fwd_pf, 3.0) + 0.12 * fwd_stab)
            - 0.45 * pbo
            - 0.25 * divergence
            - 0.35 * q_value
        )

        reasons = []
        state = "RECERCA"
        grade = "D"

        if live_n >= 8 and (live_avg < -0.05 or live_pf < 0.85 or live_lcb < -0.12):
            state = "QUARANTENA"
            grade = "F"
            reasons.append("LIVE_NET_NEGATIU")

        elif (
            live_n >= int(pol.get("min_live_validated", 150))
            and live_lcb > 0.02
            and live_pf >= 1.20
            and live_stab >= num(pol.get("min_stability_validated", 0.60))
            and pbo <= num(pol.get("max_pbo_validated", 0.45))
            and q_value <= num(pol.get("max_q_validated", 0.10))
        ):
            state = "VALIDAT"
            grade = "A"
            reasons.append("LIVE_ROBUST_VALIDAT")

        elif (
            live_n >= int(pol.get("min_live_validable", 75))
            and live_avg > 0.02
            and live_lcb > -0.01
            and live_pf >= 1.10
            and live_stab >= 0.50
            and q_value <= num(pol.get("max_q_validable", 0.20))
        ):
            state = "VALIDABLE"
            grade = "B"
            reasons.append("LIVE_VALIDABLE")

        elif (
            live_n >= int(pol.get("min_live_candidate", 25))
            and live_avg > 0.0
            and live_pf >= 1.00
            and q_value <= num(pol.get("max_q_candidate", 0.35))
        ):
            state = "CANDIDAT"
            grade = "C"
            reasons.append("LIVE_CANDIDAT")

        elif (
            fwd_n >= int(pol.get("min_forward_explore", 500))
            and fwd_avg > 0
            and fwd_pf >= 1.05
            and fwd_stab >= 0.45
        ):
            state = "EXPLORAR"
            grade = "C-"
            reasons.append("FORWARD_ROBUST_SENSE_LIVE")

        else:
            reasons.append("EVIDENCIA_INSUFICIENT")

        if live_n < 30:
            reasons.append("LIVE_N_BAIX")
        if fwd_n < 300:
            reasons.append("FORWARD_N_BAIX")
        if pbo > 0.60:
            reasons.append("PBO_ALT")
        if q_value > 0.35:
            reasons.append("MULTIPLE_TEST_PENALTY")
        if live_n and fwd_n and divergence > 0.25:
            reasons.append("LIVE_FORWARD_DIVERGENCIA")

        return {
            "state": state,
            "grade": grade,
            "score": score,
            "q_value": q_value,
            "pbo_proxy": pbo,
            "live_n": live_n,
            "live_avg": live_avg,
            "live_lcb": live_lcb,
            "live_pf": live_pf,
            "live_stability": live_stab,
            "forward_n": fwd_n,
            "forward_avg": fwd_avg,
            "forward_lcb": fwd_lcb,
            "forward_pf": fwd_pf,
            "forward_stability": fwd_stab,
            "divergence_penalty": divergence,
            "reasons": reasons,
        }

    def run(self) -> Dict[str, Any]:
        rows = self.load_results()
        run_id = "QGOV3_" + datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d_%H%M%S")
        grid = self.cost_grid(rows)
        cost_anchor = min(grid, key=lambda x: abs(x - 0.05)) if grid else 0.05

        grouped_rows: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
        for r in rows:
            font = r["_font"]
            for key in self.keys_for(r):
                grouped_rows.setdefault(key, {}).setdefault(font, []).append(r)

        pvals = {}
        cache = {}

        for key, by_font in grouped_rows.items():
            live_xs = [r["_r"] for r in by_font.get("LIVE", [])]
            fwd_rows = by_font.get("FORWARD", by_font.get("FWD", []))
            fwd_xs = [r["_r"] for r in fwd_rows]
            xs_for_p = live_xs if len(live_xs) >= 8 else fwd_xs
            pvals[key] = self.block_bootstrap(xs_for_p)["p_le0"] if xs_for_p else 1.0

        qvals = self.fdr_q_values(pvals)

        with self.con() as con:
            con.execute(
                "INSERT OR REPLACE INTO quant_governance_runs_v3(run_id,created_at,version,git_head,config,payload) VALUES(?,?,?,?,?,?)",
                (run_id, utc(), VERSION, git_head(), jdump({"cost_grid": grid, "cost_anchor": cost_anchor, "policy": self.policy()}), jdump({"rows": len(rows), "keys": len(grouped_rows)})),
            )

            con.execute("DELETE FROM quant_governance_decision_v3")

            metric_n = cost_n = wf_n = 0

            for key, by_font in grouped_rows.items():
                per_font_metrics = {}
                per_font_wf = {}
                per_font_cost_anchor = {}

                for font, rs in by_font.items():
                    xs = [r["_r"] for r in rs]
                    m = self.metrics(xs)
                    wf = self.walkforward(xs)
                    cm_anchor = self.apply_cost(xs, cost_anchor)

                    per_font_metrics[font] = m
                    per_font_wf[font] = wf
                    per_font_cost_anchor[font] = cm_anchor

                    con.execute("""
                    INSERT INTO quant_governance_metrics_v3(
                        ts,run_id,key,font,n,avg_r,lcb_r,pf,winrate,mdd_r,es95_r,std_r,
                        block_boot_p05,block_boot_p50,block_boot_p95,prob_mean_le_zero,
                        mc_end_p05,mc_mdd_p05,payload
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """, (
                        utc(), run_id, key, font, int(m["n"]), m["avg_r"], m["lcb_r"], m["pf"], m["winrate"],
                        m["mdd_r"], m["es95_r"], m["std_r"], m["block_boot_p05"], m["block_boot_p50"],
                        m["block_boot_p95"], m["prob_mean_le_zero"], m["mc_end_p05"], m["mc_mdd_p05"],
                        jdump({"version": VERSION}),
                    ))
                    metric_n += 1

                    for fold in wf.get("folds", []):
                        con.execute("""
                        INSERT INTO quant_governance_walkforward_v3(
                            ts,run_id,key,font,fold,train_n,test_n,train_avg,test_avg,test_pf,test_mdd,embargo_rows,payload
                        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                        """, (
                            utc(), run_id, key, font, int(fold["fold"]), int(fold["train_n"]), int(fold["test_n"]),
                            fold["train_avg"], fold["test_avg"], fold["test_pf"], fold["test_mdd"], int(fold["embargo_rows"]),
                            jdump({"version": VERSION}),
                        ))
                        wf_n += 1

                    for cost in grid:
                        cm = self.apply_cost(xs, cost)
                        con.execute("""
                        INSERT INTO quant_governance_cost_v3(
                            ts,run_id,key,font,cost_r,n,avg_net,lcb_net,pf_net,mdd_net,pass_net,payload
                        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                        """, (
                            utc(), run_id, key, font, cost, int(cm["n"]), cm["avg_net"], cm["lcb_net"], cm["pf_net"],
                            cm["mdd_net"], int(cm["pass_net"]), jdump({"version": VERSION}),
                        ))
                        cost_n += 1

                live = per_font_metrics.get("LIVE", {"n": 0})
                fwd = per_font_metrics.get("FORWARD", per_font_metrics.get("FWD", {"n": 0}))
                live_wf = per_font_wf.get("LIVE", {"stability": 0.0, "pbo_proxy": 1.0})
                fwd_wf = per_font_wf.get("FORWARD", per_font_wf.get("FWD", {"stability": 0.0, "pbo_proxy": 1.0}))
                live_cost = per_font_cost_anchor.get("LIVE", {"avg_net": 0.0, "lcb_net": 0.0, "pf_net": 0.0})
                fwd_cost = per_font_cost_anchor.get("FORWARD", per_font_cost_anchor.get("FWD", {"avg_net": 0.0, "lcb_net": 0.0, "pf_net": 0.0}))

                decision = self.state_for(key, live, fwd, live_wf, fwd_wf, live_cost, fwd_cost, qvals.get(key, 1.0))

                con.execute("""
                INSERT OR REPLACE INTO quant_governance_decision_v3(
                    key,updated_at,run_id,version,state,grade,score,q_value,pbo_proxy,
                    live_n,live_avg_net05,live_lcb_net05,live_pf_net05,live_stability,
                    forward_n,forward_avg_net05,forward_lcb_net05,forward_pf_net05,forward_stability,
                    divergence_penalty,reasons,payload
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (
                    key, utc(), run_id, VERSION, decision["state"], decision["grade"], decision["score"], decision["q_value"], decision["pbo_proxy"],
                    int(decision["live_n"]), decision["live_avg"], decision["live_lcb"], decision["live_pf"], decision["live_stability"],
                    int(decision["forward_n"]), decision["forward_avg"], decision["forward_lcb"], decision["forward_pf"], decision["forward_stability"],
                    decision["divergence_penalty"], jdump(decision["reasons"]), jdump(decision),
                ))

                try:
                    con.execute("""
                    UPDATE estat_promocio_quant
                    SET governance_state=?, governance_score=?, governance_payload=?
                    WHERE key=?
                    """, (decision["state"], decision["score"], jdump(decision), key))
                except Exception:
                    pass

            con.commit()

        self.audit("RUN_QGOV3_OK", "INFO", {
            "run_id": run_id,
            "rows": len(rows),
            "keys": len(grouped_rows),
            "metric_rows": metric_n,
            "cost_rows": cost_n,
            "wf_rows": wf_n,
        })

        return {
            "run_id": run_id,
            "rows": len(rows),
            "keys": len(grouped_rows),
            "metric_rows": metric_n,
            "cost_rows": cost_n,
            "wf_rows": wf_n,
        }

    def report(self) -> str:
        lines = []
        lines.append("===== QUANT GOVERNANCE V3 INSTITUTIONAL =====")
        lines.append("UTC: " + utc())

        with self.con() as con:
            for t in [
                "quant_governance_runs_v3",
                "quant_governance_metrics_v3",
                "quant_governance_walkforward_v3",
                "quant_governance_cost_v3",
                "quant_governance_decision_v3",
                "quant_governance_policy_v3",
                "quant_governance_audit_v3",
            ]:
                try:
                    n = con.execute(f"SELECT COUNT(*) c FROM {t}").fetchone()["c"]
                except Exception:
                    n = "ERR"
                lines.append(f"{t}: {n}")

            lines.append("")
            lines.append("===== STATES =====")
            for r in con.execute("""
                SELECT state, grade, COUNT(*) n,
                       ROUND(AVG(score),4) avg_score,
                       ROUND(AVG(live_avg_net05),4) live_avg,
                       ROUND(AVG(forward_avg_net05),4) fwd_avg,
                       ROUND(AVG(q_value),4) q,
                       ROUND(AVG(pbo_proxy),4) pbo
                FROM quant_governance_decision_v3
                GROUP BY state, grade
                ORDER BY n DESC
            """).fetchall():
                lines.append(jdump(dict(r)))

            lines.append("")
            lines.append("===== TOP ALLOWED RESEARCH LADDER =====")
            for r in con.execute("""
                SELECT key,state,grade,ROUND(score,4) score,ROUND(q_value,4) q,
                       ROUND(pbo_proxy,3) pbo,live_n,
                       ROUND(live_avg_net05,4) live_avg,
                       ROUND(live_lcb_net05,4) live_lcb,
                       ROUND(live_pf_net05,3) live_pf,
                       ROUND(live_stability,3) live_stab,
                       forward_n,
                       ROUND(forward_avg_net05,4) fwd_avg,
                       ROUND(forward_lcb_net05,4) fwd_lcb,
                       ROUND(forward_pf_net05,3) fwd_pf,
                       ROUND(forward_stability,3) fwd_stab,
                       reasons
                FROM quant_governance_decision_v3
                WHERE state!='QUARANTENA'
                ORDER BY
                    CASE state WHEN 'VALIDAT' THEN 0 WHEN 'VALIDABLE' THEN 1 WHEN 'CANDIDAT' THEN 2 WHEN 'EXPLORAR' THEN 3 ELSE 4 END,
                    score DESC
                LIMIT 40
            """).fetchall():
                lines.append(jdump(dict(r)))

            lines.append("")
            lines.append("===== QUARANTENA / REJECT =====")
            for r in con.execute("""
                SELECT key,state,grade,ROUND(score,4) score,ROUND(q_value,4) q,
                       ROUND(pbo_proxy,3) pbo,live_n,
                       ROUND(live_avg_net05,4) live_avg,
                       ROUND(live_lcb_net05,4) live_lcb,
                       ROUND(live_pf_net05,3) live_pf,
                       reasons
                FROM quant_governance_decision_v3
                WHERE state='QUARANTENA'
                ORDER BY live_n DESC, live_avg_net05 ASC
                LIMIT 40
            """).fetchall():
                lines.append(jdump(dict(r)))

        return "\n".join(lines)

def get_governance(db_path: Path | str = DB) -> QuantGovernanceV3:
    return QuantGovernanceV3(db_path)
