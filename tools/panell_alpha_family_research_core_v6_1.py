from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from joanbot.institutional.alpha_family_research_core_v6_1 import render_panel

DB = ROOT / "data" / "joanbot_v14.sqlite"
print(render_panel(DB))
