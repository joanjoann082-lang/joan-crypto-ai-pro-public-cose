#!/data/data/com.termux/files/usr/bin/bash
set -e

cd "$(dirname "$0")/.."
export PYTHONPATH=$PWD

echo "===== STATE INTEGRITY V1 AUDIT ====="

test -f joanbot/execution/contract.py
grep -q "if best.action == 'OPEN'" joanbot/runner.py
grep -q "evaluate_execution" joanbot/execution/broker.py
grep -q "SETUP_QUARANTINED_BY_EXECUTION_CONTRACT" joanbot/intelligence/decision.py

python -m py_compile joanbot/ops/state_integrity_v1.py

python -m joanbot.ops.state_integrity_v1 --max-age-min 15

echo "===== CURRENT OPEN CONFLICTS ====="
sqlite3 -header -column data/joanbot_v14.sqlite "
SELECT symbol, COUNT(*) AS open_positions, GROUP_CONCAT(side) AS sides, GROUP_CONCAT(setup) AS setups
FROM positions
WHERE status='OPEN'
GROUP BY symbol;
"

echo "STATE_INTEGRITY_V1_AUDIT_OK"
