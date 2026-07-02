#!/data/data/com.termux/files/usr/bin/bash
set -e

cd "$(dirname "$0")/.."
export PYTHONPATH=$PWD

FAIL=0

echo "===== EVIDENCE AUTHORITY R V1 AUDIT ====="

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
  joanbot/intelligence/evidence_engine_v1.py \
  joanbot/intelligence/statistical_edge_authority_v1.py \
  joanbot/intelligence/decision.py \
  joanbot/intelligence/risk.py \
  joanbot/execution/contract.py \
  joanbot/runner.py \
  || FAIL=1

echo "===== AUTHORITY BOUNDARY CHECK ====="
grep -q "EvidenceEngineV1" joanbot/intelligence/statistical_edge_authority_v1.py || FAIL=1

if grep -nE "def _position_rows|def _forward_rows|FROM positions|FROM forward_results|sqlite_master|PRAGMA table_info|get_db" joanbot/intelligence/statistical_edge_authority_v1.py; then
  echo "BAD_DIRECT_DB_READ_IN_AUTHORITY"
  FAIL=1
else
  echo "AUTHORITY_NO_DIRECT_DB_READ_OK"
fi

echo "===== EVIDENCE OUTPUT ====="
python -m joanbot.intelligence.evidence_engine_v1 || FAIL=1

echo "===== AUTHORITY OUTPUT ====="
python -m joanbot.intelligence.statistical_edge_authority_v1 || FAIL=1

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

echo "===== POLICY CHECK ====="
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

    print(
        symbol, side, setup,
        "status", v["authority_status"],
        "edge", v["edge_status"],
        "open", v["allow_open"],
        "probe", v["allow_probe"],
        "r_n", v["position_r_n"],
        "r_exp", v["position_expectancy_r"],
        "usd_n", v["position_usd_n"],
        "usd_exp", v["position_expectancy_usd"],
        "fw_n", v["forward_n"],
        "shadow_n", v["forward_shadow_n"],
        "join", v["source_health"].get("forward_join_schema_usable"),
        "reasons", ",".join(v["reasons"][:4]),
    )

    required = [
        "authority_status",
        "edge_status",
        "allow_open",
        "allow_probe",
        "score_adjustment",
        "size_multiplier",
        "status",
        "expectancy_r",
        "profit_factor",
        "position_r_n",
        "position_expectancy_r",
        "position_profit_factor_r",
        "position_usd_n",
        "forward_n",
        "forward_shadow_n",
        "reasons",
        "evidence",
    ]

    for k in required:
        if k not in v:
            bad.append(f"MISSING_KEY_{k}")

    if not v["source_health"].get("forward_join_schema_usable"):
        bad.append("FORWARD_JOIN_SCHEMA_NOT_USABLE")

    if setup == "CAPITULATION_REBOUND_LONG" and (v["allow_open"] or v["allow_probe"]):
        bad.append("CAPITULATION_REBOUND_LONG_NOT_BLOCKED")

    if v["position_r_n"] < 12 and v["allow_open"]:
        bad.append("OPEN_ALLOWED_WITH_LOW_R_SAMPLE")

    if v["forward_shadow_n"] > 0 and v["position_r_n"] < 12 and v["allow_open"]:
        bad.append("SHADOW_EVIDENCE_PROMOTED_TO_OPEN")

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
print("DECISION_KERNEL_OK")
PY

echo "===== GIT DIFF CHECK ====="
git diff --check || FAIL=1

echo "===== STATUS ====="
git status --short

if [ "$FAIL" -eq 0 ]; then
  echo "EVIDENCE_AUTHORITY_R_V1_AUDIT_OK"
else
  echo "EVIDENCE_AUTHORITY_R_V1_AUDIT_FAIL"
  exit 1
fi
