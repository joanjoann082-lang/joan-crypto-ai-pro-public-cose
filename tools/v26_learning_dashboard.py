
from pathlib import Path
import sys

ROOT = Path("/storage/emulated/0/Download/joan_crypto_ai_pro_v14")
sys.path.insert(0, str(ROOT))

from joanbot.storage import get_db
from joanbot.institutional.outcome_learning_v26 import get_core

db = get_db()
core = get_core(db)
text = core.report()

out = ROOT / "live_export" / "v26_learning_report.txt"
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(text, encoding="utf-8")

print(text)
