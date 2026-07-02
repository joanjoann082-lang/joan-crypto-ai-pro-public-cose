from pathlib import Path
import re

p = Path("joanbot/control/control_plane_v11.py")
if not p.exists():
    raise SystemExit("CONTROL_PLANE_NOT_FOUND")

orig = p.read_text()
backup = p.with_suffix(".py.before_alpha_gate_v16")
backup.write_text(orig)

s = orig

# Elimina imports antics de gates anteriors per evitar solapament.
s = re.sub(r"^from joanbot\.institutional_v14\.final_gate_adapter_v14 import apply_final_gate_v14\n", "", s, flags=re.M)
s = re.sub(r"^from joanbot\.institutional_v15\.final_gate_adapter_v15 import apply_alpha_final_gate_v15\n", "", s, flags=re.M)

# Desembolica retorns antics.
s = re.sub(r"return apply_final_gate_v14\((\w+)\)", r"return \1", s)
s = re.sub(r"return apply_alpha_final_gate_v15\((\w+)\)", r"return \1", s)

if "apply_alpha_final_gate_v16" not in s:
    s = "from joanbot.institutional_v16.final_gate_adapter_v16 import apply_alpha_final_gate_v16\n" + s

patched = False
for var in ["control", "payload", "result", "state"]:
    marker = f"return {var}\n"
    if marker in s:
        s = s.replace(marker, f"return apply_alpha_final_gate_v16({var})\n", 1)
        patched = True
        break

if not patched:
    p.write_text(orig)
    raise SystemExit("NO_SAFE_RETURN_MARKER_FOUND_ROLLBACK_DONE")

p.write_text(s)
print("CONTROL_ALPHA_GATE_V16_PATCH_OK")
