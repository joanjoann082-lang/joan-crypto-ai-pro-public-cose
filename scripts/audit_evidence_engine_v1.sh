#!/data/data/com.termux/files/usr/bin/bash
set -e

cd "$(dirname "$0")/.."
export PYTHONPATH=$PWD

FAIL=0

echo "===== EVIDENCE ENGINE V1 STANDALONE AUDIT ====="

echo "===== NO RUNTIME ====="
if ps -ef | grep -Ei "joanbot.runner|telegram_command_bot|dashboard" | grep -v grep; then
  echo "RUNTIME_ACTIVE_ABORT"
  exit 1
else
  echo "NO_RUNTIME_OK"
fi

echo "===== COMPILE ====="
python -m py_compile joanbot/intelligence/evidence_engine_v1.py || FAIL=1

echo "===== RUN ENGINE ====="
python -m joanbot.intelligence.evidence_engine_v1 || FAIL=1

echo "===== DB SAFETY ====="
sqlite3 -header -column data/joanbot_v14.sqlite "PRAGMA integrity_check;" || FAIL=1

echo "===== JOIN HEALTH ====="
sqlite3 -header -column data/joanbot_v14.sqlite "
SELECT COUNT(*) AS joined_forward_rows
FROM forward_results fr
JOIN forward_cases fc ON fr.case_id = fc.id;

SELECT fc.action, COALESCE(fr.symbol, fc.symbol) AS symbol, fc.side, fc.setup,
       COUNT(*) AS n,
       ROUND(AVG(fr.result_r),4) AS avg_r
FROM forward_results fr
JOIN forward_cases fc ON fr.case_id = fc.id
GROUP BY fc.action, COALESCE(fr.symbol, fc.symbol), fc.side, fc.setup
ORDER BY n DESC
LIMIT 20;
" || FAIL=1

echo "===== STATUS ====="
git status --short

if [ "$FAIL" -eq 0 ]; then
  echo "EVIDENCE_ENGINE_V1_STANDALONE_AUDIT_OK"
else
  echo "EVIDENCE_ENGINE_V1_STANDALONE_AUDIT_FAIL"
  exit 1
fi
