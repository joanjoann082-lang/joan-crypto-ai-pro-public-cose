from pathlib import Path
import shutil, os, time

ROOT = Path("/storage/emulated/0/Download/joan_crypto_ai_pro_v14")
DOWNLOAD = Path("/storage/emulated/0/Download")

KEEP_BACKUPS = 3

NO_DELETE = {
    ROOT / "data" / "joanbot_v14.sqlite",
    ROOT / ".env",
    ROOT / "data" / "github_status_publish.env",
    ROOT / ".git",
    ROOT / "joanbot",
    ROOT / "tools",
}

def size_path(p: Path) -> int:
    try:
        if not p.exists():
            return 0
        if p.is_file():
            return p.stat().st_size
        total = 0
        for x in p.rglob("*"):
            try:
                if x.is_file():
                    total += x.stat().st_size
            except Exception:
                pass
        return total
    except Exception:
        return 0

def human(n: int) -> str:
    for unit in ["B","KB","MB","GB","TB"]:
        if n < 1024:
            return f"{n:.2f}{unit}"
        n /= 1024
    return f"{n:.2f}PB"

def safe_delete(p: Path):
    p = p.resolve()
    for nd in NO_DELETE:
        try:
            if p == nd.resolve() or nd.resolve() in p.parents:
                print("NO_ELIMINO_PROTEGIT:", p)
                return 0
        except Exception:
            pass

    if not p.exists():
        return 0

    sz = size_path(p)
    if p.is_dir():
        shutil.rmtree(p, ignore_errors=True)
    else:
        try:
            p.unlink()
        except FileNotFoundError:
            pass
    print("ELIMINAT:", human(sz), p)
    return sz

print("===== NETEJA ESPAI SEGURA BOT =====")

freed = 0

# 1) Builds duplicats extrets
candidates_dirs = [
    ROOT / "build_gestio_definitiva",
    ROOT / "build_gestio_final",
    DOWNLOAD / "build_gestio_definitiva",
    DOWNLOAD / "build_gestio_final",
]

for p in candidates_dirs:
    freed += safe_delete(p)

# 2) ZIPs de paquets generats
for pattern in [
    "joan_crypto_ai_pro_v14_*.zip",
    "*GESTIO_POSICIO*.zip",
    "*APRENENTATGE_CAUSAL*.zip",
    "*EXPERIMENTACIO_ACTIVA*.zip",
]:
    for p in DOWNLOAD.glob(pattern):
        freed += safe_delete(p)
    for p in ROOT.glob(pattern):
        freed += safe_delete(p)

# 3) __pycache__
for p in list(ROOT.rglob("__pycache__")):
    freed += safe_delete(p)

# 4) Logs grans: deixar-los buits, no eliminar fitxer
for p in [
    ROOT / "data" / "runner_stdout.log",
    ROOT / "data" / "runner_errors.log",
]:
    if p.exists() and p.is_file():
        sz = p.stat().st_size
        if sz > 2_000_000:
            p.write_text("", encoding="utf-8")
            freed += sz
            print("TRUNCAT_LOG:", human(sz), p)

for p in (ROOT / "logs").glob("*.log") if (ROOT / "logs").exists() else []:
    freed += safe_delete(p)

# 5) Backups antics: mantenir els 3 últims
backup_dir = ROOT / "backups"
if backup_dir.exists():
    backups = [p for p in backup_dir.iterdir() if p.is_dir()]
    backups.sort(key=lambda x: x.stat().st_mtime if x.exists() else 0, reverse=True)

    keep = backups[:KEEP_BACKUPS]
    delete = backups[KEEP_BACKUPS:]

    print("BACKUPS_CONSERVATS:")
    for p in keep:
        print(" -", p.name, human(size_path(p)))

    print("BACKUPS_A_ELIMINAR:", len(delete))
    for p in delete:
        freed += safe_delete(p)

print("===== TOTAL_ALLIBERAT =====")
print(human(freed))
