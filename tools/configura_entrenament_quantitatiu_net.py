from pathlib import Path
import json
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
P = ROOT / "data" / "runtime_controls_v25.json"
P.parent.mkdir(parents=True, exist_ok=True)
try:
    data = json.loads(P.read_text(encoding="utf-8")) if P.exists() else {}
except Exception:
    data = {}

data.update({
    "version": "CONTROL_OPERATIU_NATIU",
    "enabled": True,
    "trading_paused": False,
    "mode": "entrenament_quantitatiu_net",
    "base_min_score": 52.0,
    "score_floor_delta": -10.0,
    "min_score_override": None,
    "risk_multiplier": 1.5,
    "max_global_open": 5,
    "allow_longs": True,
    "allow_shorts": True,
    "updated_utc": datetime.now(timezone.utc).isoformat(),
    "updated_by": "configura_entrenament_quantitatiu_net",
    "nota": "El bloqueig LONG/SHORT el decideix el nucli quantitatiu net per estat i evidència, no per apagada global.",
})
P.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")

# Variables del nucli net. Es deixen a .env perquè el codi les llegeix amb os.getenv.
env_path = ROOT / ".env"
try:
    old_lines = env_path.read_text(encoding="utf-8", errors="ignore").splitlines() if env_path.exists() else []
except Exception:
    old_lines = []
keys = {
    "ENTRENAMENT_PAPER_NET_ACTIU": "1",
    "MIDA_RECERCA_USD": "8000",
    "MIDA_EXPLORAR_USD": "12000",
    "MIDA_CANARI_USD": "20000",
    "MIDA_VALIDAT_USD": "40000",
    "MIDA_MAXIMA_POSICIO_USD": "50000",
    "SCORE_MIN_OBRIR_RECERCA": "42",
    "SCORE_MIN_OBRIR_EDGE_NET": "52",
    "MAX_R_ABSOLUTA_ADMESA": "8.0",
    "RECERCA_OBRE_ENTRENAMENT_PAPER": "1",
}
clean = []
for line in old_lines:
    k = line.split("=", 1)[0].strip() if "=" in line else None
    if k in keys:
        continue
    clean.append(line)
clean.append("")
clean.append("# === NUCLI_QUANTITATIU_NET ===")
for k, v in keys.items():
    clean.append(f"{k}={v}")
env_path.write_text("\n".join(clean).strip() + "\n", encoding="utf-8")

print("CONTROL_OPERATIU_CONFIGURAT")
print(json.dumps(data, indent=2, sort_keys=True))
print("ENV_QUANTITATIU_NET_CONFIGURAT")
