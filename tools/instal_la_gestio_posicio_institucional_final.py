from pathlib import Path
import shutil, subprocess, datetime, sys, os

ROOT = Path("/storage/emulated/0/Download/joan_crypto_ai_pro_v14")
SRC = ROOT / "build_gestio_definitiva"
TS = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d_%H%M%S")
BK = ROOT / "backups" / f"install_gestio_posicio_final_{TS}"
DB = ROOT / "data" / "joanbot_v14.sqlite"

REQUIRED = [
    ("joanbot/institutional/gestio_posicio_institucional_neta.py", "joanbot/institutional/gestio_posicio_institucional_neta.py"),
    ("joanbot/execution/broker.py", "joanbot/execution/broker.py"),
    ("tools/prova_gestio_posicio_institucional.py", "tools/prova_gestio_posicio_institucional.py"),
    ("tools/reconstrueix_gestio_posicio_institucional.py", "tools/reconstrueix_gestio_posicio_institucional.py"),
    ("tools/panell_gestio_posicio_institucional.py", "tools/panell_gestio_posicio_institucional.py"),
    ("tools/valida_consolidacio_quantitativa_neta.py", "tools/valida_consolidacio_quantitativa_neta.py"),
]

OPTIONAL = [
    ("INSTRUCCIONS_GESTIO_POSICIO_INSTITUCIONAL_NETA.md", "INSTRUCCIONS_GESTIO_POSICIO_INSTITUCIONAL_NETA.md"),
]

def run(cmd):
    print("\n===== " + " ".join(cmd) + " =====")
    subprocess.check_call(cmd, cwd=str(ROOT))

def copy_backup(path: Path):
    if path.exists():
        dst = BK / "files" / path.relative_to(ROOT)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dst)

def restore():
    print("\n===== ROLLBACK AUTOMATIC =====")
    files = BK / "files"
    if files.exists():
        for p in files.rglob("*"):
            if p.is_file():
                dst = ROOT / p.relative_to(files)
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(p, dst)
    db_bk = BK / "db" / "joanbot_v14.sqlite"
    if db_bk.exists():
        shutil.copy2(db_bk, DB)
    print("ROLLBACK_FET:", BK)

try:
    if not SRC.exists():
        raise SystemExit(f"ERROR: no existeix carpeta font: {SRC}")

    missing = []
    for src_rel, _ in REQUIRED:
        if not (SRC / src_rel).exists():
            missing.append(src_rel)
    if missing:
        raise SystemExit("ERROR: falten fitxers font:\n" + "\n".join(missing))

    BK.mkdir(parents=True, exist_ok=True)
    (BK / "db").mkdir(parents=True, exist_ok=True)

    print("===== ATURAR RUNNER =====")
    subprocess.call("pkill -f 'joanbot.runner' || true", shell=True)
    subprocess.call("sleep 2", shell=True)

    print("===== BACKUP CODI I DB =====")
    for _, dst_rel in REQUIRED + OPTIONAL:
        copy_backup(ROOT / dst_rel)

    if DB.exists():
        shutil.copy2(DB, BK / "db" / "joanbot_v14.sqlite")

    print("BACKUP:", BK)

    print("===== COPIA CONTROLADA =====")
    for src_rel, dst_rel in REQUIRED + OPTIONAL:
        src = SRC / src_rel
        dst = ROOT / dst_rel
        if src.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            print("COPIAT:", dst_rel)

    compile_targets = [
        "joanbot/institutional/gestio_posicio_institucional_neta.py",
        "joanbot/execution/broker.py",
        "tools/prova_gestio_posicio_institucional.py",
        "tools/reconstrueix_gestio_posicio_institucional.py",
        "tools/panell_gestio_posicio_institucional.py",
        "tools/valida_consolidacio_quantitativa_neta.py",
    ]

    run(["python", "-m", "py_compile", *compile_targets])
    run(["python", "tools/valida_consolidacio_quantitativa_neta.py"])
    run(["python", "tools/prova_gestio_posicio_institucional.py"])
    run(["python", "tools/reconstrueix_gestio_posicio_institucional.py"])
    run(["python", "tools/panell_gestio_posicio_institucional.py"])

    print("\n===== INSTAL_LACIO_GESTIO_POSICIO_INSTITUCIONAL_FINAL_OK =====")
    print("backup:", BK)
    print("estat: codi instal·lat, compilat, validat i reconstruït")

except Exception as e:
    print("\nERROR_INSTAL_LACIO:", repr(e))
    restore()
    sys.exit(1)
