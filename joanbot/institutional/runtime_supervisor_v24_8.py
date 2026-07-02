#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import signal
import sqlite3
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from joanbot.institutional.kernel_contract_v24_6 import OFFICIAL_SERVICES

ROOT = Path("/storage/emulated/0/Download/joan_crypto_ai_pro_v14")
DB = ROOT / "data" / "joanbot_v14.sqlite"
OUT = ROOT / "data" / "v22_1_runtime_manager"
STATE_PATH = OUT / "runtime_supervisor_state_v24_8.json"
OUT.mkdir(parents=True, exist_ok=True)

VERSION = "V24_8_PRO_INSTITUTIONAL_RUNTIME_SUPERVISOR"

STARTUP_GRACE_SEC = 150
RESTART_WINDOW_SEC = 1800
MAX_RESTARTS_IN_WINDOW = 6
COOLDOWN_SEC = 240

PRICE_MAX_AGE_MIN = 5.0
BRAIN_MAX_AGE_MIN = 12.0
ADAPTER_HEALTH_MAX_AGE_MIN = 4.0


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def now_epoch() -> float:
    return time.time()


def parse_ts(x: Any) -> Optional[datetime]:
    if not x:
        return None
    try:
        return datetime.fromisoformat(str(x).replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


def age_min(x: Any) -> Optional[float]:
    d = parse_ts(x)
    if not d:
        return None
    return round((datetime.now(timezone.utc) - d).total_seconds() / 60.0, 4)


def qid(x: str) -> str:
    return '"' + str(x).replace('"', '""') + '"'


def load_state() -> Dict[str, Any]:
    if not STATE_PATH.exists():
        return {"version": VERSION, "services": {}}
    try:
        return json.loads(STATE_PATH.read_text())
    except Exception:
        return {"version": VERSION, "services": {}}


def save_state(state: Dict[str, Any]) -> None:
    state["version"] = VERSION
    state["updated_at"] = utc_now()
    STATE_PATH.write_text(json.dumps(state, indent=2, sort_keys=True, default=str))


def service_state(state: Dict[str, Any], name: str) -> Dict[str, Any]:
    services = state.setdefault("services", {})
    return services.setdefault(name, {
        "restart_epochs": [],
        "last_start_epoch": None,
        "last_ok_epoch": None,
        "cooldown_until": None,
        "last_health": None,
    })


def db_connect() -> sqlite3.Connection:
    con = sqlite3.connect(str(DB), timeout=20)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=20000")
    return con


def db_health() -> Dict[str, Any]:
    out = {
        "exists": DB.exists(),
        "size_mb": round(DB.stat().st_size / 1024 / 1024, 2) if DB.exists() else None,
        "quick_check": None,
        "error": None,
    }
    if not DB.exists():
        return out
    try:
        con = db_connect()
        out["quick_check"] = con.execute("PRAGMA quick_check").fetchone()[0]
        con.close()
    except Exception as e:
        out["error"] = repr(e)
    return out


def table_exists(con: sqlite3.Connection, t: str) -> bool:
    return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (t,)).fetchone() is not None


def cols(con: sqlite3.Connection, t: str) -> List[str]:
    if not table_exists(con, t):
        return []
    return [r[1] for r in con.execute(f"PRAGMA table_info({qid(t)})")]


def latest_rows(con: sqlite3.Connection, t: str, n: int = 1) -> List[Dict[str, Any]]:
    if not table_exists(con, t):
        return []
    c = cols(con, t)
    if not c:
        return []
    order = "id" if "id" in c else ("ts" if "ts" in c else c[0])
    return [dict(r) for r in con.execute(f"SELECT * FROM {qid(t)} ORDER BY {qid(order)} DESC LIMIT {n}")]


def ps_lines() -> List[str]:
    for cmd in (["ps", "-ef"], ["ps", "aux"]):
        try:
            r = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, timeout=20)
            if r.stdout:
                return r.stdout.splitlines()
        except Exception:
            pass
    return []


def parse_pid(line: str) -> Optional[int]:
    parts = line.split()
    if len(parts) < 2:
        return None
    try:
        return int(parts[1])
    except Exception:
        return None


def regex_hit(pattern: str, line: str) -> bool:
    try:
        return bool(re.search(pattern, line))
    except Exception:
        return pattern in line


def service_rows(service: Dict[str, Any]) -> List[Dict[str, Any]]:
    pattern = str(service.get("pattern") or "")
    script = str(service.get("script") or "")
    script_base = os.path.basename(script) if script else ""
    me = os.getpid()

    rows: List[Dict[str, Any]] = []
    for line in ps_lines():
        pid = parse_pid(line)
        if not pid or pid == me:
            continue
        if "grep " in line:
            continue

        p_hit = regex_hit(pattern, line) if pattern else False
        s_hit = bool(script_base and script_base in line)

        if p_hit or s_hit:
            rows.append({
                "pid": pid,
                "cmd": line,
                "wrapper": s_hit,
                "worker": p_hit and not s_hit,
            })

    return rows


def split_rows(service: Dict[str, Any], rows: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    script = str(service.get("script") or "")
    script_base = os.path.basename(script) if script else ""

    wrappers = []
    workers = []
    for r in rows:
        cmd = r["cmd"]
        if script_base and script_base in cmd:
            wrappers.append(r)
        else:
            workers.append(r)
    return wrappers, workers


def kill_pid(pid: int, hard: bool = False) -> None:
    try:
        os.kill(pid, signal.SIGKILL if hard else signal.SIGTERM)
    except Exception:
        pass


def kill_rows(rows: List[Dict[str, Any]]) -> List[int]:
    pids = sorted({int(r["pid"]) for r in rows})
    for pid in pids:
        kill_pid(pid, hard=False)
    time.sleep(1)
    for pid in pids:
        kill_pid(pid, hard=True)
    return pids


def start_service(service: Dict[str, Any]) -> Dict[str, Any]:
    script = str(service.get("script") or "")
    if not script:
        return {"started": False, "pid": None, "error": "NO_SCRIPT"}

    script_path = ROOT / script
    if not script_path.exists():
        return {"started": False, "pid": None, "error": f"SCRIPT_NOT_FOUND:{script}"}

    stdout_path = ROOT / str(service.get("stdout") or f"data/v22_1_runtime_manager/{service['name']}_stdout.log")
    stderr_path = ROOT / str(service.get("stderr") or f"data/v22_1_runtime_manager/{service['name']}_stderr.log")
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    stderr_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        out = open(stdout_path, "a")
        err = open(stderr_path, "a")
        p = subprocess.Popen(
            ["bash", str(script_path)],
            cwd=str(ROOT),
            stdout=out,
            stderr=err,
            start_new_session=True,
        )
        return {"started": True, "pid": p.pid, "error": None}
    except Exception as e:
        return {"started": False, "pid": None, "error": repr(e)}


def in_startup_grace(st: Dict[str, Any]) -> bool:
    last = st.get("last_start_epoch")
    if not last:
        return False
    return (now_epoch() - float(last)) <= STARTUP_GRACE_SEC


def restart_allowed(st: Dict[str, Any]) -> Tuple[bool, str]:
    t = now_epoch()
    cooldown_until = st.get("cooldown_until")
    if cooldown_until and t < float(cooldown_until):
        return False, f"COOLDOWN_UNTIL_{cooldown_until}"

    restarts = [float(x) for x in st.get("restart_epochs", []) if t - float(x) <= RESTART_WINDOW_SEC]
    st["restart_epochs"] = restarts

    if len(restarts) >= MAX_RESTARTS_IN_WINDOW:
        st["cooldown_until"] = t + COOLDOWN_SEC
        return False, "TEMPORARY_RESTART_COOLDOWN"

    return True, "RESTART_ALLOWED"


def mark_start(st: Dict[str, Any]) -> None:
    t = now_epoch()
    st["last_start_epoch"] = t
    restarts = [float(x) for x in st.get("restart_epochs", []) if t - float(x) <= RESTART_WINDOW_SEC]
    restarts.append(t)
    st["restart_epochs"] = restarts


def health_price(con: sqlite3.Connection) -> Dict[str, Any]:
    try:
        from joanbot.institutional.canonical_market_data_contract_v24_9_final import canonical_market_health
        return canonical_market_health(con)
    except Exception as e:
        return {"ok": False, "reason": "FINAL_CANONICAL_MARKET_HEALTH_EXCEPTION", "error": repr(e)}


def health_brain(con: sqlite3.Connection) -> Dict[str, Any]:
    t = "institutional_quant_brain_v17_5_1"
    if not table_exists(con, t):
        return {"ok": False, "reason": "BRAIN_TABLE_MISSING"}

    rows = latest_rows(con, t, 1)
    if not rows:
        return {"ok": False, "reason": "BRAIN_NO_ROWS"}

    r = rows[0]
    a = age_min(r.get("ts"))
    ok = a is not None and a <= BRAIN_MAX_AGE_MIN
    return {
        "ok": ok,
        "reason": "BRAIN_OK" if ok else "BRAIN_STALE",
        "age_min": a,
        "row": {k: r.get(k) for k in ["id", "ts", "symbol", "side", "setup", "authority_state", "brain_score"] if k in r},
    }


def health_adapter(con: sqlite3.Connection) -> Dict[str, Any]:
    t = "institutional_v24_4_canonical_adapter_health"
    if not table_exists(con, t):
        return {"ok": False, "reason": "ADAPTER_HEALTH_TABLE_MISSING"}

    rows = latest_rows(con, t, 1)
    if not rows:
        return {"ok": False, "reason": "ADAPTER_NO_HEALTH_ROWS"}

    r = rows[0]
    a = age_min(r.get("ts"))
    errors = int(r.get("errors") or 0)
    qc = str(r.get("quick_check") or "").lower()
    ok = a is not None and a <= ADAPTER_HEALTH_MAX_AGE_MIN and errors == 0 and qc == "ok"

    return {
        "ok": ok,
        "reason": "ADAPTER_OK" if ok else "ADAPTER_STALE_OR_ERRORS",
        "age_min": a,
        "errors": errors,
        "quick_check": qc,
        "row": {k: r.get(k) for k in ["id", "ts", "pending_intents", "opened_positions", "managed_positions", "closed_positions", "rejected_intents", "errors"] if k in r},
    }


def logical_health(service: Dict[str, Any], rows: List[Dict[str, Any]], st: Dict[str, Any]) -> Dict[str, Any]:
    name = str(service.get("name") or "")

    if not service.get("enabled"):
        return {"ok": True, "reason": "DISABLED"}

    if not rows:
        return {"ok": False, "reason": "PROCESS_NOT_RUNNING"}

    if in_startup_grace(st):
        return {"ok": True, "reason": "STARTUP_GRACE"}

    try:
        con = db_connect()

        if name == "V24_1_PRICE_CONTRACT":
            out = health_price(con)
            con.close()
            return out

        if name == "QUANT_BRAIN":
            out = health_brain(con)
            con.close()
            return out

        if name == "V24_4_CANONICAL_ADAPTER":
            out = health_adapter(con)
            con.close()
            return out

        con.close()
    except Exception as e:
        return {"ok": False, "reason": "HEALTH_PROBE_EXCEPTION", "error": repr(e)}

    return {"ok": True, "reason": "PROCESS_ALIVE"}


def supervise_service(service: Dict[str, Any], state: Dict[str, Any]) -> Dict[str, Any]:
    name = str(service.get("name"))
    enabled = bool(service.get("enabled"))
    critical = bool(service.get("critical"))
    st = service_state(state, name)

    rows = service_rows(service)
    wrappers, workers = split_rows(service, rows)

    result = {
        "name": name,
        "enabled": enabled,
        "critical": critical,
        "running": len(rows),
        "wrapper_count": len(wrappers),
        "worker_count": len(workers),
        "pids": [r["pid"] for r in rows],
        "action": "NONE",
        "reason": None,
        "health": None,
        "started_pid": None,
        "killed_pids": [],
        "errors": [],
    }

    if not enabled:
        if rows:
            result["killed_pids"] = kill_rows(rows)
            result["action"] = "DISABLED_KILLED"
        else:
            result["action"] = "DISABLED_OK"
        result["health"] = {"ok": True, "reason": "DISABLED"}
        return result

    # Multiple wrapper loops are real duplication. Wrapper + worker is normal.
    if len(wrappers) > 1:
        result["killed_pids"] = kill_rows(rows)
        allowed, why = restart_allowed(st)
        if allowed:
            started = start_service(service)
            mark_start(st)
            result["started_pid"] = started.get("pid")
            result["action"] = "DEDUP_RESTART"
            result["reason"] = why
            if started.get("error"):
                result["errors"].append(started["error"])
        else:
            result["action"] = "DEDUP_COOLDOWN"
            result["reason"] = why
        return result

    if not rows:
        allowed, why = restart_allowed(st)
        if allowed:
            started = start_service(service)
            mark_start(st)
            result["started_pid"] = started.get("pid")
            result["action"] = "START"
            result["reason"] = why
            if started.get("error"):
                result["errors"].append(started["error"])
        else:
            result["action"] = "MISSING_COOLDOWN"
            result["reason"] = why
        return result

    h = logical_health(service, rows, st)
    result["health"] = h

    if h.get("ok"):
        st["last_ok_epoch"] = now_epoch()
        st["last_health"] = h
        result["action"] = "OK"
        result["reason"] = h.get("reason")
        return result

    # Service is alive but logically unhealthy: restart, but not permanently disable.
    allowed, why = restart_allowed(st)
    if allowed:
        result["killed_pids"] = kill_rows(rows)
        started = start_service(service)
        mark_start(st)
        result["started_pid"] = started.get("pid")
        result["action"] = "RESTART_UNHEALTHY"
        result["reason"] = f"{h.get('reason')}|{why}"
        if started.get("error"):
            result["errors"].append(started["error"])
    else:
        result["action"] = "UNHEALTHY_COOLDOWN"
        result["reason"] = f"{h.get('reason')}|{why}"

    st["last_health"] = h
    return result


def write_report(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    db = db_health()

    enabled = [r for r in results if r["enabled"]]
    critical = [r for r in enabled if r["critical"]]

    problems = []
    recovering = []

    for r in critical:
        h = r.get("health") or {}
        if r["running"] <= 0 and r["action"] not in {"START", "DEDUP_RESTART", "RESTART_UNHEALTHY"}:
            problems.append(f"CRITICAL_NOT_RUNNING:{r['name']}")

        if r.get("errors"):
            problems.append(f"CRITICAL_ERRORS:{r['name']}:{r['errors']}")

        if r["action"] in {"START", "DEDUP_RESTART", "RESTART_UNHEALTHY"}:
            recovering.append(r["name"])

        if h and not h.get("ok") and r["action"] not in {"RESTART_UNHEALTHY", "START"}:
            problems.append(f"CRITICAL_HEALTH_BAD:{r['name']}:{h.get('reason')}")

    if problems:
        verdict = "DEGRADED"
    elif recovering:
        verdict = "RECOVERING"
    else:
        verdict = "OK_RUNTIME_MANAGER_ACTIVE"

    report = {
        "version": VERSION,
        "ts": utc_now(),
        "verdict": verdict,
        "db": db,
        "enabled": len(enabled),
        "running_enabled": sum(1 for r in enabled if r["running"] > 0 or r["action"] in {"START", "DEDUP_RESTART", "RESTART_UNHEALTHY"}),
        "critical_enabled": len(critical),
        "critical_running_or_recovering": sum(1 for r in critical if r["running"] > 0 or r["action"] in {"START", "DEDUP_RESTART", "RESTART_UNHEALTHY"}),
        "recovering": recovering,
        "problems": problems,
        "services": results,
    }

    (OUT / "runtime_health.json").write_text(json.dumps(report, indent=2, sort_keys=True, default=str))

    lines = []
    lines.append(f"# {VERSION}")
    lines.append(f"- UTC: `{report['ts']}`")
    lines.append(f"- Verdict: `{report['verdict']}`")
    lines.append(f"- DB: `{report['db']}`")
    lines.append(f"- Enabled: `{report['enabled']}`")
    lines.append(f"- Running/recovering enabled: `{report['running_enabled']}`")
    lines.append(f"- Critical enabled: `{report['critical_enabled']}`")
    lines.append(f"- Critical running/recovering: `{report['critical_running_or_recovering']}`")
    lines.append(f"- Recovering: `{report['recovering']}`")
    lines.append(f"- Problems: `{report['problems']}`")
    lines.append("")
    lines.append("## Services")

    for r in results:
        h = r.get("health") or {}
        lines.append(
            f"- {r['name']} | enabled={r['enabled']} critical={r['critical']} "
            f"running={r['running']} wrappers={r['wrapper_count']} workers={r['worker_count']} "
            f"action={r['action']} reason={r.get('reason')} "
            f"health_ok={h.get('ok')} health_reason={h.get('reason')} "
            f"pids={r['pids']} killed={r.get('killed_pids')} started={r.get('started_pid')} errors={r.get('errors')}"
        )

    md = "\n".join(lines)
    (OUT / "runtime_summary.md").write_text(md)
    print(md)

    return report


def run_once() -> Dict[str, Any]:
    state = load_state()
    results = []
    for service in OFFICIAL_SERVICES:
        results.append(supervise_service(service, state))
    save_state(state)
    return write_report(results)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--daemon", action="store_true")
    ap.add_argument("--interval", type=int, default=60)
    args = ap.parse_args()

    if args.daemon:
        while True:
            try:
                run_once()
            except Exception as e:
                fatal = {
                    "ts": utc_now(),
                    "version": VERSION,
                    "fatal": repr(e),
                }
                (OUT / "runtime_fatal_v24_8.json").write_text(json.dumps(fatal, indent=2, sort_keys=True))
                print("RUNTIME_FATAL", repr(e), flush=True)
            time.sleep(max(10, int(args.interval)))
    else:
        run_once()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
