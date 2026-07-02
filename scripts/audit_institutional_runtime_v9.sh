#!/data/data/com.termux/files/usr/bin/bash
set -e

cd "$(dirname "$0")/.."
export PYTHONPATH=$PWD
FAIL=0

echo "===== INSTITUTIONAL RUNTIME V9 AUDIT ====="

echo "===== NO RUNTIME ====="
if ps -ef | grep -Ei "python.*joanbot.runner|python.*joanbot.orchestrator|python.*joanbot.runtime|joanbot_overnight_supervisor" | grep -v grep; then
  echo "RUNTIME_ACTIVE_ABORT"
  exit 1
else
  echo "NO_RUNTIME_OK"
fi

echo "===== REQUIRED FILES ====="
for f in \
  joanbot/alpha/institutional_edge_factory_v8.py \
  joanbot/alpha/edge_robustness_validator_v9.py \
  joanbot/execution/micro_canary_outcome_feedback_v9.py \
  joanbot/control/control_plane_v9.py \
  joanbot/execution/paper_micro_canary_bridge_v9.py \
  joanbot/runtime/institutional_runtime_v9.py
do
  if [ ! -f "$f" ]; then
    echo "FAIL_MISSING_FILE=$f"
    exit 1
  fi
  echo "OK $f"
done

echo "===== COMPILE ====="
python -m py_compile \
  joanbot/alpha/institutional_edge_factory_v8.py \
  joanbot/alpha/edge_robustness_validator_v9.py \
  joanbot/execution/micro_canary_outcome_feedback_v9.py \
  joanbot/control/control_plane_v9.py \
  joanbot/execution/paper_micro_canary_bridge_v9.py \
  joanbot/runtime/institutional_runtime_v9.py \
  joanbot/runner.py \
  || exit 1

echo "===== STATIC AST SAFETY ====="
python - <<'PY'
import ast
from pathlib import Path

p = Path("joanbot/runtime/institutional_runtime_v9.py")
tree = ast.parse(p.read_text())

forbidden_calls = {
    "step_decisions",
    "step_positions",
    "open_from_decision",
    "execute",
    "place_order",
    "open_position",
    "close_position",
}

hits = []

for node in ast.walk(tree):
    if isinstance(node, ast.Call):
        fn = node.func
        if isinstance(fn, ast.Attribute) and fn.attr in forbidden_calls:
            hits.append(fn.attr)
        elif isinstance(fn, ast.Name) and fn.id in forbidden_calls:
            hits.append(fn.id)

if hits:
    print("FAIL_FORBIDDEN_RUNTIME_CALLS", hits)
    raise SystemExit(1)

print("NO_FORBIDDEN_RUNTIME_CALLS_OK")
PY

echo "===== LEGACY MUTATION SAFETY ====="
python - <<'PY'
from pathlib import Path

files = [
    "joanbot/alpha/institutional_edge_factory_v8.py",
    "joanbot/alpha/edge_robustness_validator_v9.py",
    "joanbot/execution/micro_canary_outcome_feedback_v9.py",
    "joanbot/control/control_plane_v9.py",
    "joanbot/execution/paper_micro_canary_bridge_v9.py",
    "joanbot/runtime/institutional_runtime_v9.py",
]

forbidden = [
    "INSERT INTO TRADES",
    "UPDATE TRADES",
    "DELETE FROM TRADES",
    "INSERT INTO POSITIONS ",
    "UPDATE POSITIONS ",
    "DELETE FROM POSITIONS ",
    "INSERT INTO DECISIONS",
    "UPDATE DECISIONS",
    "DELETE FROM DECISIONS",
]

bad = []

for f in files:
    s = Path(f).read_text().upper()
    for token in forbidden:
        if token in s:
            bad.append((f, token))

if bad:
    print("FAIL_FORBIDDEN_LEGACY_SQL_MUTATION", bad)
    raise SystemExit(1)

print("NO_LEGACY_SQL_MUTATION_OK")
PY

echo "===== LEGACY COUNTS BEFORE ====="
DEC_BEFORE=$(sqlite3 data/joanbot_v14.sqlite "SELECT COUNT(*) FROM decisions;" 2>/dev/null || echo 0)
POS_BEFORE=$(sqlite3 data/joanbot_v14.sqlite "SELECT COUNT(*) FROM positions;" 2>/dev/null || echo 0)
TR_BEFORE=$(sqlite3 data/joanbot_v14.sqlite "SELECT COUNT(*) FROM trades;" 2>/dev/null || echo 0)
echo "decisions=$DEC_BEFORE positions=$POS_BEFORE trades=$TR_BEFORE"

echo "===== RUN ONE V9 AUDIT CYCLE ====="
python -m joanbot.runtime.institutional_runtime_v9 --audit-once || exit 1

echo "===== LEGACY COUNTS AFTER ====="
DEC_AFTER=$(sqlite3 data/joanbot_v14.sqlite "SELECT COUNT(*) FROM decisions;" 2>/dev/null || echo 0)
POS_AFTER=$(sqlite3 data/joanbot_v14.sqlite "SELECT COUNT(*) FROM positions;" 2>/dev/null || echo 0)
TR_AFTER=$(sqlite3 data/joanbot_v14.sqlite "SELECT COUNT(*) FROM trades;" 2>/dev/null || echo 0)
echo "decisions=$DEC_AFTER positions=$POS_AFTER trades=$TR_AFTER"

[ "$DEC_BEFORE" = "$DEC_AFTER" ] || { echo "FAIL_DECISIONS_CHANGED"; exit 1; }
[ "$POS_BEFORE" = "$POS_AFTER" ] || { echo "FAIL_POSITIONS_CHANGED"; exit 1; }
[ "$TR_BEFORE" = "$TR_AFTER" ] || { echo "FAIL_TRADES_CHANGED"; exit 1; }

echo "LEGACY_TABLES_UNCHANGED_OK"

echo "===== ROBUSTNESS V9 ====="
sqlite3 -header -column data/joanbot_v14.sqlite "
SELECT
  symbol,
  side,
  family_name,
  setup,
  profile,
  horizon_min,
  n,
  ROUND(avg_r,4) AS avg_r,
  ROUND(lcb_r,4) AS lcb_r,
  ROUND(recent20_avg_r,4) AS r20,
  ROUND(recent50_avg_r,4) AS r50,
  ROUND(decay_guard,4) AS decay,
  ROUND(winrate,2) AS wr,
  ROUND(robustness_score,2) AS score,
  validation_state,
  canary_permission,
  hard_vetoes
FROM latest_edge_robustness_validator_v9
ORDER BY canary_permission DESC, robustness_score DESC, lcb_r DESC
LIMIT 15;
"

echo "===== FEEDBACK V9 ====="
sqlite3 -header -column data/joanbot_v14.sqlite "
SELECT
  feedback_state,
  canary_cooldown,
  closed_n,
  last5_n,
  ROUND(last5_avg_r,4) AS last5_avg,
  ROUND(last5_sum_r,4) AS last5_sum,
  ROUND(last5_winrate,2) AS last5_wr,
  loss_streak,
  hard_vetoes
FROM latest_micro_canary_outcome_feedback_v9;
"

echo "===== CONTROL V9 ====="
sqlite3 -header -column data/joanbot_v14.sqlite "
SELECT
  global_state,
  ROUND(control_score,2) AS control_score,
  recommended_action,
  next_required_build,
  allow_standard_open,
  allow_direct_open,
  allow_paper_micro_canary,
  force_learning_only,
  veto_new_positions,
  max_size_usd,
  edge_symbol,
  edge_side,
  edge_family,
  edge_setup,
  edge_profile,
  edge_horizon_min,
  edge_n,
  ROUND(edge_avg_r,4) AS edge_avg,
  ROUND(edge_lcb_r,4) AS edge_lcb,
  ROUND(edge_winrate,2) AS edge_wr,
  ROUND(robustness_score,2) AS robust_score,
  validation_state,
  regime_state,
  feedback_state,
  hard_vetoes,
  reasons
FROM latest_institutional_control_plane_v9;
"

echo "===== HARD SAFETY ====="
BAD=$(sqlite3 data/joanbot_v14.sqlite "
SELECT COUNT(*)
FROM latest_institutional_control_plane_v9
WHERE allow_standard_open != 0
   OR allow_direct_open != 0;
")
if [ "$BAD" -ne 0 ]; then
  echo "FAIL_STANDARD_OR_DIRECT_OPEN_ALLOWED=$BAD"
  exit 1
else
  echo "NO_STANDARD_OR_DIRECT_OPEN_OK"
fi

echo "===== DB INTEGRITY ====="
sqlite3 data/joanbot_v14.sqlite "PRAGMA integrity_check;"

echo "===== GIT DIFF CHECK ====="
git diff --check

echo "===== STATUS ====="
git status --short

echo "INSTITUTIONAL_RUNTIME_V9_AUDIT_OK"
