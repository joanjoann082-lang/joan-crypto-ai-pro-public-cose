#!/data/data/com.termux/files/usr/bin/bash
set -e

cd "$(dirname "$0")/.."
export PYTHONPATH=$PWD

echo "===== EXECUTION CONTRACT V1 AUDIT ====="

python -m py_compile \
  joanbot/runner.py \
  joanbot/execution/broker.py \
  joanbot/execution/contract.py \
  joanbot/intelligence/decision.py

python - <<'PY'
from types import SimpleNamespace
from joanbot.execution.contract import evaluate_execution, is_setup_quarantined

def D(**kw):
    base = dict(
        action="OPEN",
        symbol="BTCUSDT",
        side="LONG",
        setup="TREND_BOUNCE_SHORT",
        size_usd=1000,
        entry=100,
        stop_loss=95,
        take_profit_1=105,
        take_profit_2=110,
        final_score=80,
        confidence=80,
    )
    base.update(kw)
    return SimpleNamespace(**base)

tests = []

v = evaluate_execution(D(action="PROBE"), [])
tests.append(("probe_rejected", not v.allowed and v.reason == "REJECT_NON_OPEN_ACTION"))

v = evaluate_execution(D(setup="CAPITULATION_REBOUND_LONG"), [])
tests.append(("quarantined_setup_rejected", not v.allowed and v.reason == "REJECT_QUARANTINED_SETUP"))

v = evaluate_execution(D(), [{"id":"x", "symbol":"BTCUSDT", "side":"SHORT", "setup":"OTHER"}])
tests.append(("hedge_rejected", not v.allowed and v.reason == "REJECT_OPPOSITE_SIDE_HEDGE"))

v = evaluate_execution(D(), [{"id":"x", "symbol":"BTCUSDT", "side":"LONG", "setup":"OTHER"}])
tests.append(("same_symbol_rejected", not v.allowed and v.reason == "REJECT_SYMBOL_ALREADY_OPEN"))

v = evaluate_execution(D(), [])
tests.append(("clean_open_allowed", v.allowed and v.reason == "EXECUTION_ALLOWED"))

failed = [name for name, ok in tests if not ok]
if failed:
    print("FAILED_TESTS:", failed)
    raise SystemExit(1)

print("CONTRACT_UNIT_TESTS_OK")
print("QUARANTINE_CAPITULATION:", is_setup_quarantined("CAPITULATION_REBOUND_LONG"))
PY

echo "===== SOURCE CHECKS ====="
grep -RIn "if best.action == 'OPEN'" joanbot/runner.py
grep -RIn "evaluate_execution\|record_execution_rejection\|EXECUTION_REJECTED" joanbot/execution
grep -RIn "SETUP_QUARANTINED_BY_EXECUTION_CONTRACT" joanbot/intelligence/decision.py
grep -RIn "from ..execution.contract import is_setup_quarantined" joanbot/intelligence/decision.py

echo "===== CURRENT OPEN POSITION CONFLICTS ====="
sqlite3 -header -column data/joanbot_v14.sqlite "
SELECT symbol, COUNT(*) AS open_positions, GROUP_CONCAT(side) AS sides, GROUP_CONCAT(setup) AS setups
FROM positions
WHERE status='OPEN'
GROUP BY symbol;
"

echo "NOTE: existing open conflicts may still appear. Contract prevents new ones; it does not force-close old ones."
echo "EXECUTION_CONTRACT_AUDIT_OK"
