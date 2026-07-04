from pathlib import Path
import sys, json
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from joanbot.institutional.quant_governance_v3 import get_governance

g = get_governance()
checks = {
    "module_ok": True,
    "db_exists": (ROOT / "data" / "joanbot_v14.sqlite").exists(),
}
for table in [
    "quant_governance_runs_v3",
    "quant_governance_metrics_v3",
    "quant_governance_walkforward_v3",
    "quant_governance_cost_v3",
    "quant_governance_decision_v3",
    "quant_governance_policy_v3",
    "quant_governance_audit_v3",
]:
    try:
        with g.con() as con:
            con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
        checks[f"table_{table}"] = True
    except Exception:
        checks[f"table_{table}"] = False

try:
    cols = g.cols("estat_promocio_quant")
    checks["overlay_estat_promocio_quant"] = all(c in cols for c in ["governance_state", "governance_score", "governance_payload"])
except Exception:
    checks["overlay_estat_promocio_quant"] = False

print("VALIDACIO_QUANT_GOVERNANCE_V3")
print(json.dumps(checks, indent=2, sort_keys=True, ensure_ascii=False))
if not all(checks.values()):
    raise SystemExit(1)
print("VALIDACIO_QUANT_GOVERNANCE_V3_OK")
