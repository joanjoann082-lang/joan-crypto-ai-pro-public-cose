from __future__ import annotations

import json
import math
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple

DB_PATH = "data/joanbot_v14.sqlite"
VERSION = "INSTITUTIONAL_RESEARCH_BRAIN_V13_0"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def mean(v: List[float]) -> float:
    return sum(v) / len(v) if v else 0.0


def stdev(v: List[float]) -> float:
    if len(v) < 2:
        return 0.0
    m = mean(v)
    return math.sqrt(sum((x - m) ** 2 for x in v) / (len(v) - 1))


def lcb(v: List[float], z: float = 1.28) -> float:
    if not v:
        return 0.0
    if len(v) == 1:
        return v[0] - 0.50
    return mean(v) - z * stdev(v) / math.sqrt(len(v))


def profit_factor(v: List[float]) -> float:
    gp = sum(x for x in v if x > 0)
    gl = abs(sum(x for x in v if x < 0))
    if gl <= 0:
        return 99.0 if gp > 0 else 0.0
    return gp / gl


def max_drawdown(v: List[float]) -> float:
    peak = 0.0
    cum = 0.0
    dd = 0.0
    for x in v:
        cum += x
        peak = max(peak, cum)
        dd = min(dd, cum - peak)
    return dd


def downside_tail(v: List[float]) -> float:
    if not v:
        return 0.0
    s = sorted(v)
    k = max(1, int(len(s) * 0.10))
    return mean(s[:k])


class InstitutionalResearchBrainV13:
    """
    Research brain institucional.

    Responsabilitat:
    - construir univers de candidats
    - validar amb walk-forward purgat / rolling windows
    - aplicar posterior bayesià jeràrquic
    - penalitzar decay, tail risk, crowding i live underperformance
    - escriure un contracte quantitatiu final
    - NO obrir trades
    - NO tocar decisions/positions/trades legacy
    """

    def __init__(self, db_path: str = DB_PATH) -> None:
        self.db_path = db_path

    def connect(self):
        con = sqlite3.connect(self.db_path, timeout=30)
        con.row_factory = sqlite3.Row
        return con

    def exists(self, cur, name: str) -> bool:
        return cur.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE name=?",
            (name,),
        ).fetchone()[0] > 0

    def cols(self, cur, table: str) -> List[str]:
        try:
            return [r[1] for r in cur.execute(f"PRAGMA table_info({table})").fetchall()]
        except Exception:
            return []

    def ensure_schema(self, cur) -> None:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS research_brain_candidates_v13 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                version TEXT NOT NULL,
                symbol TEXT,
                side TEXT,
                setup TEXT,
                profile TEXT,
                horizon_min INTEGER,
                source_edge_id INTEGER,
                research_state TEXT NOT NULL,
                research_score REAL NOT NULL,
                posterior_mean_r REAL NOT NULL,
                posterior_lcb_r REAL NOT NULL,
                wf_score REAL NOT NULL,
                stability_score REAL NOT NULL,
                live_score REAL NOT NULL,
                derivatives_score REAL NOT NULL,
                tail_risk_score REAL NOT NULL,
                risk_budget_score REAL NOT NULL,
                recommended_size_mult REAL NOT NULL,
                hard_vetoes TEXT NOT NULL,
                payload TEXT NOT NULL
            );
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS research_brain_contract_v13 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                version TEXT NOT NULL,
                contract_state TEXT NOT NULL,
                selected_candidate_id INTEGER,
                selected_symbol TEXT,
                selected_side TEXT,
                selected_setup TEXT,
                selected_profile TEXT,
                selected_horizon_min INTEGER,
                contract_score REAL NOT NULL,
                policy TEXT NOT NULL,
                size_mult REAL NOT NULL,
                hard_vetoes TEXT NOT NULL,
                payload TEXT NOT NULL
            );
        """)

        cur.execute("DROP VIEW IF EXISTS latest_research_brain_contract_v13;")
        cur.execute("""
            CREATE VIEW latest_research_brain_contract_v13 AS
            SELECT *
            FROM research_brain_contract_v13
            ORDER BY id DESC
            LIMIT 1;
        """)

        cur.execute("DROP VIEW IF EXISTS latest_research_brain_candidates_v13;")
        cur.execute("""
            CREATE VIEW latest_research_brain_candidates_v13 AS
            SELECT *
            FROM research_brain_candidates_v13
            ORDER BY id DESC
            LIMIT 25;
        """)

    def latest(self, cur, name: str) -> Dict[str, Any]:
        if not self.exists(cur, name):
            return {}
        try:
            row = cur.execute(f"SELECT * FROM {name} LIMIT 1;").fetchone()
            return dict(row) if row else {}
        except Exception:
            return {}

    def edge_universe(self, cur) -> List[Dict[str, Any]]:
        if not self.exists(cur, "edge_robustness_validator_v9"):
            d = self.latest(cur, "latest_edge_robustness_validator_v9")
            return [d] if d else []

        rows = cur.execute("""
            SELECT *
            FROM edge_robustness_validator_v9
            ORDER BY id DESC
            LIMIT 300;
        """).fetchall()

        best: Dict[Tuple[Any, ...], Dict[str, Any]] = {}

        for r in rows:
            d = dict(r)
            key = (
                d.get("symbol"),
                d.get("side"),
                d.get("setup"),
                d.get("profile"),
                d.get("horizon_min"),
            )
            score = fnum(d.get("robustness_score")) + fnum(d.get("lcb_r")) * 100
            old = best.get(key)
            old_score = fnum(old.get("robustness_score")) + fnum(old.get("lcb_r")) * 100 if old else -999
            if score > old_score:
                best[key] = d

        return sorted(
            best.values(),
            key=lambda x: (
                fnum(x.get("canary_permission")),
                fnum(x.get("robustness_score")),
                fnum(x.get("lcb_r")),
                fnum(x.get("avg_r")),
            ),
            reverse=True,
        )[:40]

    def shadow_outcomes_for_candidate(self, cur, cand: Dict[str, Any]) -> List[float]:
        """
        Busca R real a universal_shadow_results_v2 si existeix.
        Si no hi ha columna R usable, retorna [] i després usem proxy edge.
        """
        table = "universal_shadow_results_v2"
        if not self.exists(cur, table):
            return []

        c = self.cols(cur, table)
        r_col = None
        for x in ["net_pnl_r", "pnl_r", "result_r", "outcome_r", "realized_r", "r"]:
            if x in c:
                r_col = x
                break

        if not r_col:
            return []

        filters = []
        params = []

        for col in ["symbol", "side", "setup", "profile", "horizon_min"]:
            if col in c and cand.get(col) not in (None, ""):
                filters.append(f"{col}=?")
                params.append(cand.get(col))

        where = "WHERE " + " AND ".join(filters) if filters else ""

        try:
            rows = cur.execute(
                f"""
                SELECT {r_col} AS r
                FROM {table}
                {where}
                ORDER BY rowid ASC
                LIMIT 1000;
                """,
                params,
            ).fetchall()
        except Exception:
            return []

        return [fnum(dict(r).get("r")) for r in rows]

    def purged_walk_forward(self, vals: List[float], cand: Dict[str, Any]) -> Dict[str, Any]:
        if len(vals) < 30:
            return {
                "mode": "WF_PROXY_FROM_EDGE",
                "n": len(vals),
                "score": 45.0,
                "hard_vetoes": ["WF_REAL_SAMPLE_LT_30"],
            }

        n = len(vals)
        folds = []
        embargo = max(2, int(n * 0.03))

        # 5 rolling/purged folds
        for i in range(5):
            start = int(n * (0.10 + i * 0.12))
            end = int(n * (0.45 + i * 0.10))
            test_start = min(n, end + embargo)
            test_end = min(n, test_start + max(10, int(n * 0.15)))

            train = vals[:start] + vals[start:end]
            test = vals[test_start:test_end]

            if len(train) < 10 or len(test) < 5:
                continue

            folds.append({
                "train_n": len(train),
                "test_n": len(test),
                "train_exp": mean(train),
                "test_exp": mean(test),
                "test_lcb": lcb(test),
                "test_pf": profit_factor(test),
                "test_dd": max_drawdown(test),
                "test_tail": downside_tail(test),
            })

        if not folds:
            return {
                "mode": "WF_NO_VALID_FOLDS",
                "n": n,
                "score": 25.0,
                "hard_vetoes": ["WF_NO_VALID_FOLDS"],
            }

        test_exps = [f["test_exp"] for f in folds]
        test_lcbs = [f["test_lcb"] for f in folds]
        test_pfs = [f["test_pf"] for f in folds]
        test_tails = [f["test_tail"] for f in folds]

        positive_folds = sum(1 for x in test_exps if x > 0)
        fold_pass_rate = positive_folds / len(folds)

        degradation = 0.0
        train_avg = mean([f["train_exp"] for f in folds])
        test_avg = mean(test_exps)
        if train_avg > 0:
            degradation = max(0.0, (train_avg - test_avg) / max(abs(train_avg), 0.0001))

        score = 50.0
        score += clamp(test_avg * 220, -35, 35)
        score += clamp(mean(test_lcbs) * 260, -35, 35)
        score += clamp((mean(test_pfs) - 1.0) * 20, -20, 25)
        score += clamp((fold_pass_rate - 0.50) * 40, -20, 20)
        score -= clamp(degradation * 30, 0, 35)
        score += clamp(mean(test_tails) * 50, -30, 10)
        score = clamp(score, 0, 100)

        hard = []
        if fold_pass_rate < 0.60:
            hard.append("WF_FOLD_PASS_RATE_LT_60")
        if mean(test_lcbs) < -0.03:
            hard.append("WF_MEAN_TEST_LCB_NEGATIVE")
        if degradation > 1.0:
            hard.append("WF_DEGRADATION_GT_100PCT")
        if mean(test_tails) < -0.75:
            hard.append("WF_TAIL_TOO_NEGATIVE")

        return {
            "mode": "PURGED_ROLLING_WF",
            "n": n,
            "folds": folds,
            "fold_pass_rate": fold_pass_rate,
            "test_exp": test_avg,
            "test_lcb": mean(test_lcbs),
            "test_pf": mean(test_pfs),
            "degradation": degradation,
            "tail": mean(test_tails),
            "score": score,
            "hard_vetoes": hard,
        }

    def bayesian_hierarchical_edge(self, cand: Dict[str, Any], vals: List[float]) -> Dict[str, Any]:
        n = fnum(cand.get("n"))
        avg_r = fnum(cand.get("avg_r"))
        lcb_r = fnum(cand.get("lcb_r"))
        worst = fnum(cand.get("worst_r"))
        r20 = fnum(cand.get("recent20_avg_r"))
        r50 = fnum(cand.get("recent50_avg_r"))
        r50_lcb = fnum(cand.get("recent50_lcb_r"))
        robustness = fnum(cand.get("robustness_score"))

        if vals:
            n = max(n, len(vals))
            avg_r = mean(vals)
            lcb_r = lcb(vals)
            worst = min(vals)
            r20 = mean(vals[-20:]) if len(vals) >= 20 else mean(vals)
            r50 = mean(vals[-50:]) if len(vals) >= 50 else mean(vals)
            r50_lcb = lcb(vals[-50:]) if len(vals) >= 10 else lcb(vals)

        # Prior jeràrquic conservador: el bot ha de demostrar edge.
        prior_n = 80.0
        prior_mean = 0.0

        post_mean = ((avg_r * n) + (prior_mean * prior_n)) / max(n + prior_n, 1.0)

        sigma = abs(avg_r - lcb_r) * math.sqrt(max(n, 1.0)) / 1.28 if n > 1 else 0.40
        sigma = clamp(sigma, 0.05, 1.75)

        post_se = sigma / math.sqrt(max(n + prior_n, 1.0))
        post_lcb = post_mean - 1.28 * post_se

        decay = 0.0
        if avg_r > 0:
            decay = max(0.0, (avg_r - r20) / max(abs(avg_r), 0.0001))

        edge_quality = 50.0
        edge_quality += clamp(post_lcb * 420, -55, 50)
        edge_quality += clamp(robustness * 0.20, 0, 20)
        edge_quality += clamp(r50_lcb * 180, -20, 20)
        edge_quality -= clamp(decay * 30, 0, 35)
        edge_quality = clamp(edge_quality, 0, 100)

        hard = []
        if n < 30:
            hard.append("BAYES_N_LT_30")
        if post_lcb <= 0:
            hard.append("BAYES_POST_LCB_NOT_POSITIVE")
        if r50_lcb < -0.08:
            hard.append("BAYES_R50_LCB_TOO_NEGATIVE")
        if worst < -1.25:
            hard.append("BAYES_WORST_LT_MINUS_1_25R")
        if decay > 1.25:
            hard.append("BAYES_DECAY_TOO_HIGH")

        return {
            "n": n,
            "avg_r": avg_r,
            "lcb_r": lcb_r,
            "worst_r": worst,
            "r20": r20,
            "r50": r50,
            "r50_lcb": r50_lcb,
            "sigma": sigma,
            "posterior_mean": post_mean,
            "posterior_lcb": post_lcb,
            "posterior_se": post_se,
            "decay": decay,
            "edge_quality": edge_quality,
            "hard_vetoes": hard,
        }

    def derivatives_score(self, cur, cand: Dict[str, Any]) -> Dict[str, Any]:
        deriv = self.latest(cur, "latest_derivatives_regime_v10")
        if not deriv:
            return {
                "state": "DERIVATIVES_UNAVAILABLE",
                "score": 35.0,
                "hard_vetoes": [],
            }

        cand_side = str(cand.get("side") or "").upper()
        selected_side = str(deriv.get("selected_side") or "").upper()
        state = str(deriv.get("derivatives_state") or "")
        confidence = fnum(deriv.get("confidence_score"))
        selected_score = fnum(deriv.get("selected_score"))
        opposite_score = fnum(deriv.get("opposite_score"))

        veto = deriv.get("veto_canary") in {1, "1", True, "true"}

        score = confidence
        hard = []

        if veto:
            hard.append("DERIVATIVES_VETO")
            score = 0

        if selected_side and selected_side != cand_side:
            hard.append("DERIVATIVES_SIDE_CONFLICT")
            score = min(score, 25)

        if "CONFLICT" in state:
            hard.append("DERIVATIVES_CONFLICT_STATE")
            score = min(score, 25)

        if opposite_score > selected_score + 20:
            hard.append("DERIVATIVES_OPPOSITE_DOMINANT")
            score = min(score, 35)

        return {
            "state": state,
            "selected_side": selected_side,
            "score": clamp(score, 0, 100),
            "confidence": confidence,
            "selected_score": selected_score,
            "opposite_score": opposite_score,
            "hard_vetoes": hard,
            "reasons": deriv.get("reasons"),
        }

    def live_score(self, cur, cand: Dict[str, Any]) -> Dict[str, Any]:
        if not self.exists(cur, "paper_micro_canary_positions_v11"):
            return {
                "closed_n": 0,
                "score": 20.0,
                "hard_vetoes": [],
            }

        rows = cur.execute("""
            SELECT symbol, side, setup, profile, net_pnl_r, status, closed_at
            FROM paper_micro_canary_positions_v11
            WHERE status='CLOSED' OR closed_at IS NOT NULL
            ORDER BY id ASC;
        """).fetchall()

        all_vals = [fnum(dict(r).get("net_pnl_r")) for r in rows]

        exact = []
        for r in rows:
            d = dict(r)
            if (
                d.get("symbol") == cand.get("symbol")
                and d.get("side") == cand.get("side")
                and d.get("setup") == cand.get("setup")
            ):
                exact.append(fnum(d.get("net_pnl_r")))

        vals = exact if len(exact) >= 3 else all_vals

        n = len(vals)
        exp = mean(vals)
        pf = profit_factor(vals)
        dd = max_drawdown(vals)
        tail = downside_tail(vals)

        score = 20.0
        if n >= 1 and exp > 0:
            score = 40.0
        if n >= 5 and exp > 0 and pf > 1.05:
            score = 60.0
        if n >= 10 and exp >= 0.03 and pf >= 1.15 and dd > -4:
            score = 85.0
        if n >= 30 and exp >= 0.04 and pf >= 1.20 and dd > -5:
            score = 100.0

        hard = []
        if n >= 5 and exp < 0:
            hard.append("LIVE_EXPECTANCY_NEGATIVE")
        if n >= 10 and pf < 1.0:
            hard.append("LIVE_PF_LT_1")
        if n >= 10 and dd < -4:
            hard.append("LIVE_DD_EXCEEDS_LIMIT")

        return {
            "closed_n": n,
            "exact_closed_n": len(exact),
            "expectancy": exp,
            "pf": pf,
            "dd": dd,
            "tail": tail,
            "score": score,
            "hard_vetoes": hard,
        }

    def risk_budget(self, edge: Dict[str, Any], wf: Dict[str, Any], live: Dict[str, Any]) -> Dict[str, Any]:
        score = 100.0
        hard = []

        if edge["posterior_lcb"] <= 0:
            score -= 50
        if edge["worst_r"] < -1:
            score -= 15
        if wf.get("tail", 0) < -0.75:
            score -= 20
        if live.get("dd", 0) < -4:
            score -= 30
        if live.get("closed_n", 0) < 10:
            score -= 25

        score = clamp(score, 0, 100)

        if score < 35:
            hard.append("RISK_BUDGET_TOO_LOW")

        size_mult = 0.0
        if score >= 80:
            size_mult = 1.0
        elif score >= 60:
            size_mult = 0.50
        elif score >= 45:
            size_mult = 0.25

        return {
            "score": score,
            "size_mult": size_mult,
            "hard_vetoes": hard,
        }

    def score_candidate(self, cur, cand: Dict[str, Any]) -> Dict[str, Any]:
        vals = self.shadow_outcomes_for_candidate(cur, cand)

        edge = self.bayesian_hierarchical_edge(cand, vals)
        wf = self.purged_walk_forward(vals, cand)
        deriv = self.derivatives_score(cur, cand)
        live = self.live_score(cur, cand)
        risk = self.risk_budget(edge, wf, live)

        stability_score = clamp(
            100
            - edge["decay"] * 45
            - max(0, -wf.get("tail", 0)) * 20,
            0,
            100,
        )

        tail_score = clamp(100 + wf.get("tail", 0) * 80, 0, 100)

        hard = []
        hard += [f"EDGE_{x}" for x in edge["hard_vetoes"]]
        hard += [f"WF_{x}" for x in wf.get("hard_vetoes", [])]
        hard += [f"DER_{x}" for x in deriv.get("hard_vetoes", [])]
        hard += [f"LIVE_{x}" for x in live.get("hard_vetoes", [])]
        hard += [f"RISK_{x}" for x in risk.get("hard_vetoes", [])]

        score = (
            edge["edge_quality"] * 0.26
            + wf["score"] * 0.24
            + stability_score * 0.14
            + deriv["score"] * 0.14
            + live["score"] * 0.12
            + tail_score * 0.06
            + risk["score"] * 0.04
        )

        if hard:
            score = min(score, 59.0)

        score = clamp(score, 0, 100)

        if hard:
            state = "RESEARCH_BLOCK"
            size_mult = 0.0
        elif score >= 82:
            state = "RESEARCH_APPROVE"
            size_mult = risk["size_mult"]
        elif score >= 68:
            state = "RESEARCH_REDUCED"
            size_mult = min(0.25, risk["size_mult"])
        else:
            state = "RESEARCH_REVIEW_ONLY"
            size_mult = 0.0

        payload = {
            "candidate": {
                "symbol": cand.get("symbol"),
                "side": cand.get("side"),
                "setup": cand.get("setup"),
                "profile": cand.get("profile"),
                "horizon_min": cand.get("horizon_min"),
                "source_edge_id": cand.get("source_edge_id") or cand.get("id"),
            },
            "edge_bayes": edge,
            "walk_forward": wf,
            "derivatives": deriv,
            "live": live,
            "risk_budget": risk,
            "stability_score": stability_score,
            "tail_risk_score": tail_score,
            "score": score,
            "state": state,
            "hard_vetoes": hard,
            "independent_data_contract": {
                "free_binance_derivatives": "USED_IF_PRESENT",
                "orderflow_proxy": "USED_IF_PRESENT",
                "cvd_proxy": "USED_IF_PRESENT",
                "options_flow": "NOT_AVAILABLE_NOT_FAKED",
                "etf_flows": "NOT_AVAILABLE_NOT_FAKED",
                "paid_api": "NOT_REQUIRED",
            },
        }

        return {
            "symbol": cand.get("symbol"),
            "side": cand.get("side"),
            "setup": cand.get("setup"),
            "profile": cand.get("profile"),
            "horizon_min": inum(cand.get("horizon_min")),
            "source_edge_id": inum(cand.get("source_edge_id") or cand.get("id")),
            "state": state,
            "score": score,
            "posterior_mean": edge["posterior_mean"],
            "posterior_lcb": edge["posterior_lcb"],
            "wf_score": wf["score"],
            "stability_score": stability_score,
            "live_score": live["score"],
            "derivatives_score": deriv["score"],
            "tail_risk_score": tail_score,
            "risk_budget_score": risk["score"],
            "size_mult": size_mult,
            "hard_vetoes": hard,
            "payload": payload,
        }

    def refresh(self) -> Dict[str, Any]:
        con = self.connect()
        cur = con.cursor()
        self.ensure_schema(cur)

        candidates = self.edge_universe(cur)
        scored = []

        for cand in candidates:
            s = self.score_candidate(cur, cand)
            scored.append(s)

            cur.execute("""
                INSERT INTO research_brain_candidates_v13 (
                    ts, version, symbol, side, setup, profile, horizon_min,
                    source_edge_id, research_state, research_score,
                    posterior_mean_r, posterior_lcb_r, wf_score,
                    stability_score, live_score, derivatives_score,
                    tail_risk_score, risk_budget_score, recommended_size_mult,
                    hard_vetoes, payload
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """, (
                now_iso(), VERSION,
                s["symbol"], s["side"], s["setup"], s["profile"],
                s["horizon_min"], s["source_edge_id"],
                s["state"], s["score"],
                s["posterior_mean"], s["posterior_lcb"], s["wf_score"],
                s["stability_score"], s["live_score"], s["derivatives_score"],
                s["tail_risk_score"], s["risk_budget_score"], s["size_mult"],
                js(s["hard_vetoes"]), js(s["payload"]),
            ))

        if not scored:
            hard = ["NO_RESEARCH_CANDIDATES"]
            cur.execute("""
                INSERT INTO research_brain_contract_v13 (
                    ts, version, contract_state, contract_score, policy,
                    size_mult, hard_vetoes, payload
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?);
            """, (
                now_iso(), VERSION, "CONTRACT_NO_EDGE", 0.0,
                "NO_NEW_CANARY", 0.0, js(hard), js({"hard_vetoes": hard}),
            ))
            con.commit()
            con.close()
            return {"contract_state": "CONTRACT_NO_EDGE", "hard_vetoes": hard}

        scored.sort(
            key=lambda x: (
                x["state"] == "RESEARCH_APPROVE",
                x["state"] == "RESEARCH_REDUCED",
                x["score"],
                x["posterior_lcb"],
            ),
            reverse=True,
        )

        best = scored[0]

        open_v11 = 0
        if self.exists(cur, "paper_micro_canary_positions_v11"):
            open_v11 = cur.execute("""
                SELECT COUNT(*)
                FROM paper_micro_canary_positions_v11
                WHERE status='OPEN' OR closed_at IS NULL;
            """).fetchone()[0]

        hard = list(best["hard_vetoes"])

        if open_v11 > 0:
            state = "CONTRACT_MANAGE_EXISTING"
            policy = "NO_NEW_CANARY_MANAGE_EXISTING"
            size_mult = 0.0
            hard.append("EXISTING_V11_CANARY_OPEN")
        elif best["state"] == "RESEARCH_APPROVE":
            state = "CONTRACT_APPROVE_CANARY"
            policy = "ALLOW_IF_V11_CONTROL_CONFIRMS"
            size_mult = best["size_mult"]
        elif best["state"] == "RESEARCH_REDUCED":
            state = "CONTRACT_REDUCED_CANARY"
            policy = "ALLOW_REDUCED_ONLY_IF_V11_CONTROL_CONFIRMS"
            size_mult = best["size_mult"]
        else:
            state = "CONTRACT_BLOCK"
            policy = "NO_NEW_CANARY"
            size_mult = 0.0

        payload = {
            "selected": best["payload"],
            "open_v11_canaries": open_v11,
            "top_candidates": [
                {
                    "symbol": x["symbol"],
                    "side": x["side"],
                    "setup": x["setup"],
                    "profile": x["profile"],
                    "score": round(x["score"], 2),
                    "state": x["state"],
                    "post_lcb": round(x["posterior_lcb"], 4),
                    "wf": round(x["wf_score"], 2),
                    "live": round(x["live_score"], 2),
                    "der": round(x["derivatives_score"], 2),
                    "vetoes": x["hard_vetoes"][:8],
                }
                for x in scored[:10]
            ],
        }

        cur.execute("""
            INSERT INTO research_brain_contract_v13 (
                ts, version, contract_state, selected_symbol, selected_side,
                selected_setup, selected_profile, selected_horizon_min,
                contract_score, policy, size_mult, hard_vetoes, payload
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """, (
            now_iso(), VERSION, state,
            best["symbol"], best["side"], best["setup"], best["profile"],
            best["horizon_min"], best["score"], policy, size_mult,
            js(hard), js(payload),
        ))

        con.commit()
        con.close()

        return payload


def main() -> None:
    print(json.dumps(InstitutionalResearchBrainV13().refresh(), indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
