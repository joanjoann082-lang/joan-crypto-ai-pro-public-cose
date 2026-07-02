#!/data/data/com.termux/files/usr/bin/bash
set -e

cd "$(dirname "$0")/.."
export PYTHONPATH=$PWD
FAIL=0

echo "===== DECISION SOURCE AUDIT V6 ====="

echo "===== RUNTIME CHECK ====="
RUNNERS=$(ps -ef | grep -Ei "python.*joanbot.runner" | grep -v grep | wc -l || true)
echo "runner_processes=$RUNNERS"
if [ "$RUNNERS" -gt 1 ]; then
  echo "FAIL_MULTIPLE_RUNNERS"
  FAIL=1
fi

echo
echo "===== DB INTEGRITY ====="
sqlite3 data/joanbot_v14.sqlite "PRAGMA integrity_check;" || FAIL=1

echo
echo "===== DECISION / RISK / EXECUTION SOURCE SCAN ====="
echo "--- OPEN / WAIT / REJECT / SCORE ---"
grep -RInE "OPEN|WAIT|REJECT|score|threshold|set_open|allowed|veto|gate|governor|authority" \
  joanbot \
  --include="*.py" \
  | grep -Ev "__pycache__|\.bak|audit_|control_plane_v6|institutional_authority_v5" \
  | head -250 || true

echo
echo "--- RISK / SIZE / LEVERAGE / CAP ---"
grep -RInE "risk|size_usd|position_size|leverage|cap|drawdown|max_daily|exposure" \
  joanbot \
  --include="*.py" \
  | grep -Ev "__pycache__|\.bak|audit_|control_plane_v6" \
  | head -250 || true

echo
echo "--- TELEGRAM COMMANDS / RECOMMENDATIONS ---"
grep -RInE "telegram|/set_open|/risk|/confirm|recommend|suggest|command" \
  joanbot scripts \
  --include="*.py" --include="*.sh" \
  | grep -Ev "__pycache__|\.bak|audit_decision_sources_v6" \
  | head -250 || true

echo
echo "--- EXECUTION MUTATION POINTS ---"
grep -RInE "INSERT INTO trades|INSERT INTO positions|UPDATE positions|execute|broker|open_position|close_position|place_order" \
  joanbot \
  --include="*.py" \
  | grep -Ev "__pycache__|\.bak|audit_" \
  | head -250 || true

echo
echo "===== ALPHA / AUTHORITY CONNECTION CHECK ====="
echo "--- alpha/control imported in decision/risk/execution? ---"
if grep -RInE "alpha_|latest_alpha_|InstitutionalAuthority|ControlPlane|control_plane" \
  joanbot/intelligence/decision.py \
  joanbot/intelligence/risk.py \
  joanbot/execution \
  --include="*.py" 2>/dev/null; then
  echo "WARN_ALPHA_OR_CONTROL_ALREADY_CONNECTED_TO_TRADING_PATH"
else
  echo "TRADING_PATH_NO_ALPHA_CONTROL_OK"
fi

echo
echo "===== DB GOVERNANCE OBJECTS ====="
sqlite3 -header -column data/joanbot_v14.sqlite "
SELECT name, type
FROM sqlite_master
WHERE name LIKE '%alpha%'
   OR name LIKE '%authority%'
   OR name LIKE '%control_plane%'
ORDER BY name;
" || true

echo
echo "===== LATEST ALPHA CONTRACT SUMMARY ====="
sqlite3 -header -column data/joanbot_v14.sqlite "
SELECT
  contract_state,
  COUNT(*) AS n,
  SUM(allowed_paper_micro_canary) AS micro_ready,
  ROUND(MAX(meta_score),2) AS max_meta,
  ROUND(MAX(posterior_score),2) AS max_post,
  ROUND(MAX(posterior_lcb_r),4) AS max_lcb,
  ROUND(MAX(tensor_quality),2) AS max_tensor_q
FROM latest_alpha_promotion_contract_v5
GROUP BY contract_state;
" 2>/dev/null || echo "NO_ALPHA_CONTRACT_VIEW"

echo
echo "===== STRUCTURAL VERDICT ====="
echo "This audit is read-only. It identifies active decision sources and possible overlaps."
echo "Expected before Control Plane V6: trading path clean, alpha/control not directly connected to execution."

if [ "$FAIL" -eq 0 ]; then
  echo "DECISION_SOURCE_AUDIT_V6_OK"
else
  echo "DECISION_SOURCE_AUDIT_V6_FAIL"
  exit 1
fi
