from pathlib import Path
import sqlite3, json, subprocess
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "joanbot_v14.sqlite"
OUT = ROOT / "ai_audit" / "ai_context_snapshot.json"

def cmd(c):
    try:
        return subprocess.check_output(
            c, cwd=str(ROOT), shell=True, text=True, stderr=subprocess.DEVNULL
        ).strip()
    except Exception:
        return None

def trunc(v, n=1200):
    if isinstance(v, str) and len(v) > n:
        return v[:n] + "...TRUNCATED"
    return v

report = {
    "utc": datetime.now(timezone.utc).isoformat(),
    "git": {
        "branch": cmd("git branch --show-current"),
        "head": cmd("git rev-parse HEAD"),
        "upstream": cmd("git rev-parse @{u}"),
        "status": cmd("git status --short"),
    },
    "runtime": {
        "runner_process": cmd("ps -ef | grep -E 'python -m joanbot.runner|joanbot.runner' | grep -v grep || true"),
        "errors_tail": cmd("tail -80 data/runner_errors.log 2>/dev/null || true"),
    },
    "db": {
        "exists": DB.exists(),
        "size_mb": round(DB.stat().st_size / 1024 / 1024, 2) if DB.exists() else None,
        "tables": [],
        "samples": {},
    },
}

if DB.exists():
    con = sqlite3.connect(str(DB))
    con.row_factory = sqlite3.Row

    tables = [r["name"] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )]

    for t in tables:
        try:
            cols = [dict(x) for x in con.execute(f"PRAGMA table_info({t})")]
            n = con.execute(f"SELECT COUNT(*) c FROM {t}").fetchone()["c"]
            report["db"]["tables"].append({
                "table": t,
                "count": n,
                "columns": [{"name": c["name"], "type": c["type"]} for c in cols],
            })
        except Exception as e:
            report["db"]["tables"].append({"table": t, "error": str(e)})

    important = [
        "positions",
        "trades",
        "resultats_quant_nets",
        "research_promotion_decisions_v1",
        "universal_shadow_registry_v2",
        "universal_shadow_results_v2",
        "universal_shadow_cases_v2",
        "runtime_events",
        "position_events",
        "quant_governance_decision_v3",
        "risk_authority_decisions_v1",
        "risk_authority_global_state_v1",
        "tancaments_posicio_neta",
        "simulacions_sortida_neta",
    ]

    for t in important:
        if t not in tables:
            continue
        rows = []
        try:
            for r in con.execute(f"SELECT * FROM {t} ORDER BY rowid DESC LIMIT 10"):
                d = {k: trunc(v) for k, v in dict(r).items()}
                rows.append(d)
        except Exception as e:
            rows = [{"error": str(e)}]
        report["db"]["samples"][t] = rows

OUT.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2, default=str), encoding="utf-8")
print("AI_CONTEXT_SNAPSHOT_OK", OUT)
