#!/data/data/com.termux/files/usr/bin/bash
set -e

cd "$(dirname "$0")/.."
export PYTHONPATH=$PWD
FAIL=0

echo "===== UNIVERSAL SHADOW ALPHA LOOP V2 AUDIT ====="

echo "===== NO RUNTIME ====="
if ps -ef | grep -Ei "python.*joanbot.runner|python.*joanbot.ui.dashboard|telegram_command_bot" | grep -v grep; then
  echo "RUNTIME_ACTIVE_ABORT"
  exit 1
else
  echo "NO_RUNTIME_OK"
fi

echo "===== FORWARD TABLE COUNTS BEFORE ====="
FC_BEFORE=$(sqlite3 data/joanbot_v14.sqlite "SELECT COUNT(*) FROM forward_cases;")
FR_BEFORE=$(sqlite3 data/joanbot_v14.sqlite "SELECT COUNT(*) FROM forward_results;")
echo "forward_cases_before=$FC_BEFORE forward_results_before=$FR_BEFORE"

echo "===== COMPILE ====="
python -m py_compile \
  joanbot/intelligence/universal_shadow_alpha_loop_v2.py \
  joanbot/runner.py \
  joanbot/features/context.py \
  joanbot/storage/db.py \
  joanbot/intelligence/decision.py \
  joanbot/intelligence/risk.py \
  || FAIL=1

echo "===== STATIC RUNNER INTEGRATION ====="
grep -RIn "UniversalShadowAlphaLoopV2" joanbot/runner.py || FAIL=1
grep -RIn "def step_alpha_shadow" joanbot/runner.py || FAIL=1
grep -RIn "step_alpha_shadow" joanbot/runner.py || FAIL=1

echo "===== SAFETY: NO TRADING PATH MUTATION ====="
if grep -RInE "UniversalShadowAlphaLoopV2|universal_shadow_alpha_loop_v2|universal_shadow_cases_v2|universal_shadow_results_v2|universal_shadow_registry_v2" \
  joanbot/intelligence/decision.py \
  joanbot/intelligence/risk.py \
  joanbot/execution \
  --include="*.py"; then
  echo "FAIL_UNIVERSAL_SHADOW_IN_TRADING_DECISION_PATH"
  FAIL=1
else
  echo "TRADING_DECISION_PATH_CLEAN_OK"
fi

echo "===== SAFETY: MODULE DOES NOT MUTATE FORWARD TABLES ====="
python - <<'PY2' || FAIL=1
from pathlib import Path

s = Path("joanbot/intelligence/universal_shadow_alpha_loop_v2.py").read_text()

forbidden = [
    "INSERT INTO forward_cases",
    "INSERT OR IGNORE INTO forward_cases",
    "UPDATE forward_cases",
    "DELETE FROM forward_cases",
    "FROM forward_cases",
    "JOIN forward_cases",
    "INSERT INTO forward_results",
    "INSERT OR IGNORE INTO forward_results",
    "UPDATE forward_results",
    "DELETE FROM forward_results",
    "FROM forward_results",
    "JOIN forward_results",
]

hits = [x for x in forbidden if x in s]

if hits:
    print("FAIL_FORWARD_TABLE_SQL_USAGE", hits)
    raise SystemExit(1)

print("FORWARD_TABLE_SQL_ISOLATION_OK")
PY2

echo "===== RUN ONE LIMITED CYCLE ====="
UNIVERSAL_ALPHA_V2_MAX_CASES_PER_CYCLE=12 \
python -m joanbot.intelligence.universal_shadow_alpha_loop_v2 --cycle --pending --latest || FAIL=1

echo "===== DB INTEGRITY ====="
sqlite3 -header -column data/joanbot_v14.sqlite "PRAGMA integrity_check;" || FAIL=1

echo "===== UNIVERSAL SHADOW CASES ====="
sqlite3 -header -column data/joanbot_v14.sqlite "
SELECT
  symbol,
  side,
  setup,
  profile,
  horizon_min,
  status,
  COUNT(*) AS n,
  ROUND(AVG(context_score),2) AS avg_context_score,
  MIN(created_at) AS first_created,
  MAX(created_at) AS last_created
FROM universal_shadow_cases_v2
GROUP BY symbol, side, setup, profile, horizon_min, status
ORDER BY last_created DESC
LIMIT 50;
" || FAIL=1

echo "===== UNIVERSAL SHADOW REGISTRY ====="
sqlite3 -header -column data/joanbot_v14.sqlite "
SELECT
  symbol,
  side,
  setup,
  profile,
  horizon_min,
  context_bucket,
  n,
  ROUND(expectancy_r,4) AS exp_r,
  ROUND(profit_factor,3) AS pf,
  ROUND(validation_exp_r,4) AS val_r,
  ROUND(quality_score,2) AS quality,
  state,
  recommendation
FROM latest_universal_shadow_registry_v2
ORDER BY quality_score DESC, expectancy_r DESC, n DESC
LIMIT 30;
" || true

echo "===== FORWARD TABLE COUNTS AFTER ====="
FC_AFTER=$(sqlite3 data/joanbot_v14.sqlite "SELECT COUNT(*) FROM forward_cases;")
FR_AFTER=$(sqlite3 data/joanbot_v14.sqlite "SELECT COUNT(*) FROM forward_results;")
echo "forward_cases_after=$FC_AFTER forward_results_after=$FR_AFTER"

if [ "$FC_BEFORE" != "$FC_AFTER" ]; then echo "FAIL_FORWARD_CASES_CHANGED"; FAIL=1; else echo "FORWARD_CASES_UNCHANGED_OK"; fi
if [ "$FR_BEFORE" != "$FR_AFTER" ]; then echo "FAIL_FORWARD_RESULTS_CHANGED"; FAIL=1; else echo "FORWARD_RESULTS_UNCHANGED_OK"; fi

echo "===== QUALITY: CASES CREATED ====="
CASE_N=$(sqlite3 data/joanbot_v14.sqlite "SELECT COUNT(*) FROM universal_shadow_cases_v2;")
if [ "$CASE_N" -lt 1 ]; then
  echo "FAIL_NO_UNIVERSAL_SHADOW_CASES_CREATED"
  FAIL=1
else
  echo "UNIVERSAL_SHADOW_CASES_CREATED_OK=$CASE_N"
fi

echo "===== STORAGE BOUNDS ====="
C=$(sqlite3 data/joanbot_v14.sqlite "SELECT COUNT(*) FROM universal_shadow_cases_v2;")
R=$(sqlite3 data/joanbot_v14.sqlite "SELECT COUNT(*) FROM universal_shadow_results_v2;")
G=$(sqlite3 data/joanbot_v14.sqlite "SELECT COUNT(*) FROM universal_shadow_registry_v2;")
A=$(sqlite3 data/joanbot_v14.sqlite "SELECT COUNT(*) FROM universal_shadow_alpha_audit_v2;")
echo "cases=$C results=$R registry=$G audit=$A"

if [ "$C" -gt 2500 ]; then echo "FAIL_CASE_BOUND=$C"; FAIL=1; else echo "CASE_BOUND_OK"; fi
if [ "$R" -gt 2500 ]; then echo "FAIL_RESULT_BOUND=$R"; FAIL=1; else echo "RESULT_BOUND_OK"; fi
if [ "$G" -gt 800 ]; then echo "FAIL_REGISTRY_BOUND=$G"; FAIL=1; else echo "REGISTRY_BOUND_OK"; fi
if [ "$A" -gt 200 ]; then echo "FAIL_AUDIT_BOUND=$A"; FAIL=1; else echo "AUDIT_BOUND_OK"; fi

echo "===== GIT DIFF CHECK ====="
git diff --check || FAIL=1

echo "===== STATUS ====="
git status --short

if [ "$FAIL" -eq 0 ]; then
  echo "UNIVERSAL_SHADOW_ALPHA_LOOP_V2_AUDIT_OK"
else
  echo "UNIVERSAL_SHADOW_ALPHA_LOOP_V2_AUDIT_FAIL"
  exit 1
fi
