#!/data/data/com.termux/files/usr/bin/bash
set -e

cd "$(dirname "$0")/.."
export PYTHONPATH=$PWD
FAIL=0

echo "===== CANARY PROMOTION GATE V1 AUDIT ====="

echo "===== NO RUNTIME ====="
if ps -ef | grep -Ei "joanbot.runner|joanbot.ui.dashboard|telegram_command_bot" | grep -v grep; then
  echo "RUNTIME_ACTIVE_ABORT"
  exit 1
else
  echo "NO_RUNTIME_OK"
fi

echo "===== COMPILE ====="
python -m py_compile \
  joanbot/intelligence/canary_promotion_gate_v1.py \
  joanbot/intelligence/statistical_edge_authority_v1.py \
  joanbot/intelligence/decision.py \
  joanbot/intelligence/research_promotion_policy_v1.py \
  joanbot/intelligence/bayesian_evidence_v1.py \
  joanbot/intelligence/evidence_registry_v1.py \
  joanbot/execution/contract.py \
  joanbot/execution/broker.py \
  joanbot/runner.py \
  || FAIL=1

echo "===== REFRESH EVIDENCE STACK ====="
python -m joanbot.intelligence.evidence_registry_v1 --refresh --summary || FAIL=1
python -m joanbot.intelligence.bayesian_evidence_v1 --refresh --latest || FAIL=1
python -m joanbot.intelligence.research_promotion_policy_v1 --refresh --latest || FAIL=1

echo "===== CANARY GATE DIRECT ====="
python -m joanbot.intelligence.canary_promotion_gate_v1 || FAIL=1

echo "===== AUTHORITY CANARY INTEGRATION STATIC CHECK ====="
grep -n "CanaryPromotionGateV1\|CANARY_PROMOTION_GATE_V1\|CANARY_MICRO_PROBE_ONLY\|size_usd_cap" joanbot/intelligence/statistical_edge_authority_v1.py || FAIL=1

echo "===== DECISION ONLY APPLIES AUTHORITY CAP ====="
grep -n "AUTHORITY_SIZE_USD_CAP_APPLIED" joanbot/intelligence/decision.py || FAIL=1

if grep -nEi "research_promotion_policy_v1|latest_research_promotion_v1|sqlite3|connect\\(" joanbot/intelligence/decision.py; then
  echo "FAIL_DECISION_POLICY_OR_DB_READ"
  FAIL=1
else
  echo "DECISION_NO_POLICY_DB_READ_OK"
fi

echo "===== AUTHORITY HAS NO DIRECT DB READ ====="
python - <<'PYCHK' || FAIL=1
from pathlib import Path
import re

p = Path("joanbot/intelligence/statistical_edge_authority_v1.py")
bad = []
for i, line in enumerate(p.read_text().splitlines(), 1):
    stripped = line.strip()

    # Imports are allowed. They are not DB reads.
    if stripped.startswith("from ") or stripped.startswith("import "):
        continue

    # Comments are allowed.
    if stripped.startswith("#"):
        continue

    if re.search(r"sqlite3|\.connect\s*\(|\.execute\s*\(|\.query\s*\(", line):
        bad.append((i, line))

    # SQL tokens only count when they appear inside actual code/string, not imports.
    if re.search(r"\bSELECT\b|\bFROM\b|\bJOIN\b", line):
        bad.append((i, line))

if bad:
    print("FAIL_AUTHORITY_DIRECT_DB_READ")
    for i, line in bad:
        print(f"{i}: {line}")
    raise SystemExit(1)

print("AUTHORITY_NO_DIRECT_DB_READ_OK")
PYCHK

echo "===== CANARY SAFETY FROM SOURCE VIEW ====="
sqlite3 -header -column data/joanbot_v14.sqlite "
SELECT
  symbol,
  side,
  setup,
  allow_canary_probe,
  allow_direct_open,
  ROUND(size_multiplier_cap,4) AS size_mult_cap,
  ROUND(absolute_size_usd_cap,2) AS usd_cap,
  promotion_state
FROM latest_research_promotion_v1
ORDER BY allow_canary_probe DESC, quality_score DESC;
" || FAIL=1

BAD_OPEN=$(sqlite3 data/joanbot_v14.sqlite "
SELECT COUNT(*)
FROM latest_research_promotion_v1
WHERE allow_direct_open != 0;
")
if [ "$BAD_OPEN" != "0" ]; then
  echo "FAIL_DIRECT_OPEN_ALLOWED=$BAD_OPEN"
  FAIL=1
else
  echo "NO_DIRECT_OPEN_CANARY_OK"
fi

BAD_CAP=$(sqlite3 data/joanbot_v14.sqlite "
SELECT COUNT(*)
FROM latest_research_promotion_v1
WHERE allow_canary_probe=1
  AND (size_multiplier_cap > 0.025 OR absolute_size_usd_cap > 250);
")
if [ "$BAD_CAP" != "0" ]; then
  echo "FAIL_CANARY_CAP=$BAD_CAP"
  FAIL=1
else
  echo "CANARY_SIZE_CAP_OK"
fi

echo "===== FAIL CLOSED TEST ====="
python - <<'PY' || FAIL=1
from joanbot.intelligence.canary_promotion_gate_v1 import CanaryPromotionGateV1
v = CanaryPromotionGateV1().evaluate("BTCUSDT", "LONG", "NON_EXISTENT_SETUP")
assert v["allow_canary_probe"] is False
assert v["allow_direct_open"] is False
assert v["allow_canary_probe"] is False
assert v["allow_direct_open"] is False
assert v["gate_status"] in ("NO_PROMOTION_ROW","NO_SOURCE_VIEW","GATE_ERROR","CANARY_BLOCKED")
print("FAIL_CLOSED_OK", v["gate_status"])
PY

echo "===== AUTHORITY LIVE VERDICT SAMPLE ====="
python - <<'PY' || FAIL=1
from joanbot.intelligence.statistical_edge_authority_v1 import StatisticalEdgeAuthorityV1

class C:
    def __init__(self, symbol, side, setup):
        self.symbol=symbol
        self.side=side
        self.setup=setup

auth=StatisticalEdgeAuthorityV1()
for c in [
    C("BTCUSDT","LONG","CAPITULATION_REBOUND_LONG"),
    C("ETHUSDT","LONG","CAPITULATION_REBOUND_LONG"),
    C("BTCUSDT","SHORT","TREND_BOUNCE_SHORT"),
    C("ETHUSDT","SHORT","TREND_BOUNCE_SHORT"),
]:
    v=auth.evaluate(c, {}, {})
    print(c.symbol, c.side, c.setup, v["authority_status"], "probe=", v["allow_probe"], "open=", v["allow_open"], "size_mult=", v["size_multiplier"], "usd_cap=", v.get("size_usd_cap"))
    assert not v["allow_open"]
    if c.setup == "CAPITULATION_REBOUND_LONG":
        assert v["authority_status"] in ("CANARY_MICRO_PROBE_ONLY","INSUFFICIENT_SAMPLE","PROBE_ONLY")
PY

echo "===== DB INTEGRITY ====="
sqlite3 -header -column data/joanbot_v14.sqlite "PRAGMA integrity_check;" || FAIL=1

echo "===== STORAGE BOUNDS ====="
sqlite3 -header -column data/joanbot_v14.sqlite "
SELECT 'bayesian_evidence_scores_v1' AS table_name, COUNT(*) AS n FROM bayesian_evidence_scores_v1
UNION ALL
SELECT 'research_promotion_decisions_v1', COUNT(*) FROM research_promotion_decisions_v1
UNION ALL
SELECT 'evidence_registry_audit_v1', COUNT(*) FROM evidence_registry_audit_v1;
" || FAIL=1

echo "===== SIZE CHECK ====="
ls -lh data/joanbot_v14.sqlite*
df -h /storage/emulated

echo "===== GIT DIFF CHECK ====="
git diff --check || FAIL=1

echo "===== STATUS ====="
git status --short

if [ "$FAIL" -eq 0 ]; then
  echo "CANARY_PROMOTION_GATE_V1_AUDIT_OK"
else
  echo "CANARY_PROMOTION_GATE_V1_AUDIT_FAIL"
  exit 1
fi
