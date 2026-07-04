from pathlib import Path
import ast, subprocess, sys, json

ROOT = Path(__file__).resolve().parents[1]

FITXERS_OBLIGATORIS = [
    "joanbot/institutional/nucli_quantitatiu_net.py",
    "joanbot/institutional/aprenentatge_causal_net.py",
    "joanbot/intelligence/decision.py",
    "joanbot/execution/broker.py",
    "joanbot/execution/contract.py",
    "joanbot/runner.py",
    "tools/prova_nucli_quantitatiu_net.py",
    "tools/reconstrueix_nucli_quantitatiu_net.py",
    "tools/panell_quantitatiu_net.py",
    "tools/configura_entrenament_quantitatiu_net.py",
    "tools/publish_status_github_v25_3.py",
    "joanbot/institutional/gestio_posicio_institucional_neta.py",
    "tools/prova_gestio_posicio_institucional.py",
    "tools/reconstrueix_gestio_posicio_institucional.py",
    "tools/panell_gestio_posicio_institucional.py",
]

def fail(msg):
    raise SystemExit(f"VALIDACIO_FALLIDA: {msg}")

def read(rel):
    p = ROOT / rel
    if not p.exists():
        fail(f"falta {rel}")
    return p.read_text(encoding="utf-8", errors="ignore")

# 1. Fitxers i sintaxi.
for rel in FITXERS_OBLIGATORIS:
    txt = read(rel)
    try:
        ast.parse(txt)
    except SyntaxError as e:
        fail(f"syntax error {rel}: {e}")

# 2. Integració nativa. No accepta runtime principal sense nucli net.
runner = read("joanbot/runner.py")
decision = read("joanbot/intelligence/decision.py")
broker = read("joanbot/execution/broker.py")
contract = read("joanbot/execution/contract.py")
publisher = read("tools/publish_status_github_v25_3.py")
context = read("joanbot/features/context.py")
strategy = read("joanbot/intelligence/strategy.py")
nucli = read("joanbot/institutional/nucli_quantitatiu_net.py")
gestio_posicio = read("joanbot/institutional/gestio_posicio_institucional_neta.py")

checks = {
    "runner_importa_nucli": "nucli_quantitatiu_net import get_core as NucliQuantitatiuNet" in runner,
    "runner_usa_forward_net": "registra_forward" in runner and "UNKNOWN','UNKNOWN','UNKNOWN','UNKNOWN" not in runner,
    "decision_importa_nucli": "nucli_quantitatiu_net import get_core as NucliQuantitatiuNet" in decision,
    "decision_ajusta_edge": "ajusta_edge_candidat" in decision,
    "decision_aplica_politica": "aplica_politica_decisio" in decision,
    "broker_importa_nucli": "nucli_quantitatiu_net import get_core as NucliQuantitatiuNet" in broker,
    "broker_pnl_r_real": "calcula_r_live" in broker and "pnl,0,fees" not in broker,
    "contract_quarantena_dinamica": "QUARANTINED_SETUPS = set()" in contract,
    "publisher_publica_nucli": "NUCLI QUANTITATIU NET" in publisher and "estat_promocio_quant" in publisher,
    "context_crea_mapa_causal": "mapa_causal" in context and "MotorAprenentatgeCausalNet" in context,
    "strategy_usa_score_causal": "score_causal" in strategy and "CAUSAL_" in strategy,
    "nucli_claus_causals": "claus_causals" in nucli and "estat_causal_quant" in nucli,
    "mostreig_actiu_recerca": "WAIT_A_OPEN_PER_MOSTREIG_ACTIU_RECERCA" in nucli,
    "gestio_posicio_institucional": "GestioPosicioInstitucionalNeta" in gestio_posicio and "politica_gestio_posicio_neta" in gestio_posicio,
    "gestio_plans_inicials": "plans_gestio_posicio_neta" in gestio_posicio and "crea_pla_inicial" in gestio_posicio,
    "gestio_simulacions_sortida": "simulacions_sortida_neta" in gestio_posicio and "simula_sortides" in gestio_posicio,
    "gestio_fraccio_posicio_actual": "fraccio_sobre_posicio_actual" in gestio_posicio and "CURRENT_REMAINING_POSITION" in broker,
    "gestio_idempotent": "marca_accio" in gestio_posicio and "gestio_accions" in broker,
    "broker_crea_pla_gestio": "crea_pla_inicial" in broker and "pla_gestio_posicio_institucional_neta" in broker,
    "broker_usa_gestio_posicio": "gestio_posicio_institucional_neta" in broker and "decideix_accio" in broker and "TANCAR_PARCIAL" in broker,
}
for k, ok in checks.items():
    if not ok:
        fail(k)

# 3. No hi ha bootstraps antics actius al runner.
for bad in ["V26_INSTITUTIONAL_LEARNING_BOOT", "V25_2B_RUNNER_GUARD_BOOT", "hard_runtime_guard_v25_2"]:
    if bad in runner:
        fail(f"runner conté bootstrap antic actiu: {bad}")

# 4. Compilació real.
cmd = [sys.executable, "-m", "py_compile"] + FITXERS_OBLIGATORIS
res = subprocess.run(cmd, cwd=str(ROOT), text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
if res.returncode != 0:
    fail("py_compile: " + res.stdout)

print("VALIDACIO_CONSOLIDACIO_QUANTITATIVA_NETA_OK")
print(json.dumps(checks, indent=2, sort_keys=True))
