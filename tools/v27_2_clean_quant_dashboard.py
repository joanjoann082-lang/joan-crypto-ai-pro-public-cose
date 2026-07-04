
from pathlib import Path
import sys
ROOT = Path("/storage/emulated/0/Download/joan_crypto_ai_pro_v14")
sys.path.insert(0, str(ROOT))
from joanbot.storage import get_db
from joanbot.institutional.quant_core_v27_2 import get_core
db = get_db()
core = get_core(db)
txt = core.report()
p = ROOT / "live_export" / "v27_2_clean_quant_report.txt"
p.parent.mkdir(parents=True, exist_ok=True)
p.write_text(txt, encoding="utf-8")
print(txt)
