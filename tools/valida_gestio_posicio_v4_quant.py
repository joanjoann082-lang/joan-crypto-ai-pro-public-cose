
from pathlib import Path
import json, sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from joanbot.storage import get_db
from joanbot.institutional.gestio_posicio_institucional_neta import get_core

broker = (ROOT / "joanbot/execution/broker.py").read_text(encoding="utf-8")
gestio = (ROOT / "joanbot/institutional/gestio_posicio_institucional_neta.py").read_text(encoding="utf-8")
db = get_db()
core = get_core(db)

checks = {
    "broker_mark_positions_v4": "GESTIO_POSICIO_V4_QUANT_UNICA" in broker,
    "broker_manage_v4": "_manage_positions_v4_quant" in broker,
    "profitguard_wrapper": "Adaptador de compatibilitat" in broker,
    "sense_sortides_laterals": "sortides_estadistiques" not in broker and not (ROOT / "joanbot/institutional/sortides_estadistiques_netes.py").exists(),
    "gestio_v4_class": "GestioPosicioInstitucionalNetaV4Quant" in gestio,
    "get_core_v4": "GestioPosicioInstitucionalNetaV4Quant(db)" in gestio,
    "policy_compiler": "optimitza_politica" in gestio and "GRID_MFE_MAE_GIVEBACK_V4" in gestio,
    "tp3_final": "partial3_after_r" in gestio and "final_after_r" in gestio,
    "idempotencia": "accions_gestio_posicio_neta" in gestio and "reserva_accio" in gestio,
    "higiene": "neteja_contaminacio_prova" in gestio,
}

for table in [
    "accions_gestio_posicio_neta",
    "recerca_politica_gestio_posicio_neta",
    "higiene_gestio_posicio_neta",
]:
    try:
        db.query(f"SELECT COUNT(*) c FROM {table}")
        checks[f"taula_{table}"] = True
    except Exception:
        checks[f"taula_{table}"] = False

print("VALIDACIO_GESTIO_POSICIO_V4_QUANT")
print(json.dumps(checks, indent=2, sort_keys=True, ensure_ascii=False))

if not all(checks.values()):
    raise SystemExit(1)

print("VALIDACIO_GESTIO_POSICIO_V4_QUANT_OK")
