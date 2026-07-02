#!/data/data/com.termux/files/usr/bin/bash

cd "$(dirname "$0")/.."
export PYTHONPATH=$PWD

echo "===== RETENTION V1 INSTITUTIONAL AUDIT ====="

FAIL=0

echo "===== NO RUNTIME ====="
ps -ef | grep -Ei "joanbot.runner|telegram_command_bot|dashboard" | grep -v grep && FAIL=1 || echo NO_RUNTIME_OK

echo "===== COMPILE ====="
python -m py_compile joanbot/ops/retention_v1.py joanbot/runner.py || FAIL=1

echo "===== RUNNER INTEGRATION ====="
grep -q "run_retention_safe" joanbot/runner.py || FAIL=1
grep -q "def step_retention" joanbot/runner.py || FAIL=1
grep -q "self.step_retention();" joanbot/runner.py || FAIL=1

echo "===== GIT DATA HYGIENE ====="
git check-ignore data/joanbot_v14.sqlite >/dev/null || { echo "DB_NOT_IGNORED_BY_GIT"; FAIL=1; }

BAD_TRACKED=$(git ls-files | grep -Ei '(^data/|\.sqlite$|\.sqlite3$|\.db$|\.zip$|\.bak|\.log$)' || true)
if [ -n "$BAD_TRACKED" ]; then
  echo "BAD_TRACKED_ARTIFACTS:"
  echo "$BAD_TRACKED"
  FAIL=1
else
  echo NO_BAD_TRACKED_ARTIFACTS_OK
fi

echo "===== DRY RUN ====="
python -m joanbot.ops.retention_v1 || FAIL=1

echo "===== APPLY ====="
python -m joanbot.ops.retention_v1 --apply || FAIL=1

echo "===== LIMIT VALIDATION ====="
python - <<'PY' || FAIL=1
import sqlite3, sys

limits = {
    "alerts": 300,
    "decisions": 3000,
    "market_snapshots": 3000,
    "derivatives_snapshots": 3000,
    "orderflow_snapshots": 3000,
    "features": 3000,
    "forward_cases": 5000,
    "forward_results": 5000,
    "runtime_events": 500,
    "news_events": 500,
    "candles": 10000,
}

protected = ["positions", "trades", "position_events", "edge_memory", "state_integrity_events"]

con = sqlite3.connect("data/joanbot_v14.sqlite")
bad = []

for t, lim in limits.items():
    exists = con.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?", (t,)).fetchone()[0]
    if not exists:
        continue
    n = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    print(t, n, "limit", lim)
    if n > lim:
        bad.append((t, n, lim))

for t in protected:
    exists = con.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?", (t,)).fetchone()[0]
    if exists:
        n = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        print("PROTECTED", t, n)

ic = con.execute("PRAGMA integrity_check").fetchone()[0]
print("INTEGRITY:", ic)
if ic != "ok":
    bad.append(("integrity", ic, "ok"))

if bad:
    print("LIMIT_OR_INTEGRITY_FAIL:", bad)
    sys.exit(1)
PY

echo "===== FILE SIZE ====="
ls -lh data/joanbot_v14.sqlite*
df -h /storage/emulated

if [ "$FAIL" -ne 0 ]; then
  echo "RETENTION_V1_AUDIT_FAIL"
  exit 1
fi

echo "RETENTION_V1_INSTITUTIONAL_AUDIT_OK"
