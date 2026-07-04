from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from joanbot.institutional.quant_governance_v3 import get_governance

txt = get_governance().report()
(ROOT / "live_export").mkdir(exist_ok=True)
(ROOT / "live_export" / "panell_quant_governance_v3.txt").write_text(txt, encoding="utf-8")
print(txt)
