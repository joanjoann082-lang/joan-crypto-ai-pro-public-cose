
from core.runtime_control_v25 import summary, update_controls, set_preset, reset_controls

HELP = """/control
/control preset conservative|normal|aggressive
/control pause
/control resume
/control score +5
/control minscore 78
/control risk 0.50
/control maxopen 1
/control longs on|off
/control shorts on|off
/control reset
"""

def handle_runtime_control_text(text, user="telegram"):
    try:
        parts = (text or "").strip().split()
        if parts and parts[0].startswith("/"):
            parts = parts[1:]
        if not parts:
            return summary() + "\n" + HELP

        cmd = parts[0].lower()

        if cmd == "preset":
            set_preset(parts[1], updated_by=user)
        elif cmd == "pause":
            update_controls(updated_by=user, trading_paused=True)
        elif cmd == "resume":
            update_controls(updated_by=user, trading_paused=False)
        elif cmd == "score":
            v = parts[1]
            if v.startswith("+") or v.startswith("-"):
                update_controls(updated_by=user, score_floor_delta=float(v), min_score_override=None)
            else:
                update_controls(updated_by=user, min_score_override=float(v))
        elif cmd == "minscore":
            update_controls(updated_by=user, min_score_override=float(parts[1]))
        elif cmd == "risk":
            update_controls(updated_by=user, risk_multiplier=float(parts[1]))
        elif cmd == "maxopen":
            update_controls(updated_by=user, max_global_open=int(parts[1]))
        elif cmd == "longs":
            update_controls(updated_by=user, allow_longs=parts[1].lower() in ("on","true","1","yes","si","sí"))
        elif cmd == "shorts":
            update_controls(updated_by=user, allow_shorts=parts[1].lower() in ("on","true","1","yes","si","sí"))
        elif cmd == "reset":
            reset_controls(updated_by=user)
        else:
            return "Ordre no reconeguda.\n\n" + HELP

        return "Runtime control actualitzat:\n" + summary()
    except Exception as e:
        return f"ERROR_RUNTIME_CONTROL: {e}\n\n{HELP}"
