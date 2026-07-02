#!/usr/bin/env python3
from __future__ import annotations

import json, math, sqlite3, statistics, time
from datetime import datetime, timezone
from pathlib import Path
from collections import defaultdict

ROOT = Path.cwd()
DB = ROOT / "data" / "joanbot_v14.sqlite"
OUT = ROOT / "data" / "v17_3"

VERSION = "V17.3_QUANT_AUTHORITY_SCORING_ENGINE"

TABLES = [
    "alpha_evidence_tensor_v5",
    "alpha_setup_registry_v16",
    "alpha_bayesian_posterior_v5",
    "alpha_research_rollup_v16",
    "alpha_promotion_contract_v5",
    "alpha_final_gate_v16",
    "institutional_control_plane_v11",
    "universal_shadow_results_v2",
    "paper_micro_canary_positions_v11",
    "trades",
    "positions",
    "outcome_provenance_v1",
    "evidence_hygiene_summary_v1",
]

def now():
    return datetime.now(timezone.utc)

def iso():
    return now().isoformat()

def qid(x):
    return '"' + x.replace('"', '""') + '"'

def f(x, d=None):
    try:
        if x is None or x == "":
            return d
        v = float(x)
        if math.isnan(v) or math.isinf(v):
            return d
        return v
    except Exception:
        return d

def i(x, d=0):
    v = f(x, None)
    return d if v is None else int(v)

def clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))

def connect():
    con = sqlite3.connect("file:" + str(DB.resolve()) + "?mode=ro", uri=True, timeout=10)
    con.row_factory = sqlite3.Row
    return con

def exists(con, t):
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (t,)
    ).fetchone() is not None

def rows(con, t):
    if not exists(con, t):
        return []
    return [dict(r) for r in con.execute(f"SELECT * FROM {qid(t)}").fetchall()]

def key(r):
    symbol = str(r.get("symbol") or r.get("selected_symbol") or r.get("edge_symbol") or "").upper()
    side = str(r.get("side") or r.get("selected_side") or r.get("edge_side") or "").upper()
    setup = str(r.get("setup") or r.get("selected_setup") or r.get("edge_setup") or "")
    profile = str(r.get("profile") or r.get("selected_profile") or r.get("edge_profile") or "")
    horizon = str(r.get("horizon_min") or r.get("selected_horizon_min") or r.get("edge_horizon_min") or "")
    if symbol and side and setup:
        return "|".join([symbol, side, setup, profile or "-", horizon or "-"])
    return "UNKNOWN|" + str(r.get("alpha_key") or r.get("contract_id") or r.get("id") or "")

def ts_val(r):
    for c in ["ts", "updated_at", "closed_at", "resolved_at", "opened_at", "created_at"]:
        v = r.get(c)
        if not v:
            continue
        s = str(v).replace("Z", "+00:00")
        try:
            d = datetime.fromisoformat(s.replace(" ", "T"))
            if d.tzinfo is None:
                d = d.replace(tzinfo=timezone.utc)
            return d.timestamp()
        except Exception:
            pass
    return 0.0

def latest_map(rs):
    out = {}
    for r in rs:
        k = key(r)
        if k not in out or ts_val(r) >= ts_val(out[k]):
            out[k] = r
    return out

def value(r, names):
    for n in names:
        if n in r and r[n] not in (None, "", "None"):
            return f(r[n], None)
    return None

def sample_values(rs, cols):
    b = defaultdict(list)
    for r in rs:
        v = value(r, cols)
        if v is not None:
            b[key(r)].append(v)
    return b

def stat(vals):
    vals = [v for v in vals if v is not None]
    if not vals:
        return {}
    wins = [v for v in vals if v > 0]
    losses = [v for v in vals if v < 0]
    gp = sum(wins)
    gl = abs(sum(losses))
    eq = peak = mdd = 0.0
    for v in vals:
        eq += v
        peak = max(peak, eq)
        mdd = max(mdd, peak - eq)
    return {
        "n": len(vals),
        "mean": sum(vals) / len(vals),
        "median": statistics.median(vals),
        "winrate": len(wins) / len(vals),
        "pf": gp / gl if gl > 0 else None,
        "worst": min(vals),
        "best": max(vals),
        "mdd": mdd,
        "std": statistics.pstdev(vals) if len(vals) > 1 else 0.0,
    }

def bayes_lcb(mean, std, n):
    if mean is None:
        return None
    n = max(1, n)
    std = 0.35 if std is None or std <= 0 else std
    return mean - 1.65 * std / math.sqrt(n)

def shrink(mean, n, prior=0.0, prior_n=80):
    if mean is None:
        return None
    return ((mean * n) + (prior * prior_n)) / (n + prior_n)

def prob_edge_gt_zero(mean, std, n):
    if mean is None:
        return None
    n = max(1, n)
    std = 0.35 if std is None or std <= 0 else std
    z = mean / (std / math.sqrt(n))
    return 1 / (1 + math.exp(-1.702 * z))

def cost_adjust(exp_r, side, horizon):
    if exp_r is None:
        return None
    h = i(horizon, 60)
    base = 0.012       # fees + spread mínim en R
    latency = 0.004 if h <= 30 else 0.002
    funding = 0.006 if side == "SHORT" and h >= 240 else 0.002
    return exp_r - base - latency - funding

def hard_veto(*rs):
    txt = []
    for r in rs:
        if not r:
            continue
        for c in ["hard_vetoes", "reason", "reasons", "invalidations", "payload"]:
            v = r.get(c)
            if v and str(v).strip() not in ("[]", "{}", "None", "none"):
                s = str(v)
                if any(x in s.upper() for x in ["VETO", "BLOCK", "REJECT", "TOXIC", "INVALID"]):
                    txt.append(s[:300])
    return " | ".join(txt)

def score(c):
    red, yellow = [], []
    s = 0.0

    n = c["sample_n"]
    live = c["live_n"]
    shadow = c["shadow_n"]
    exp = c["net_expectancy_r"]
    lcb = c["net_lcb_r"]
    pf = c["profit_factor"]
    prob = c["prob_edge_gt_zero"]
    q = c["quality"]
    worst = c["worst_r"]
    dd = c["max_drawdown_r"]

    s += 12 * clamp(n / 250)
    s += 10 * clamp(live / 40)
    s += 6 * clamp(shadow / 400)

    if exp is None:
        yellow.append("NO_NET_EXPECTANCY")
    elif exp <= 0:
        red.append("NET_EXPECTANCY_LE_0")
    else:
        s += 18 * clamp(exp / 0.10)

    if lcb is None:
        yellow.append("NO_NET_LCB")
    elif lcb <= 0:
        red.append("NET_LCB_LE_0")
    else:
        s += 16 * clamp(lcb / 0.06)

    if pf is None:
        yellow.append("NO_PF")
    elif pf < 1.15:
        red.append("PF_BELOW_INSTITUTIONAL_MIN")
    else:
        s += 12 * clamp((pf - 1.15) / 0.85)

    if prob is None:
        yellow.append("NO_PROB_EDGE")
    elif prob < 0.60:
        red.append("PROB_EDGE_TOO_LOW")
    else:
        s += 10 * clamp((prob - 0.60) / 0.30)

    if q is not None:
        s += 10 * clamp(q)
    else:
        yellow.append("NO_QUALITY_SCORE")

    if worst is not None:
        if worst <= -2.0:
            red.append("FAT_TAIL_WORST_R")
        elif worst <= -1.2:
            yellow.append("TAIL_RISK_HIGH")
        else:
            s += 4

    if dd is not None:
        if dd >= 6:
            red.append("DRAWDOWN_TOO_HIGH")
        elif dd >= 3:
            yellow.append("DRAWDOWN_ELEVATED")
        else:
            s += 4

    if c["hard_veto"]:
        red.append("HARD_VETO_PRESENT")

    if n < 60:
        yellow.append("LOW_SAMPLE")
    if live < 5:
        yellow.append("LOW_LIVE_CONFIRMATION")

    s = round(clamp(s, 0, 100), 2)

    if red:
        state = "BLOCKED"
    elif s >= 85 and live >= 20 and n >= 160:
        state = "PROMOTION_CANDIDATE"
    elif s >= 75 and live >= 10 and n >= 100:
        state = "PAPER_DIRECT_CANDIDATE"
    elif s >= 62 and n >= 60:
        state = "PAPER_MICRO_CANDIDATE"
    elif s >= 45:
        state = "SHADOW_ONLY"
    else:
        state = "RESEARCH_ONLY"

    size = {
        "BLOCKED": 0.0,
        "RESEARCH_ONLY": 0.0,
        "SHADOW_ONLY": 0.02,
        "PAPER_MICRO_CANDIDATE": 0.05 + 0.15 * clamp((s - 62) / 18),
        "PAPER_DIRECT_CANDIDATE": 0.20 + 0.30 * clamp((s - 75) / 15),
        "PROMOTION_CANDIDATE": 0.50 + 0.40 * clamp((s - 85) / 15),
    }[state]

    return {
        "score": s,
        "authority_state": state,
        "recommended_size_mult": round(size, 4),
        "red_flags": red,
        "yellow_flags": yellow,
    }

def build():
    OUT.mkdir(parents=True, exist_ok=True)
    con = connect()
    qc = con.execute("PRAGMA quick_check").fetchone()[0]

    data = {t: rows(con, t) for t in TABLES}
    latest = {t: latest_map(data[t]) for t in TABLES}

    live_vals = defaultdict(list)
    for t, cols in {
        "trades": ["pnl_r", "net_pnl_r", "pnl"],
        "positions": ["pnl_r", "net_pnl_r", "pnl_usd"],
        "paper_micro_canary_positions_v11": ["net_pnl_r", "pnl_r", "gross_pnl_r"],
        "outcome_provenance_v1": ["pnl_r", "pnl_usd"],
    }.items():
        for k, vals in sample_values(data[t], cols).items():
            live_vals[k].extend(vals)

    shadow_vals = sample_values(data["universal_shadow_results_v2"], ["result_r"])

    keys = set()
    for mp in latest.values():
        keys.update(mp.keys())
    keys.update(live_vals.keys())
    keys.update(shadow_vals.keys())

    candidates = []

    for k in keys:
        tensor = latest["alpha_evidence_tensor_v5"].get(k)
        registry = latest["alpha_setup_registry_v16"].get(k)
        post = latest["alpha_bayesian_posterior_v5"].get(k)
        roll = latest["alpha_research_rollup_v16"].get(k)
        promo = latest["alpha_promotion_contract_v5"].get(k)
        gate = latest["alpha_final_gate_v16"].get(k)

        idr = tensor or registry or post or roll or promo or gate or {}
        symbol = str(idr.get("symbol") or idr.get("selected_symbol") or "").upper()
        side = str(idr.get("side") or idr.get("selected_side") or "").upper()
        setup = str(idr.get("setup") or idr.get("selected_setup") or "")
        profile = str(idr.get("profile") or idr.get("selected_profile") or "")
        horizon = str(idr.get("horizon_min") or idr.get("selected_horizon_min") or "")

        live_stat = stat(live_vals.get(k, []))
        shadow_stat = stat(shadow_vals.get(k, []))

        n = max(
            i((tensor or {}).get("n")),
            i((registry or {}).get("sample_n")),
            i((post or {}).get("n")),
            i((roll or {}).get("sample_n")),
            live_stat.get("n", 0),
            shadow_stat.get("n", 0),
        )

        raw_exp = (
            value(tensor or {}, ["shrunk_expectancy_r", "expectancy_r", "validation_exp_r"])
            or value(registry or {}, ["expectancy_r"])
            or value(roll or {}, ["expectancy_r"])
            or value(post or {}, ["posterior_mean_r", "tensor_mean_r"])
            or live_stat.get("mean")
            or shadow_stat.get("mean")
        )

        std = (
            value(tensor or {}, ["std_r", "tensor_std_r"])
            or value(post or {}, ["posterior_std_r", "tensor_std_r"])
            or live_stat.get("std")
            or shadow_stat.get("std")
            or 0.35
        )

        shrunk_exp = shrink(raw_exp, n)
        net_exp = cost_adjust(shrunk_exp, side, horizon)
        net_lcb = cost_adjust(bayes_lcb(shrunk_exp, std, n), side, horizon)

        pf = (
            value(tensor or {}, ["profit_factor_capped", "profit_factor"])
            or value(registry or {}, ["profit_factor"])
            or value(roll or {}, ["profit_factor"])
            or live_stat.get("pf")
            or shadow_stat.get("pf")
        )

        prob = (
            value(post or {}, ["prob_edge_gt_zero", "prob_edge_gt_min"])
            or value(promo or {}, ["prob_edge_gt_zero", "prob_edge_gt_min"])
            or prob_edge_gt_zero(net_exp, std, n)
        )

        quality_values = [
            value(tensor or {}, ["sample_quality"]),
            value(tensor or {}, ["validation_quality"]),
            value(tensor or {}, ["fold_quality"]),
            value(tensor or {}, ["stability_quality"]),
            value(post or {}, ["sample_quality"]),
            value(post or {}, ["validation_quality"]),
            value(roll or {}, ["cpcv_score"]),
            value(roll or {}, ["feature_score"]),
            value(roll or {}, ["risk_score"]),
            value(promo or {}, ["robustness_score"]),
        ]
        qv = [x for x in quality_values if x is not None]
        quality = sum(qv) / len(qv) if qv else None

        worst = min([
            x for x in [
                value(tensor or {}, ["worst_r"]),
                live_stat.get("worst"),
                shadow_stat.get("worst")
            ] if x is not None
        ], default=None)

        dd = max([
            x for x in [
                value(registry or {}, ["max_drawdown_r"]),
                value(roll or {}, ["max_drawdown_r"]),
                live_stat.get("mdd"),
                shadow_stat.get("mdd"),
            ] if x is not None
        ], default=None)

        c = {
            "key": k,
            "symbol": symbol,
            "side": side,
            "setup": setup,
            "profile": profile,
            "horizon_min": horizon,
            "sample_n": n,
            "live_n": live_stat.get("n", 0),
            "shadow_n": shadow_stat.get("n", 0),
            "raw_expectancy_r": raw_exp,
            "shrunk_expectancy_r": shrunk_exp,
            "net_expectancy_r": net_exp,
            "net_lcb_r": net_lcb,
            "profit_factor": pf,
            "prob_edge_gt_zero": prob,
            "quality": quality,
            "worst_r": worst,
            "max_drawdown_r": dd,
            "live_expectancy_r": live_stat.get("mean"),
            "shadow_expectancy_r": shadow_stat.get("mean"),
            "hard_veto": hard_veto(tensor, registry, post, roll, promo, gate),
            "sources": {
                "tensor": bool(tensor),
                "registry": bool(registry),
                "posterior": bool(post),
                "rollup": bool(roll),
                "promotion": bool(promo),
                "final_gate": bool(gate),
                "live": bool(live_stat),
                "shadow": bool(shadow_stat),
            }
        }

        c.update(score(c))
        candidates.append(c)

    candidates.sort(key=lambda x: (x["score"], x["sample_n"]), reverse=True)

    counts = defaultdict(int)
    for c in candidates:
        counts[c["authority_state"]] += 1

    out = {
        "version": VERSION,
        "generated_utc": iso(),
        "quick_check": qc,
        "candidate_count": len(candidates),
        "state_counts": dict(counts),
        "candidates": candidates,
    }

    con.close()
    return out

def write(out):
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "quant_authority_latest.json").write_text(json.dumps(out, indent=2, sort_keys=True))

    lines = []
    lines.append("# V17.3 Quant Authority Scoring Engine")
    lines.append("")
    lines.append(f"- UTC: `{out['generated_utc']}`")
    lines.append(f"- DB: `{out['quick_check']}`")
    lines.append(f"- Candidates: `{out['candidate_count']}`")
    lines.append(f"- States: `{out['state_counts']}`")
    lines.append("")
    lines.append("| rank | state | score | size | symbol | side | setup | n | live | shadow | net_exp | net_lcb | PF | prob | quality | red | yellow |")
    lines.append("|---:|---|---:|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|")

    for r, c in enumerate(out["candidates"][:60], 1):
        lines.append(
            f"| {r} | {c['authority_state']} | {c['score']:.2f} | {c['recommended_size_mult']:.4f} | "
            f"{c['symbol'] or '-'} | {c['side'] or '-'} | {(c['setup'] or '-')[:30]} | "
            f"{c['sample_n']} | {c['live_n']} | {c['shadow_n']} | "
            f"{fmt(c['net_expectancy_r'])} | {fmt(c['net_lcb_r'])} | {fmt(c['profit_factor'])} | "
            f"{fmt(c['prob_edge_gt_zero'])} | {fmt(c['quality'])} | "
            f"{','.join(c['red_flags'])[:80]} | {','.join(c['yellow_flags'])[:120]} |"
        )

    (OUT / "quant_authority_summary.md").write_text("\n".join(lines))

    with (OUT / "quant_authority_ledger.jsonl").open("a") as f:
        f.write(json.dumps({
            "ts": out["generated_utc"],
            "states": out["state_counts"],
            "top": out["candidates"][:10],
        }, sort_keys=True) + "\n")

def fmt(x):
    if x is None:
        return "-"
    try:
        return f"{float(x):.4f}"
    except Exception:
        return "-"

def main():
    out = build()
    write(out)

    print("===== V17.3 QUANT AUTHORITY =====")
    print("quick_check:", out["quick_check"])
    print("candidate_count:", out["candidate_count"])
    print("state_counts:", out["state_counts"])
    print("")
    for r, c in enumerate(out["candidates"][:25], 1):
        print(
            f"#{r:02d} {c['authority_state']} score={c['score']} "
            f"size={c['recommended_size_mult']} {c['symbol']} {c['side']} "
            f"{c['setup'][:28]} n={c['sample_n']} live={c['live_n']} "
            f"shadow={c['shadow_n']} net_exp={fmt(c['net_expectancy_r'])} "
            f"lcb={fmt(c['net_lcb_r'])} pf={fmt(c['profit_factor'])} "
            f"red={c['red_flags']} yellow={c['yellow_flags']}"
        )

if __name__ == "__main__":
    main()
