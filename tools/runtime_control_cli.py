
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.runtime_control_v25 import summary, update_controls, set_preset, reset_controls

args = sys.argv[1:]

if not args or args[0] in ("show", "status"):
    print(summary())
    raise SystemExit

cmd = args[0].lower()

if cmd == "preset":
    set_preset(args[1])
elif cmd == "pause":
    update_controls(trading_paused=True)
elif cmd == "resume":
    update_controls(trading_paused=False)
elif cmd == "risk":
    update_controls(risk_multiplier=float(args[1]))
elif cmd == "score_delta":
    update_controls(score_floor_delta=float(args[1]), min_score_override=None)
elif cmd == "minscore":
    update_controls(min_score_override=float(args[1]))
elif cmd == "maxopen":
    update_controls(max_global_open=int(args[1]))
elif cmd == "longs":
    update_controls(allow_longs=args[1].lower() in ("on","true","1","yes","si","sí"))
elif cmd == "shorts":
    update_controls(allow_shorts=args[1].lower() in ("on","true","1","yes","si","sí"))
elif cmd == "reset":
    reset_controls()
else:
    raise SystemExit("ordre no reconeguda")

print(summary())
