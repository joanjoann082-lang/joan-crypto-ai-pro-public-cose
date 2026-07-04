
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from joanbot.storage import get_db
from joanbot.institutional.gestio_posicio_institucional_neta import get_core

core = get_core(get_db())
txt = core.report()
(ROOT / "live_export").mkdir(exist_ok=True)
(ROOT / "live_export" / "panell_gestio_posicio_v4_quant.txt").write_text(txt, encoding="utf-8")
print(txt)
