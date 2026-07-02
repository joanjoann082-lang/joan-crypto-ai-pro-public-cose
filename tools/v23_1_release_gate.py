#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

VERSION = "V23.1_RELEASE_GATE_PRO"

LIVE = Path("/storage/emulated/0/Download/joan_crypto_ai_pro_v14")
POLICY = LIVE / "config/v23_1_operating_policy.json"
OUT = LIVE / "data/v23_1_release_gate"
REPORT_JSON = OUT / "release_gate_report.json"
SUMMARY_MD = OUT / "release_gate_summary.md"

ERROR_RE = re.compile(
    r"(Traceback|OperationalError|database is locked|unable to open database|no such table|no such column|IntegrityError|SyntaxError|NameError|KeyError)",
    re.I,
)

def utc() -> str:
    return datetime.now(timezone.utc).isoformat()

def run(cmd: List[str], cwd: Path, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=str(cwd), text=True, capture_output=True, check=check)

def sh(command: str, cwd: Path, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(command, cwd=str(cwd), text=True, shell=True, capture_output=True, check=check)

def load_policy() -> Dict[str, Any]:
    if not POLICY.exists():
        raise SystemExit("FAIL_POLICY_MISSING")
    return json.loads(POLICY.read_text())

def forbidden(path: str, policy: Dict[str, Any]) -> bool:
    low = path.lower()
    return any(p.lower() in low for p in policy["forbidden_patterns"])

def write_gitignore(root: Path) -> None:
    (root / ".gitignore").write_text("""__pycache__/
*.pyc
*.pyo
*.swp
*.tmp

.env
*.key
*.pem
secrets/

data/
logs/
*.log
*.pid

*.sqlite
*.sqlite-*
*.db
*.db-*

*.bak
*.backup
*.old
*.BROKEN*
*broken*
*_before_*
*_backup_*
audit_export_*
""")

def db_check(root: Path, policy: Dict[str, Any]) -> Dict[str, Any]:
    db = root / policy["paths"]["db"]
    out = {"exists": db.exists(), "size_mb": None, "quick_check": None, "error": None}
    if db.exists():
        out["size_mb"] = round(db.stat().st_size / 1024 / 1024, 2)
    else:
        out["error"] = "DB_MISSING"
        return out

    try:
        con = sqlite3.connect(str(db), timeout=30)
        out["quick_check"] = con.execute("PRAGMA quick_check").fetchone()[0]
        con.close()
    except Exception as e:
        out["error"] = repr(e)

    return out

def runtime_check(root: Path, policy: Dict[str, Any]) -> Dict[str, Any]:
    p = root / "data/v22_1_runtime_manager/runtime_health.json"
    if not p.exists():
        return {"exists": False, "error": "RUNTIME_HEALTH_MISSING"}

    try:
        data = json.loads(p.read_text())
    except Exception as e:
        return {"exists": True, "error": repr(e)}

    ok_verdicts = policy["runtime_policy"]["required_runtime_verdicts"]
    return {
        "exists": True,
        "verdict": data.get("verdict"),
        "db": data.get("db"),
        "enabled_count": data.get("enabled_count"),
        "running_count": data.get("running_count"),
        "problems": data.get("problems"),
        "valid": data.get("verdict") in ok_verdicts and data.get("db", {}).get("quick_check") == "ok",
        "payload": data,
    }

def process_rows(pattern: str) -> List[str]:
    p = sh("ps -ef", LIVE, check=False)
    rows = []
    rx = re.compile(pattern)
    for line in p.stdout.splitlines():
        if "grep" in line:
            continue
        if rx.search(line):
            rows.append(line)
    return rows

def service_check(policy: Dict[str, Any]) -> List[Dict[str, Any]]:
    services = []
    for s in policy["services"]:
        rows = process_rows(s["pattern"])
        item = {
            "name": s["name"],
            "enabled": s["enabled"],
            "critical": s["critical"],
            "running": len(rows),
            "script": s["script"],
            "script_exists": (LIVE / s["script"]).exists(),
            "status": "UNKNOWN",
        }

        if s["enabled"]:
            if len(rows) == 0:
                item["status"] = "MISSING"
            elif len(rows) == 1:
                item["status"] = "OK"
            else:
                item["status"] = "DUPLICATE"
        else:
            item["status"] = "DISABLED_OK" if len(rows) == 0 else "DISABLED_BUT_RUNNING"

        services.append(item)
    return services

def log_scan(root: Path) -> Dict[str, Any]:
    candidates = [
        root / "data/v22_1_runtime_manager/runtime_manager_stderr.log",
        root / "data/v17_8_1/adapter_stderr.log",
        root / "data/v18_9_1_data_plane/gateway_stderr.log",
        root / "data/v18_2_market_context/manual_service_stderr.log",
        root / "data/v17_5_1/manual_service_stderr.log",
        root / "data/v17_6_1/manual_service_stderr.log",
    ]

    out = {}
    for p in candidates:
        if not p.exists():
            continue
        try:
            lines = p.read_text(errors="ignore").splitlines()[-80:]
        except Exception:
            continue
        hits = [x[-220:] for x in lines if ERROR_RE.search(x)]
        out[str(p.relative_to(root))] = {
            "hits_tail": hits[-8:],
            "hit_count": len(hits),
            "mtime_age_min": round((datetime.now(timezone.utc).timestamp() - p.stat().st_mtime) / 60, 2),
        }
    return out

def discover_release_files(policy: Dict[str, Any]) -> List[str]:
    files = list(policy["release_allowlist"])

    for s in policy["services"]:
        script = s.get("script")
        if script and (LIVE / script).exists():
            files.append(script)

    clean = []
    seen = set()
    for f in files:
        if f in seen:
            continue
        seen.add(f)
        clean.append(f)
    return clean

def validate_files(root: Path, files: List[str], policy: Dict[str, Any]) -> Dict[str, Any]:
    checked = {"python": [], "shell": [], "json": [], "missing": [], "forbidden": []}

    for rel in files:
        if forbidden(rel, policy):
            checked["forbidden"].append(rel)
            continue

        p = root / rel
        if not p.exists():
            checked["missing"].append(rel)
            continue

        if rel.endswith(".py"):
            run([sys.executable, "-m", "py_compile", rel], root, check=True)
            checked["python"].append(rel)
        elif rel.endswith(".sh"):
            run(["bash", "-n", rel], root, check=True)
            checked["shell"].append(rel)
        elif rel.endswith(".json"):
            json.loads(p.read_text())
            checked["json"].append(rel)

    return checked

def validate_live(strict: bool = False, write: bool = True) -> Dict[str, Any]:
    OUT.mkdir(parents=True, exist_ok=True)
    policy = load_policy()

    report: Dict[str, Any] = {
        "version": VERSION,
        "ts": utc(),
        "strict": strict,
        "db": db_check(LIVE, policy),
        "runtime": runtime_check(LIVE, policy),
        "services": service_check(policy),
        "logs": log_scan(LIVE),
        "git_live": git_state(LIVE),
        "problems": [],
        "warnings": [],
        "verdict": "UNKNOWN",
    }

    if report["db"].get("quick_check") != "ok":
        report["problems"].append("DB_NOT_OK")

    if not report["runtime"].get("valid"):
        report["problems"].append("RUNTIME_NOT_VALID")

    for s in report["services"]:
        if s["enabled"] and s["critical"] and s["status"] == "MISSING":
            report["problems"].append(f"CRITICAL_SERVICE_MISSING:{s['name']}")
        if s["enabled"] and s["status"] == "DUPLICATE":
            report["warnings"].append(f"SERVICE_DUPLICATE:{s['name']}")
        if not s["enabled"] and s["status"] == "DISABLED_BUT_RUNNING":
            report["problems"].append(f"DISABLED_SERVICE_RUNNING:{s['name']}")
        if s["enabled"] and s["critical"] and not s["script_exists"]:
            report["problems"].append(f"CRITICAL_SCRIPT_MISSING:{s['name']}")

    for path, info in report["logs"].items():
        if info["hit_count"] > 0 and info["mtime_age_min"] <= 30:
            report["warnings"].append(f"RECENT_LOG_ERRORS:{path}:{info['hit_count']}")

    if strict and report["warnings"]:
        report["problems"].append("STRICT_WARNINGS_PRESENT")

    report["verdict"] = "BLOCKED" if report["problems"] else ("WARN" if report["warnings"] else "OK")

    if write:
        save_report(report)
        write_db_health(report)

    return report

def git_state(root: Path) -> Dict[str, Any]:
    if not (root / ".git").exists():
        return {"exists": False}
    return {
        "exists": True,
        "branch": run(["git", "branch", "--show-current"], root, check=False).stdout.strip(),
        "head": run(["git", "log", "--oneline", "-1"], root, check=False).stdout.strip(),
        "status_head": run(["git", "status", "--short"], root, check=False).stdout.splitlines()[:80],
        "remote": run(["git", "remote", "get-url", "origin"], root, check=False).stdout.strip(),
    }

def save_report(report: Dict[str, Any]) -> None:
    REPORT_JSON.write_text(json.dumps(report, indent=2, sort_keys=True))
    SUMMARY_MD.write_text(summary_text(report))

def summary_text(r: Dict[str, Any]) -> str:
    lines = [
        f"# {VERSION}",
        f"- UTC: `{r['ts']}`",
        f"- Verdict: `{r['verdict']}`",
        f"- Strict: `{r['strict']}`",
        f"- DB: `{r['db']}`",
        f"- Runtime: `{ {k: r['runtime'].get(k) for k in ['exists','verdict','enabled_count','running_count','valid','problems']} }`",
        f"- Problems: `{r['problems']}`",
        f"- Warnings: `{r['warnings']}`",
        "",
        "## Services",
    ]

    for s in r["services"]:
        lines.append(
            f"- {s['name']} | {s['status']} | enabled={s['enabled']} | critical={s['critical']} | "
            f"running={s['running']} | script={s['script_exists']}"
        )

    lines.append("")
    lines.append("## Git LIVE")
    lines.append(f"- `{r['git_live']}`")

    lines.append("")
    lines.append("## Log scan")
    for k, v in r["logs"].items():
        lines.append(f"- {k}: hits={v['hit_count']} age={v['mtime_age_min']}m")

    return "\n".join(lines)

def write_db_health(report: Dict[str, Any]) -> None:
    db = LIVE / load_policy()["paths"]["db"]
    if not db.exists():
        return

    try:
        con = sqlite3.connect(str(db), timeout=30, isolation_level=None)
        con.execute("PRAGMA busy_timeout=30000")
        con.execute("""
        CREATE TABLE IF NOT EXISTS institutional_release_gate_health_v23_1 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT,
            version TEXT,
            verdict TEXT,
            strict INTEGER,
            db_quick_check TEXT,
            runtime_verdict TEXT,
            problems TEXT,
            warnings TEXT,
            payload TEXT
        )
        """)
        con.execute("""
        INSERT INTO institutional_release_gate_health_v23_1
        (ts, version, verdict, strict, db_quick_check, runtime_verdict, problems, warnings, payload)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            report["ts"],
            VERSION,
            report["verdict"],
            1 if report["strict"] else 0,
            report["db"].get("quick_check"),
            report["runtime"].get("verdict"),
            json.dumps(report["problems"], sort_keys=True),
            json.dumps(report["warnings"], sort_keys=True),
            json.dumps(report, sort_keys=True),
        ))
        con.close()
    except Exception:
        pass

def ensure_clean_repo(policy: Dict[str, Any]) -> Path:
    clean = Path(policy["paths"]["clean"])

    live_git = git_state(LIVE)
    if not live_git.get("remote") or not live_git.get("branch"):
        raise SystemExit("FAIL_LIVE_GIT_REMOTE_OR_BRANCH_MISSING")

    remote = live_git["remote"]
    branch = live_git["branch"]

    if not (clean / ".git").exists():
        if clean.exists():
            shutil.rmtree(clean)
        run(["git", "clone", "--branch", branch, remote, str(clean)], LIVE, check=True)
    else:
        run(["git", "fetch", "origin", branch], clean, check=True)
        run(["git", "checkout", branch], clean, check=True)
        run(["git", "reset", "--hard", f"origin/{branch}"], clean, check=True)
        run(["git", "clean", "-fdx"], clean, check=True)

    return clean

def release_to_github() -> int:
    policy = load_policy()

    live = validate_live(strict=False, write=True)
    if live["verdict"] == "BLOCKED":
        print(summary_text(live))
        return 2

    clean = ensure_clean_repo(policy)
    write_gitignore(clean)

    files = discover_release_files(policy)

    for rel in files:
        if forbidden(rel, policy):
            raise SystemExit(f"FAIL_FORBIDDEN_ALLOWLIST:{rel}")
        src = LIVE / rel
        dst = clean / rel
        if not src.exists():
            raise SystemExit(f"FAIL_APPROVED_FILE_MISSING:{rel}")
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)

    checked = validate_files(clean, files, policy)
    if checked["missing"] or checked["forbidden"]:
        raise SystemExit(f"FAIL_FILE_VALIDATION:{checked}")

    bad = sh(
        "find . -path './.git' -prune -o -type f | grep -Ei '(\\.sqlite|\\.db|\\.log|\\.pid|\\.bak|backup|broken|audit_export|\\.env|secret|\\.pem|\\.key)' || true",
        clean,
        check=False,
    ).stdout.strip()

    if bad:
        print("FAIL_CLEAN_FORBIDDEN_FILES")
        print(bad)
        return 3

    run(["git", "add"] + files, clean, check=True)
    staged = run(["git", "diff", "--cached", "--name-only"], clean, check=True).stdout.splitlines()

    if not staged:
        print("NO_RELEASE_CHANGES")
        print(run(["git", "status", "--short"], clean, check=False).stdout)
        return 0

    for f in staged:
        if forbidden(f, policy):
            raise SystemExit(f"FAIL_FORBIDDEN_STAGED:{f}")

    msg = f"ops: V23.1 release gate pro {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}"
    run(["git", "commit", "-m", msg], clean, check=True)
    run(["git", "push"], clean, check=True)

    print("GIT_PUSH_OK")
    print(run(["git", "log", "--oneline", "-5"], clean, check=False).stdout)
    print(run(["git", "status", "--short"], clean, check=False).stdout)
    return 0

def status() -> int:
    r = validate_live(strict=False, write=True)
    print(summary_text(r))
    return 0 if r["verdict"] != "BLOCKED" else 2

def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("validate")
    sub.add_parser("validate-strict")
    sub.add_parser("release")
    sub.add_parser("status")

    args = ap.parse_args()

    if args.cmd == "validate":
        r = validate_live(strict=False, write=True)
        print(summary_text(r))
        return 0 if r["verdict"] != "BLOCKED" else 2

    if args.cmd == "validate-strict":
        r = validate_live(strict=True, write=True)
        print(summary_text(r))
        return 0 if r["verdict"] != "BLOCKED" else 2

    if args.cmd == "release":
        return release_to_github()

    if args.cmd == "status":
        return status()

    return 2

if __name__ == "__main__":
    raise SystemExit(main())
