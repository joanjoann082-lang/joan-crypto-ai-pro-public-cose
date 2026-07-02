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

VERSION = "V23.2_RELEASE_GATE_INSTITUTIONAL"

LIVE = Path("/storage/emulated/0/Download/joan_crypto_ai_pro_v14")
POLICY = LIVE / "config/v23_2_operating_policy.json"
OUT = LIVE / "data/v23_2_release_gate"
REPORT = OUT / "release_gate_report.json"
SUMMARY = OUT / "release_gate_summary.md"

def utc() -> str:
    return datetime.now(timezone.utc).isoformat()

def run(cmd: List[str], cwd: Path, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=str(cwd), text=True, capture_output=True, check=check)

def shell(command: str, cwd: Path, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(command, cwd=str(cwd), text=True, capture_output=True, shell=True, check=check)

def load_policy() -> Dict[str, Any]:
    if not POLICY.exists():
        raise SystemExit("FAIL_POLICY_MISSING")
    return json.loads(POLICY.read_text())

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

def forbidden_path(path: str) -> bool:
    p = path.replace("\\", "/").lower()
    base = Path(p).name

    if p.startswith("data/") or p.startswith("logs/"):
        return True
    if base == ".env":
        return True
    if base.endswith((".sqlite", ".db", ".log", ".pid", ".bak", ".backup", ".old", ".pem", ".key")):
        return True
    if ".sqlite-" in p or ".db-" in p:
        return True

    tokens = [
        "broken",
        "_before_",
        "_backup_",
        "audit_export",
        "secret"
    ]
    return any(t in p for t in tokens)

def db_check(policy: Dict[str, Any]) -> Dict[str, Any]:
    db = LIVE / policy["paths"]["db"]
    out = {
        "exists": db.exists(),
        "quick_check": None,
        "size_mb": None,
        "error": None
    }

    if not db.exists():
        out["error"] = "DB_MISSING"
        return out

    out["size_mb"] = round(db.stat().st_size / 1024 / 1024, 2)

    try:
        con = sqlite3.connect(str(db), timeout=30)
        out["quick_check"] = con.execute("PRAGMA quick_check").fetchone()[0]
        con.close()
    except Exception as e:
        out["error"] = repr(e)

    return out

def runtime_check(policy: Dict[str, Any]) -> Dict[str, Any]:
    p = LIVE / policy["paths"]["runtime_health"]

    if not p.exists():
        return {"exists": False, "valid": False, "error": "RUNTIME_HEALTH_MISSING"}

    try:
        payload = json.loads(p.read_text())
    except Exception as e:
        return {"exists": True, "valid": False, "error": repr(e)}

    allowed = set(policy["runtime_policy"]["allowed_runtime_verdicts"])

    valid = (
        payload.get("verdict") in allowed and
        payload.get("db", {}).get("quick_check") == "ok"
    )

    return {
        "exists": True,
        "valid": valid,
        "verdict": payload.get("verdict"),
        "db": payload.get("db"),
        "enabled_count": payload.get("enabled_count"),
        "running_count": payload.get("running_count"),
        "problems": payload.get("problems"),
        "payload": payload
    }

def ps_output() -> List[str]:
    p = shell("ps -ef", LIVE, check=False)
    return p.stdout.splitlines()

def process_count(pattern: str) -> int:
    rx = re.compile(pattern)
    count = 0
    for line in ps_output():
        if "grep" in line:
            continue
        if rx.search(line):
            count += 1
    return count

def services_check(policy: Dict[str, Any]) -> List[Dict[str, Any]]:
    out = []

    for s in policy["services"]:
        count = process_count(s["pattern"])
        script_exists = (LIVE / s["script"]).exists()

        status = "UNKNOWN"
        if s["enabled"]:
            if count < s["min_count"]:
                status = "MISSING"
            elif count > s["max_count"]:
                status = "TOO_MANY"
            else:
                status = "OK"
        else:
            status = "DISABLED_OK" if count == 0 else "DISABLED_BUT_RUNNING"

        out.append({
            "name": s["name"],
            "enabled": s["enabled"],
            "critical": s["critical"],
            "running": count,
            "min_count": s["min_count"],
            "max_count": s["max_count"],
            "script": s["script"],
            "script_exists": script_exists,
            "status": status
        })

    return out

def git_state(root: Path) -> Dict[str, Any]:
    if not (root / ".git").exists():
        return {"exists": False}

    return {
        "exists": True,
        "branch": run(["git", "branch", "--show-current"], root, check=False).stdout.strip(),
        "head": run(["git", "log", "--oneline", "-1"], root, check=False).stdout.strip(),
        "remote": run(["git", "remote", "get-url", "origin"], root, check=False).stdout.strip(),
        "status_head": run(["git", "status", "--short"], root, check=False).stdout.splitlines()[:80]
    }

def validate_live(write: bool = True) -> Dict[str, Any]:
    OUT.mkdir(parents=True, exist_ok=True)

    policy = load_policy()

    report = {
        "version": VERSION,
        "ts": utc(),
        "db": db_check(policy),
        "runtime": runtime_check(policy),
        "services": services_check(policy),
        "git_live": git_state(LIVE),
        "problems": [],
        "warnings": [],
        "verdict": "UNKNOWN"
    }

    if report["db"].get("quick_check") != "ok":
        report["problems"].append("DB_NOT_OK")

    if not report["runtime"].get("valid"):
        report["problems"].append("RUNTIME_NOT_VALID")

    for s in report["services"]:
        if s["enabled"] and s["critical"] and s["status"] == "MISSING":
            report["problems"].append(f"CRITICAL_SERVICE_MISSING:{s['name']}")
        elif s["enabled"] and s["status"] == "MISSING":
            report["warnings"].append(f"OPTIONAL_SERVICE_MISSING:{s['name']}")
        elif s["enabled"] and s["status"] == "TOO_MANY":
            report["warnings"].append(f"SERVICE_TOO_MANY:{s['name']}:{s['running']}>{s['max_count']}")
        elif not s["enabled"] and s["status"] == "DISABLED_BUT_RUNNING":
            report["problems"].append(f"DISABLED_SERVICE_RUNNING:{s['name']}")

        if s["enabled"] and s["critical"] and not s["script_exists"]:
            report["problems"].append(f"CRITICAL_SCRIPT_MISSING:{s['name']}")

    report["verdict"] = "BLOCKED" if report["problems"] else ("WARN" if report["warnings"] else "OK")

    if write:
        REPORT.write_text(json.dumps(report, indent=2, sort_keys=True))
        SUMMARY.write_text(summary(report))
        write_health_db(report, policy)

    return report

def write_health_db(report: Dict[str, Any], policy: Dict[str, Any]) -> None:
    db = LIVE / policy["paths"]["db"]

    if not db.exists():
        return

    try:
        con = sqlite3.connect(str(db), timeout=30, isolation_level=None)
        con.execute("PRAGMA busy_timeout=30000")
        con.execute("""
        CREATE TABLE IF NOT EXISTS institutional_release_gate_health_v23_2 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT,
            version TEXT,
            verdict TEXT,
            db_quick_check TEXT,
            runtime_verdict TEXT,
            problems TEXT,
            warnings TEXT,
            payload TEXT
        )
        """)
        con.execute("""
        INSERT INTO institutional_release_gate_health_v23_2
        (ts, version, verdict, db_quick_check, runtime_verdict, problems, warnings, payload)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            report["ts"],
            VERSION,
            report["verdict"],
            report["db"].get("quick_check"),
            report["runtime"].get("verdict"),
            json.dumps(report["problems"], sort_keys=True),
            json.dumps(report["warnings"], sort_keys=True),
            json.dumps(report, sort_keys=True)
        ))
        con.close()
    except Exception:
        pass

def summary(r: Dict[str, Any]) -> str:
    lines = [
        f"# {VERSION}",
        f"- UTC: `{r['ts']}`",
        f"- Verdict: `{r['verdict']}`",
        f"- DB: `{r['db']}`",
        f"- Runtime: `{ {k: r['runtime'].get(k) for k in ['exists','valid','verdict','enabled_count','running_count','problems']} }`",
        f"- Problems: `{r['problems']}`",
        f"- Warnings: `{r['warnings']}`",
        "",
        "## Services"
    ]

    for s in r["services"]:
        lines.append(
            f"- {s['name']} | {s['status']} | running={s['running']} | "
            f"range={s['min_count']}-{s['max_count']} | enabled={s['enabled']} | critical={s['critical']} | script={s['script_exists']}"
        )

    lines.append("")
    lines.append("## Git LIVE")
    lines.append(f"- branch: `{r['git_live'].get('branch')}`")
    lines.append(f"- head: `{r['git_live'].get('head')}`")
    lines.append(f"- remote: `{r['git_live'].get('remote')}`")
    lines.append("- status_head:")
    for x in r["git_live"].get("status_head", [])[:40]:
        lines.append(f"  - `{x}`")

    return "\n".join(lines)

def ensure_clean_repo(policy: Dict[str, Any]) -> Path:
    clean = Path(policy["paths"]["clean"])
    live_git = git_state(LIVE)

    remote = live_git.get("remote")
    branch = live_git.get("branch")

    if not remote or not branch:
        raise SystemExit("FAIL_LIVE_REMOTE_OR_BRANCH_MISSING")

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

def purge_forbidden_tracked(clean: Path) -> List[str]:
    tracked = run(["git", "ls-files"], clean, check=True).stdout.splitlines()
    bad = [x for x in tracked if forbidden_path(x)]

    if bad:
        run(["git", "rm", "-f", "--"] + bad, clean, check=True)

    return bad

def validate_release_files(root: Path, files: List[str]) -> Dict[str, Any]:
    checked = {"py": [], "sh": [], "json": [], "missing": [], "forbidden": []}

    for rel in files:
        if forbidden_path(rel):
            checked["forbidden"].append(rel)
            continue

        p = root / rel

        if not p.exists():
            checked["missing"].append(rel)
            continue

        if rel.endswith(".py"):
            run([sys.executable, "-m", "py_compile", rel], root, check=True)
            checked["py"].append(rel)
        elif rel.endswith(".sh"):
            run(["bash", "-n", rel], root, check=True)
            checked["sh"].append(rel)
        elif rel.endswith(".json"):
            json.loads(p.read_text())
            checked["json"].append(rel)

    return checked

def release_to_github() -> int:
    policy = load_policy()

    live = validate_live(write=True)
    print(summary(live))

    if live["verdict"] == "BLOCKED":
        return 2

    clean = ensure_clean_repo(policy)
    write_gitignore(clean)

    removed = purge_forbidden_tracked(clean)

    files = list(dict.fromkeys(policy["release_allowlist"]))

    for rel in files:
        if forbidden_path(rel):
            raise SystemExit(f"FAIL_FORBIDDEN_ALLOWLIST:{rel}")

        src = LIVE / rel
        dst = clean / rel

        if not src.exists():
            raise SystemExit(f"FAIL_ALLOWLIST_FILE_MISSING:{rel}")

        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)

    checked = validate_release_files(clean, files)

    if checked["missing"] or checked["forbidden"]:
        raise SystemExit(f"FAIL_RELEASE_FILE_VALIDATION:{checked}")

    bad_remaining = []
    for p in clean.rglob("*"):
        if ".git" in p.parts:
            continue
        if p.is_file():
            rel = str(p.relative_to(clean))
            if forbidden_path(rel):
                bad_remaining.append(rel)

    if bad_remaining:
        raise SystemExit(f"FAIL_FORBIDDEN_REMAINING_IN_CLEAN:{bad_remaining[:30]}")

    run(["git", "add"] + files, clean, check=True)

    staged = run(["git", "diff", "--cached", "--name-only"], clean, check=True).stdout.splitlines()

    for f in staged:
        if forbidden_path(f):
            raise SystemExit(f"FAIL_FORBIDDEN_STAGED:{f}")

    if not staged and not removed:
        print("NO_RELEASE_CHANGES")
        return 0

    msg = f"ops: V23.2 institutional release gate cleanup {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}"
    run(["git", "commit", "-m", msg], clean, check=True)
    run(["git", "push"], clean, check=True)

    print("GIT_PUSH_OK")
    print("removed_forbidden_tracked:", removed)
    print(run(["git", "log", "--oneline", "-5"], clean, check=False).stdout)
    print(run(["git", "status", "--short"], clean, check=False).stdout)
    return 0

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["validate", "status", "release"])
    args = ap.parse_args()

    if args.cmd in ("validate", "status"):
        r = validate_live(write=True)
        print(summary(r))
        return 0 if r["verdict"] != "BLOCKED" else 2

    if args.cmd == "release":
        return release_to_github()

    return 2

if __name__ == "__main__":
    raise SystemExit(main())
