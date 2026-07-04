
from pathlib import Path
import sys, json
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from joanbot.storage import get_db
from joanbot.institutional.gestio_posicio_institucional_neta import get_core
core=get_core(get_db())
print(json.dumps(core.reconstrueix_des_de_trades(), ensure_ascii=False, sort_keys=True))
