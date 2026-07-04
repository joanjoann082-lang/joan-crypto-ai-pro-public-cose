
from pathlib import Path
import json, sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from joanbot.storage import get_db
from joanbot.institutional.gestio_posicio_institucional_neta import get_core

core = get_core(get_db())
print(json.dumps(core.neteja_contaminacio_prova(), indent=2, sort_keys=True, ensure_ascii=False))
