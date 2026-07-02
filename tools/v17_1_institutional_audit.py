#!/usr/bin/env python3
import ast
import hashlib
import json
import os
import py_compile
import re
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path.cwd()
OUT = ROOT / "data" / "v17_1"
DB = ROOT / "data" / "joanbot_v14.sqlite"

SKIP_DIRS = {
    ".git", "__pycache__", ".venv", "venv", "env",
    "site-packages", ".mypy_cache", ".pytest_cache",
    "data", "node_modules"
}

SCAN_DIRS = [
    "joanbot", "core", "runtime", "market", "features",
    "strategies", "risk", "execution", "learning",
    "analytics", "dashboard", "telegram", "tools", "scripts"
]

RESPONSIBILITY_PATTERNS = {
    "runtime": ["runner", "runtime", "main_loop", "daemon"],
    "market_data": ["market", "binance", "websocket", "snapshot", "ohlcv", "orderbook"],
    "features": ["feature", "indicator", "atr", "rsi", "ema", "liquidity"],
    "regime": ["regime", "context", "market_structure", "trend"],
    "strategy": ["strategy", "setup", "signal", "decision"],
    "edge": ["edge", "expectancy", "reputation", "bayes", "optimizer", "setup_reputation"],
    "risk": ["risk", "sizing", "drawdown", "portfolio", "exposure"],
    "execution": ["execution", "broker", "order", "fill", "slippage", "paper"],
    "learning": ["learning", "memory", "optimizer", "adaptive"],
    "validation": ["backtest", "walk_forward", "validation", "monte", "oos"],
    "supervision": ["supervisor", "health", "heartbeat", "liveness", "watchdog"],
    "telegram": ["telegram", "bot_command", "alert"],
    "dashboard": ["dashboard", "streamlit", "web", "panel"],
    "storage": ["storage", "sqlite", "db", "repository", "journal"],
    "config": ["config", "settings", "env"],
}

TIME_HINTS = ["ts", "time", "timestamp", "created", "updated", "opened", "closed", "utc", "datetime"]
PNL_HINTS = ["pnl", "profit", "realized", "net_pnl", "r_multiple", "rr"]
EDGE_HINTS = ["expectancy", "profit_factor", "win_rate", "sample", "confidence", "edge"]


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def rel(p: Path) -> str:
    try:
        return str(p.relative_to(ROOT))
    except Exception:
        return str(p)


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def should_skip(path: Path) -> bool:
    return any(part in SKIP_DIRS for part in path.parts)


def collect_files():
    py_files = []
    sh_files = []

    for d in SCAN_DIRS:
        base = ROOT / d
        if not base.exists():
            continue

        if base.is_file():
            continue

        for p in base.rglob("*"):
            if should_skip(p):
                continue
            if p.is_file() and p.suffix == ".py":
                py_files.append(p)
            elif p.is_file() and p.suffix == ".sh":
                sh_files.append(p)

    return sorted(set(py_files)), sorted(set(sh_files))


def tag_responsibilities(path, text, funcs, classes):
    hay = " ".join([
        rel(path).lower(),
        text[:20000].lower(),
        " ".join(funcs).lower(),
        " ".join(classes).lower(),
    ])

    tags = []
    for tag, pats in RESPONSIBILITY_PATTERNS.items():
        if any(p in hay for p in pats):
            tags.append(tag)

    return tags


def ast_scan_file(p: Path):
    text = p.read_text(errors="ignore")
    item = {
        "path": rel(p),
        "size": p.stat().st_size,
        "loc": text.count("\n") + 1,
        "sha256_12": sha256_file(p)[:12],
        "syntax_ok": True,
        "syntax_error": None,
        "imports_internal": [],
        "imports_external": [],
        "functions": [],
        "classes": [],
        "env_vars": sorted(set(re.findall(r'os\.getenv\(["\']([^"\']+)["\']', text))),
        "sqlite_refs": sorted(set(re.findall(r'["\']([A-Za-z0-9_]+)["\']', text)))[:300],
        "tags": [],
    }

    try:
        tree = ast.parse(text, filename=str(p))
    except SyntaxError as e:
        item["syntax_ok"] = False
        item["syntax_error"] = f"{e.msg} line={e.lineno}"
        return item

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            item["functions"].append(node.name)
        elif isinstance(node, ast.AsyncFunctionDef):
            item["functions"].append(node.name)
        elif isinstance(node, ast.ClassDef):
            item["classes"].append(node.name)
        elif isinstance(node, ast.Import):
            for a in node.names:
                name = a.name
                if name.startswith(("joanbot", "core", "execution", "risk", "learning", "strategies", "market", "features")):
                    item["imports_internal"].append(name)
                else:
                    item["imports_external"].append(name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if mod.startswith(("joanbot", "core", "execution", "risk", "learning", "strategies", "market", "features")):
                item["imports_internal"].append(mod)
            elif mod:
                item["imports_external"].append(mod.split(".")[0])

    item["functions"] = sorted(set(item["functions"]))
    item["classes"] = sorted(set(item["classes"]))
    item["imports_internal"] = sorted(set(item["imports_internal"]))
    item["imports_external"] = sorted(set(item["imports_external"]))
    item["tags"] = tag_responsibilities(p, text, item["functions"], item["classes"])

    return item


def compile_check(py_files):
    errors = []
    checked = 0

    for p in py_files:
        checked += 1
        try:
            py_compile.compile(str(p), doraise=True)
        except Exception as e:
            errors.append({
                "path": rel(p),
                "error": repr(e)[:500],
            })

    return {"checked": checked, "errors": errors}


def scan_scripts(sh_files):
    out = []

    for p in sh_files:
        text = p.read_text(errors="ignore")
        out.append({
            "path": rel(p),
            "executable": os.access(p, os.X_OK),
            "loc": text.count("\n") + 1,
            "mentions_runtime": bool(re.search(r"institutional_runtime|runner|python\s+-m", text)),
            "mentions_nohup": "nohup" in text,
            "mentions_pid": ".pid" in text or "pid" in text.lower(),
            "mentions_lock": "lock" in text.lower(),
            "python_modules": sorted(set(re.findall(r"python(?:3)?\s+-m\s+([A-Za-z0-9_\.]+)", text))),
        })

    return out


def parse_dt(v):
    if v is None:
        return None

    if isinstance(v, (int, float)):
        x = float(v)
        if x > 1e12:
            x /= 1000
        if x > 1e9:
            return datetime.fromtimestamp(x, timezone.utc)
        return None

    s = str(v).strip().replace("Z", "+00:00")
    if not s:
        return None

    if re.fullmatch(r"\d+(\.\d+)?", s):
        return parse_dt(float(s))

    for candidate in [s, s.replace(" ", "T")]:
        try:
            d = datetime.fromisoformat(candidate)
            if d.tzinfo is None:
                d = d.replace(tzinfo=timezone.utc)
            return d.astimezone(timezone.utc)
        except Exception:
            pass

    return None


def qid(x):
    return '"' + str(x).replace('"', '""') + '"'


def db_audit():
    r = {
        "path": rel(DB),
        "exists": DB.exists(),
        "quick_check": None,
        "size_mb": None,
        "tables": [],
        "critical_tables_found": {},
        "candidate_trade_tables": [],
        "candidate_edge_tables": [],
        "errors": [],
    }

    if not DB.exists():
        return r

    r["size_mb"] = round(DB.stat().st_size / 1048576, 2)

    try:
        con = sqlite3.connect(str(DB), timeout=5)
        r["quick_check"] = con.execute("PRAGMA quick_check").fetchone()[0]

        table_names = [
            x[0] for x in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
        ]

        critical = [
            "market_snapshots", "decisions", "paper_trades",
            "paper_micro_canary_positions_v11", "shadow_signals",
            "shadow_results", "execution_audit", "decision_audit"
        ]

        for t in critical:
            r["critical_tables_found"][t] = t in table_names

        for t in table_names:
            info = con.execute(f"PRAGMA table_info({qid(t)})").fetchall()
            cols = [x[1] for x in info]

            row_count = None
            try:
                row_count = con.execute(f"SELECT COUNT(*) FROM {qid(t)}").fetchone()[0]
            except Exception:
                pass

            newest = None
            newest_col = None

            for c in cols:
                if not any(h in c.lower() for h in TIME_HINTS):
                    continue

                try:
                    v = con.execute(f"SELECT MAX({qid(c)}) FROM {qid(t)}").fetchone()[0]
                    d = parse_dt(v)
                    if d and (newest is None or d > newest):
                        newest = d
                        newest_col = c
                except Exception:
                    pass

            table_item = {
                "name": t,
                "columns": cols,
                "row_count": row_count,
                "newest_utc": newest.isoformat() if newest else None,
                "newest_column": newest_col,
                "has_pnl_hint": any(any(h in c.lower() for h in PNL_HINTS) for c in cols),
                "has_edge_hint": any(any(h in c.lower() for h in EDGE_HINTS) for c in cols),
            }

            r["tables"].append(table_item)

            low = " ".join([t] + cols).lower()

            if any(x in low for x in ["trade", "position", "fill", "execution"]) and table_item["has_pnl_hint"]:
                r["candidate_trade_tables"].append(t)

            if any(x in low for x in ["edge", "setup", "reputation", "optimizer", "expectancy"]):
                r["candidate_edge_tables"].append(t)

        con.close()

    except Exception as e:
        r["errors"].append(repr(e))

    return r


def ps_audit():
    try:
        txt = subprocess.check_output(["ps", "-ef"], text=True, stderr=subprocess.STDOUT, errors="ignore")
    except Exception:
        txt = subprocess.check_output(["ps"], text=True, stderr=subprocess.STDOUT, errors="ignore")

    patterns = {
        "runtime_v11": r"python.*institutional_runtime_v11",
        "alpha_kernel_v16": r"run_alpha_kernel_v16_overnight",
        "liquidation_stream_v16": r"run_liquidation_stream_v16_forever",
        "process_supervisor_v16": r"v16_process_supervisor",
        "v17_control": r"v17_institutional_control|run_v17_control_plane",
    }

    out = {}

    for name, pat in patterns.items():
        hits = [
            line for line in txt.splitlines()
            if re.search(pat, line) and "grep" not in line
        ]
        out[name] = {
            "count": len(hits),
            "lines": hits[:10],
            "state": "OK" if len(hits) == 1 else "DEAD" if len(hits) == 0 else "DUPLICATED",
        }

    return out


def collision_analysis(modules):
    by_func = {}
    by_class = {}
    by_tag = {}

    for m in modules:
        for f in m["functions"]:
            by_func.setdefault(f, []).append(m["path"])
        for c in m["classes"]:
            by_class.setdefault(c, []).append(m["path"])
        for t in m["tags"]:
            by_tag.setdefault(t, []).append(m["path"])

    duplicated_functions = {
        k: v for k, v in by_func.items()
        if len(v) > 1 and not k.startswith("_")
    }

    duplicated_classes = {
        k: v for k, v in by_class.items()
        if len(v) > 1 and not k.startswith("_")
    }

    suspicious_tag_density = {
        k: v for k, v in by_tag.items()
        if k in {"supervision", "edge", "risk", "execution", "validation"} and len(v) >= 5
    }

    return {
        "duplicated_functions_top": dict(list(sorted(
            duplicated_functions.items(),
            key=lambda kv: len(kv[1]),
            reverse=True
        ))[:80]),
        "duplicated_classes_top": dict(list(sorted(
            duplicated_classes.items(),
            key=lambda kv: len(kv[1]),
            reverse=True
        ))[:80]),
        "responsibility_density": {k: len(v) for k, v in sorted(by_tag.items())},
        "suspicious_tag_density": suspicious_tag_density,
    }


def integration_map(modules, db):
    def find_by_tags(*tags):
        res = []
        for m in modules:
            if any(t in m["tags"] for t in tags):
                res.append(m["path"])
        return res[:40]

    return {
        "do_not_duplicate": {
            "runtime_candidates": find_by_tags("runtime"),
            "edge_candidates": find_by_tags("edge", "learning"),
            "risk_candidates": find_by_tags("risk"),
            "execution_candidates": find_by_tags("execution"),
            "validation_candidates": find_by_tags("validation"),
            "supervision_candidates": find_by_tags("supervision"),
            "storage_candidates": find_by_tags("storage"),
        },
        "recommended_new_namespace": "joanbot/institutional/",
        "recommended_mode_first": "READ_ONLY_OBSERVER",
        "allowed_initial_writes": [
            "data/v17_1/audit_report.json",
            "data/v17_1/audit_summary.md",
            "data/institutional/audit_ledger.jsonl",
            "data/institutional/service_heartbeats/*.json"
        ],
        "db_candidate_trade_tables": db.get("candidate_trade_tables", []),
        "db_candidate_edge_tables": db.get("candidate_edge_tables", []),
    }


def verdict(report):
    red = []
    yellow = []

    if not report["db"]["exists"]:
        red.append("DB_MISSING")
    elif str(report["db"].get("quick_check")).lower() != "ok":
        red.append("DB_QUICK_CHECK_NOT_OK")

    if report["compile"]["errors"]:
        red.append("PYTHON_COMPILE_ERRORS")

    for name, ps in report["processes"].items():
        if ps["state"] == "DUPLICATED":
            red.append(f"{name}_DUPLICATED")

    if not report["db"].get("candidate_trade_tables"):
        yellow.append("NO_CLEAR_TRADE_TABLE_FOR_QUANT_METRICS")

    if not report["db"].get("candidate_edge_tables"):
        yellow.append("NO_CLEAR_EDGE_TABLE_FOR_EDGE_REGISTRY")

    if report["collisions"]["suspicious_tag_density"]:
        yellow.append("HIGH_RESPONSIBILITY_DENSITY_REVIEW_REQUIRED")

    decision = "STOP_PATCH_UNTIL_REVIEW" if red else "PATCH_ALLOWED_READ_ONLY_FIRST" if yellow else "PATCH_ALLOWED"

    return {
        "decision": decision,
        "red": red,
        "yellow": yellow,
    }


def write_summary(report):
    lines = []
    v = report["verdict"]

    lines.append("# V17.1 Institutional Architecture Audit")
    lines.append("")
    lines.append(f"- Generated UTC: `{report['generated_utc']}`")
    lines.append(f"- Decision: `{v['decision']}`")
    lines.append(f"- Red flags: `{', '.join(v['red']) if v['red'] else 'none'}`")
    lines.append(f"- Yellow flags: `{', '.join(v['yellow']) if v['yellow'] else 'none'}`")
    lines.append("")

    lines.append("## Processes")
    for k, x in report["processes"].items():
        lines.append(f"- `{k}`: `{x['state']}` count={x['count']}")

    lines.append("")
    lines.append("## DB")
    db = report["db"]
    lines.append(f"- Exists: `{db['exists']}`")
    lines.append(f"- Quick check: `{db.get('quick_check')}`")
    lines.append(f"- Size MB: `{db.get('size_mb')}`")
    lines.append(f"- Candidate trade tables: `{db.get('candidate_trade_tables')}`")
    lines.append(f"- Candidate edge tables: `{db.get('candidate_edge_tables')}`")
    lines.append("")

    lines.append("## Critical tables")
    for k, ok in db.get("critical_tables_found", {}).items():
        lines.append(f"- `{k}`: `{ok}`")

    lines.append("")
    lines.append("## Compile")
    lines.append(f"- Checked files: `{report['compile']['checked']}`")
    lines.append(f"- Compile errors: `{len(report['compile']['errors'])}`")

    if report["compile"]["errors"]:
        for e in report["compile"]["errors"][:20]:
            lines.append(f"  - `{e['path']}`: {e['error']}")

    lines.append("")
    lines.append("## Responsibility density")
    for k, n in sorted(report["collisions"]["responsibility_density"].items()):
        lines.append(f"- `{k}`: `{n}` files")

    lines.append("")
    lines.append("## Suspicious responsibility density")
    s = report["collisions"]["suspicious_tag_density"]
    if not s:
        lines.append("- none")
    else:
        for k, files in s.items():
            lines.append(f"- `{k}`:")
            for f in files[:25]:
                lines.append(f"  - `{f}`")

    lines.append("")
    lines.append("## Integration map")
    im = report["integration_map"]
    lines.append(f"- New namespace: `{im['recommended_new_namespace']}`")
    lines.append(f"- First mode: `{im['recommended_mode_first']}`")
    lines.append("- Do not duplicate candidates:")
    for k, files in im["do_not_duplicate"].items():
        lines.append(f"  - `{k}`: {len(files)} files")
        for f in files[:15]:
            lines.append(f"    - `{f}`")

    path = OUT / "audit_summary.md"
    path.write_text("\n".join(lines))


def main():
    OUT.mkdir(parents=True, exist_ok=True)

    py_files, sh_files = collect_files()
    modules = [ast_scan_file(p) for p in py_files]
    scripts = scan_scripts(sh_files)
    comp = compile_check(py_files)
    db = db_audit()
    ps = ps_audit()
    coll = collision_analysis(modules)

    report = {
        "version": "V17.1_INSTITUTIONAL_ARCHITECTURE_AUDIT",
        "generated_utc": utc_now(),
        "root": str(ROOT),
        "files": {
            "python_count": len(py_files),
            "shell_count": len(sh_files),
        },
        "modules": modules,
        "scripts": scripts,
        "compile": comp,
        "db": db,
        "processes": ps,
        "collisions": coll,
    }

    report["integration_map"] = integration_map(modules, db)
    report["verdict"] = verdict(report)

    (OUT / "audit_report.json").write_text(json.dumps(report, indent=2, sort_keys=True))
    write_summary(report)

    print("===== V17.1 AUDIT DONE =====")
    print(f"decision={report['verdict']['decision']}")
    print(f"red={report['verdict']['red']}")
    print(f"yellow={report['verdict']['yellow']}")
    print(f"report={OUT / 'audit_report.json'}")
    print(f"summary={OUT / 'audit_summary.md'}")

    return 0 if not report["verdict"]["red"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
