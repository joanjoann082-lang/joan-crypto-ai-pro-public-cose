from pathlib import Path
import sys, json

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from joanbot.institutional.alpha_family_research_core_v6_1 import run, render_panel

DB = ROOT / "data" / "joanbot_v14.sqlite"

summary = run(DB)
print("ALPHA_FAMILY_RESEARCH_CORE_V6_1_OK")
print(json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2, default=str))
print()
print(render_panel(DB))
