from pathlib import Path
import sys, json
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from joanbot.institutional.quant_governance_v3 import get_governance

res = get_governance().run()
print(json.dumps(res, indent=2, sort_keys=True, ensure_ascii=False))
