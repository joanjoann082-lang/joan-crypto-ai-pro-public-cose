#!/data/data/com.termux/files/usr/bin/bash
set -e

cd "$(dirname "$0")/.."
export PYTHONPATH=$PWD
FAIL=0

echo "===== ALPHA RUNTIME V7 AUDIT ====="

echo "===== NO RUNTIME ====="
if ps -ef | grep -Ei "python.*joanbot.runner|python.*joanbot.orchestrator|python.*joanbot.runtime|joanbot_overnight_supervisor" | grep -v grep; then
  echo "RUNTIME_ACTIVE_ABORT"
  exit 1
else
  echo "NO_RUNTIME_OK"
fi

echo "===== COMPILE ====="
python -m py_compile \
  joanbot/alpha/alpha_cluster_aggregator_v6.py \
  joanbot/alpha/regime_adaptive_router_v6.py \
  joanbot/control/control_plane_v7.py \
  joanbot/runtime/alpha_runtime_v7.py \
  joanbot/runner.py \
  || FAIL=1

echo "===== STATIC AST SAFETY ====="
python - <<'PY' || FAIL=1
import ast
from pathlib import Path

p = Path("joanbot/runtime/alpha_runtime_v7.py")
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

echo "===== SQL MUTATION SAFETY ====="
python - <<'PY' || FAIL=1
from pathlib import Path

files = [
    "joanbot/alpha/alpha_cluster_aggregator_v6.py",
    "joanbot/alpha/regime_adaptive_router_v6.py",
    "joanbot/control/control_plane_v7.py",
    "joanbot/runtime/alpha_runtime_v7.py",
]

forbidden = [
    "INSERT INTO TRADES", "UPDATE TRADES", "DELETE FROM TRADES",
    "INSERT INTO POSITIONS", "UPDATE POSITIONS", "DELETE FROM POSITIONS",
    "INSERT INTO DECISIONS", "UPDATE DECISIONS", "DELETE FROM DECISIONS",
]

bad = []

for f in files:
    s = Path(f).read_text().upper()
    for token in forbidden:
        if token in s:
            bad.append((f, token))

if bad:
    print("FAIL_FORBIDDEN_SQL_MUTATION", bad)
    raise SystemExit(1)

print("NO_FORBIDDEN_SQL_MUTATION_OK")
PY

echo "===== PROTECTED COUNTS BEFORE ====="
DEC_BEFORE=$(sqlite3 data/joanbot_v14.sqlite "SELECT COUNT(*) FROM decisions;" 2>/dev/null || echo 0)
POS_BEFORE=$(sqlite3 data/joanbot_v14.sqlite "SELECT COUNT(*) FROM positions;" 2>/dev/null || echo 0)
TR_BEFORE=$(sqlite3 data/joanbot_v14.sqlite "SELECT COUNT(*) FROM trades;" 2>/dev/null || echo 0)
echo "decisions=$DEC_BEFORE positions=$POS_BEFORE trades=$TR_BEFORE"

echo "===== RUN ONE V7 CYCLE ====="
python -m joanbot.runtime.alpha_runtime_v7 --once || FAIL=1

echo "===== PROTECTED COUNTS AFTER ====="
DEC_AFTER=$(sqlite3 data/joanbot_v14.sqlite "SELECT COUNT(*) FROM decisions;" 2>/dev/null || echo 0)
POS_AFTER=$(sqlite3 data/joanbot_v14.sqlite "SELECT COUNT(*) FROM positions;" 2>/dev/null || echo 0)
TR_AFTER=$(sqlite3 data/joanbot_v14.sqlite "SELECT COUNT(*) FROM trades;" 2>/dev/null || echo 0)
echo "decisions=$DEC_AFTER positions=$POS_AFTER trades=$TR_AFTER"

[ "$DEC_BEFORE" = "$DEC_AFTER" ] || { echo "FAIL_DECISIONS_CHANGED"; FAIL=1; }
[ "$POS_BEFORE" = "$POS_AFTER" ] || { echo "FAIL_POSITIONS_CHANGED"; FAIL=1; }
[ "$TR_BEFORE" = "$TR_AFTER" ] || { echo "FAIL_TRADES_CHANGED"; FAIL=1; }

echo "PROTECTED_TRADING_TABLES_UNCHANGED_OK"

echo "===== CLUSTER ====="
sqlite3 -header -column data/joanbot_v14.sqlite "
SELECT
  symbol,
  side,
  family_name,
  horizon_min,
  n,
  ROUND(avg_r,4) AS avg_r,
  ROUND(lcb_r,4) AS lcb_r,
  ROUND(winrate,2) AS winrate,
  ROUND(cluster_score,2) AS score,
  cluster_state,
  hard_vetoes
FROM latest_alpha_cluster_aggregator_v6
ORDER BY cluster_score DESC, lcb_r DESC
LIMIT 10;
" || FAIL=1

echo "===== REGIME ====="
sqlite3 -header -column data/joanbot_v14.sqlite "
SELECT
  regime_state,
  ROUND(regime_score,2) AS regime_score,
  selected_symbol,
  selected_side,
  selected_family,
  selected_horizon_min,
  cluster_n,
  ROUND(cluster_avg_r,4) AS cluster_avg,
  ROUND(cluster_lcb_r,4) AS cluster_lcb,
  ROUND(shadow_100_avg_r,4) AS sh100,
  ROUND(shadow_300_avg_r,4) AS sh300,
  allow_cluster_review,
  allow_micro_canary_candidate,
  hard_vetoes,
  reasons
FROM latest_regime_adaptive_router_v6;
" || FAIL=1

echo "===== CONTROL V7 ====="
sqlite3 -header -column data/joanbot_v14.sqlite "
SELECT
  global_state,
  ROUND(control_score,2) AS control_score,
  recommended_action,
  next_required_build,
  allow_standard_open,
  allow_direct_open,
  allow_paper_micro_canary,
  micro_canary_candidate,
  force_learning_only,
  veto_new_positions,
  cluster_symbol,
  cluster_side,
  cluster_family,
  cluster_horizon_min,
  cluster_n,
  ROUND(cluster_avg_r,4) AS cluster_avg,
  ROUND(cluster_lcb_r,4) AS cluster_lcb,
  ROUND(cluster_winrate,2) AS cluster_wr,
  cluster_state,
  regime_state,
  hard_vetoes,
  reasons
FROM latest_institutional_control_plane_v7;
" || FAIL=1

echo "===== HARD SAFETY: NO OPEN PERMISSION ====="
BAD=$(sqlite3 data/joanbot_v14.sqlite "
SELECT COUNT(*)
FROM latest_institutional_control_plane_v7
WHERE allow_standard_open != 0
   OR allow_direct_open != 0
   OR allow_paper_micro_canary != 0;
")
if [ "$BAD" -ne 0 ]; then
  echo "FAIL_CONTROL_V7_ALLOWS_OPEN=$BAD"
  FAIL=1
else
  echo "NO_OPEN_PERMISSION_OK"
fi

echo "===== DB INTEGRITY ====="
sqlite3 data/joanbot_v14.sqlite "PRAGMA integrity_check;" || FAIL=1

echo "===== GIT DIFF CHECK ====="
git diff --check || FAIL=1

echo "===== STATUS ====="
git status --short

if [ "$FAIL" -eq 0 ]; then
  echo "ALPHA_RUNTIME_V7_AUDIT_OK"
else
  echo "ALPHA_RUNTIME_V7_AUDIT_FAIL"
  exit 1
fi
