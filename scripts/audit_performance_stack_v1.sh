#!/data/data/com.termux/files/usr/bin/bash
set -e

cd "$(dirname "$0")/.."
export PYTHONPATH=$PWD

echo "===== PERFORMANCE STACK V1 AUDIT ====="

python -m py_compile joanbot/analytics/performance_attribution_v1.py

python -m joanbot.analytics.performance_attribution_v1

test -f data/performance_baseline_v1.json
test -f data/reports/performance_attribution_v1_report.json

python - <<'PY'
import json
from pathlib import Path

report = json.loads(Path("data/reports/performance_attribution_v1_report.json").read_text())

required = [
    "version",
    "baseline",
    "all_trades",
    "post_baseline_trades",
    "open_conflicts",
    "by_symbol_side_setup",
    "by_symbol_side_setup_reason",
    "next_required_layer",
]

missing = [k for k in required if k not in report]
if missing:
    print("MISSING_REPORT_KEYS:", missing)
    raise SystemExit(1)

if report["version"] != "PERFORMANCE_ATTRIBUTION_V1":
    raise SystemExit("BAD_REPORT_VERSION")

print("REPORT_SCHEMA_OK")
print("OPEN_CONFLICTS:", len(report["open_conflicts"]))
print("SETUP_GROUPS:", len(report["by_symbol_side_setup"]))
PY

git check-ignore data/performance_baseline_v1.json >/dev/null
git check-ignore data/reports/performance_attribution_v1_report.json >/dev/null

echo "DATA_ARTIFACTS_IGNORED_OK"
echo "PERFORMANCE_STACK_V1_AUDIT_OK"
