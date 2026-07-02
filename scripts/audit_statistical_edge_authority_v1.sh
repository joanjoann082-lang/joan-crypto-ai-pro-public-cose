#!/data/data/com.termux/files/usr/bin/bash
set -e

cd "$(dirname "$0")/.."
export PYTHONPATH=$PWD

FAIL=0

echo "===== STATISTICAL EDGE AUTHORITY V1.1 AUDIT ====="

echo "===== BRANCH ====="
git branch --show-current

echo "===== NO RUNTIME ====="
if ps -ef | grep -Ei "joanbot.runner|telegram_command_bot|dashboard" | grep -v grep; then
  echo "RUNTIME_ACTIVE_ABORT"
  exit 1
else
  echo "NO_RUNTIME_OK"
fi

echo "===== COMPILE ====="
python -m py_compile \
  joanbot/intelligence/statistical_edge_authority_v1.py \
  joanbot/intelligence/decision.py \
  joanbot/intelligence/memory.py \
  joanbot/intelligence/risk.py \
  joanbot/execution/contract.py \
  joanbot/runner.py \
  || FAIL=1

echo "===== SOURCE CHECKS ====="
grep -q "StatisticalEdgeAuthorityV1" joanbot/intelligence/decision.py || FAIL=1
grep -q "statistical_edge_authority_v1" joanbot/intelligence/decision.py || FAIL=1
grep -q "OPEN_BLOCKED_BY_STATISTICAL_EDGE_AUTHORITY_V1" joanbot/intelligence/decision.py || FAIL=1
grep -q "authority.get('allow_open')" joanbot/intelligence/decision.py || FAIL=1
grep -q "authority.get('allow_probe')" joanbot/intelligence/decision.py || FAIL=1
grep -q "POSITION_LEVEL" joanbot/intelligence/statistical_edge_authority_v1.py || FAIL=1

if grep -q "is_setup_quarantined(cand.setup)" joanbot/intelligence/decision.py; then
  echo "BAD_BOUNDARY_DECISION_IMPORTS_EXECUTION_QUARANTINE"
  FAIL=1
else
  echo "BOUNDARY_OK_DECISION_DOES_NOT_USE_EXECUTION_QUARANTINE"
fi

echo "===== DB READ ONLY AUTHORITY OUTPUT ====="
python -m joanbot.intelligence.statistical_edge_authority_v1 || FAIL=1

echo "===== POLICY VALIDATION ====="
python - <<'PY' || FAIL=1
from types import SimpleNamespace
from joanbot.intelligence.statistical_edge_authority_v1 import StatisticalEdgeAuthorityV1

auth = StatisticalEdgeAuthorityV1()

tests = [
    ("BTCUSDT", "LONG", "CAPITULATION_REBOUND_LONG"),
    ("ETHUSDT", "LONG", "CAPITULATION_REBOUND_LONG"),
    ("BTCUSDT", "SHORT", "TREND_BOUNCE_SHORT"),
    ("ETHUSDT", "SHORT", "TREND_BOUNCE_SHORT"),
]

bad = []

for symbol, side, setup in tests:
    c = SimpleNamespace(symbol=symbol, side=side, setup=setup)
    v = auth.evaluate(c, {}, {})
    print(symbol, side, setup, v["authority_status"], "open", v["allow_open"], "probe", v["allow_probe"], "scope", v["position_scope"], "n", v["position_n"], "pnl", v["position_pnl_usd"], "pf", v["position_profit_factor"])

    if setup == "CAPITULATION_REBOUND_LONG" and v["allow_open"]:
        bad.append("CAPITULATION_REBOUND_LONG_OPEN_ALLOWED")

    if v["position_n"] < 12 and v["allow_open"]:
        bad.append(f"OPEN_ALLOWED_LOW_POSITION_SAMPLE:{symbol}:{side}:{setup}:{v['position_n']}")

    if v["authority_status"] in ("QUARANTINED", "BLOCKED") and (v["allow_open"] or v["allow_probe"]):
        bad.append(f"BLOCKED_STATUS_HAS_PERMISSION:{symbol}:{side}:{setup}")

if bad:
    print("POLICY_FAIL", bad)
    raise SystemExit(1)

print("POLICY_OK")
PY

echo "===== DECISION KERNEL SMOKE ====="
python - <<'PY' || FAIL=1
from joanbot.intelligence.decision import DecisionKernel
k = DecisionKernel()
assert hasattr(k, "stat_edge")
print("DECISION_KERNEL_STAT_EDGE_OK")
PY

echo "===== DB SAFETY ====="
python - <<'PY' || FAIL=1
from joanbot.storage import get_db
db=get_db()
rows=db.query("PRAGMA integrity_check")
print("INTEGRITY:", rows[0][list(rows[0].keys())[0]] if rows else "UNKNOWN")
PY

echo "===== GIT DIFF CHECK ====="
git diff --check || FAIL=1

echo "===== STATUS ====="
git status --short

if [ "$FAIL" -eq 0 ]; then
  echo "STATISTICAL_EDGE_AUTHORITY_V1_1_AUDIT_OK"
else
  echo "STATISTICAL_EDGE_AUTHORITY_V1_1_AUDIT_FAIL"
  exit 1
fi
