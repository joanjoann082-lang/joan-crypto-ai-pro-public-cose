from pathlib import Path
import shutil, datetime

ROOT = Path(__file__).resolve().parents[1]
LEGACY = ROOT / "_legacy_no_executar" / datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d_%H%M%S")
LEGACY.mkdir(parents=True, exist_ok=True)

PATRONS = [
    "install_v25_*.py",
    "install_v26*.py",
    "install_v27*.py",
]
FITXERS = [
    "tools/v26_learning_dashboard.py",
    "tools/v27_quant_dashboard.py",
    "tools/v27_self_test.py",
    "tools/v27_2_clean_quant_dashboard.py",
    "tools/v27_2_self_test.py",
]

moguts = []
for pat in PATRONS:
    for p in ROOT.glob(pat):
        if p.is_file():
            dst = LEGACY / p.name
            shutil.move(str(p), str(dst))
            moguts.append(str(p.relative_to(ROOT)))
for rel in FITXERS:
    p = ROOT / rel
    if p.exists() and p.is_file():
        dst = LEGACY / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(p), str(dst))
        moguts.append(rel)

README = LEGACY / "LLEGEIX_ME.txt"
README.write_text(
    "Fitxers moguts perquè són instal·ladors o eines de versions anteriors.\n"
    "No s'han esborrat. No formen part del runtime consolidat.\n"
    "Runtime oficial: joanbot.runner + nucli_quantitatiu_net.\n",
    encoding="utf-8",
)
print("SCRIPTS_OBSOLETS_MOGUTS_A:", LEGACY)
for x in moguts:
    print("-", x)
