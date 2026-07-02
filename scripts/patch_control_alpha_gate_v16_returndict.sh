#!/data/data/com.termux/files/usr/bin/bash
cd "$(dirname "$0")/.."
export PYTHONPATH=$PWD

python - <<'PY'
from pathlib import Path
import re

p = Path("joanbot/control/control_plane_v11.py")
if not p.exists():
    raise SystemExit("CONTROL_PLANE_NOT_FOUND")

orig = p.read_text()
backup = p.with_suffix(".py.before_alpha_gate_v16_returndict")
backup.write_text(orig)

s = orig

# Elimina gates antics per evitar solapaments.
s = re.sub(r"^from joanbot\.institutional_v14\.final_gate_adapter_v14 import apply_final_gate_v14\n", "", s, flags=re.M)
s = re.sub(r"^from joanbot\.institutional_v15\.final_gate_adapter_v15 import apply_alpha_final_gate_v15\n", "", s, flags=re.M)
s = re.sub(r"^from joanbot\.institutional_v16\.final_gate_adapter_v16 import apply_alpha_final_gate_v16\n", "", s, flags=re.M)

# Desembolica wrappers antics si existien.
s = re.sub(r"return apply_final_gate_v14\((\w+)\)", r"return \1", s)
s = re.sub(r"return apply_alpha_final_gate_v15\((\w+)\)", r"return \1", s)
s = re.sub(r"return apply_alpha_final_gate_v16\((\w+)\)", r"return \1", s)

# Import únic V16.
s = "from joanbot.institutional_v16.final_gate_adapter_v16 import apply_alpha_final_gate_v16\n" + s

needle = "    def refresh(self) -> Dict[str, Any]:"
start = s.find(needle)
if start < 0:
    p.write_text(orig)
    raise SystemExit("REFRESH_METHOD_NOT_FOUND_ROLLBACK")

end = s.find("\n    def ", start + len(needle))
if end < 0:
    end = len(s)

method = s[start:end]

ret_pos = method.find("return {")
if ret_pos < 0:
    p.write_text(orig)
    raise SystemExit("RETURN_DICT_NOT_FOUND_ROLLBACK")

brace_pos = method.find("{", ret_pos)
depth = 0
end_brace = None

for idx in range(brace_pos, len(method)):
    ch = method[idx]
    if ch == "{":
        depth += 1
    elif ch == "}":
        depth -= 1
        if depth == 0:
            end_brace = idx
            break

if end_brace is None:
    p.write_text(orig)
    raise SystemExit("RETURN_DICT_BRACE_MATCH_FAILED_ROLLBACK")

dict_expr = method[ret_pos + len("return "):end_brace + 1]

replacement = (
    "contract_v16 = " + dict_expr +
    "\n        return apply_alpha_final_gate_v16(contract_v16)"
)

method2 = method[:ret_pos] + replacement + method[end_brace + 1:]
s2 = s[:start] + method2 + s[end:]

p.write_text(s2)
print("CONTROL_ALPHA_GATE_V16_RETURN_DICT_PATCH_OK")
PY
