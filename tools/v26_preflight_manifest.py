from pathlib import Path
import sqlite3, json, hashlib, datetime

ROOT = Path("/storage/emulated/0/Download/joan_crypto_ai_pro_v14")
DB = ROOT / "data" / "joanbot_v14.sqlite"

files = [
    "joanbot/runner.py",
    "joanbot/execution/broker.py",
    "joanbot/intelligence/decision.py",
    "joanbot/intelligence/memory.py",
    "joanbot/intelligence/risk.py",
    "joanbot/intelligence/statistical_edge_authority_v1.py",
    "joanbot/storage/db.py",
    "joanbot/models.py",
    "joanbot/config.py",
    "data/runtime_controls_v25.json",
]

def sha(p):
    try:
        b = p.read_bytes()
        return hashlib.sha256(b).hexdigest()[:16], len(b)
    except Exception as e:
        return f"ERR:{e}", 0

out = {
    "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "root": str(ROOT),
    "db_exists": DB.exists(),
    "files": {},
    "tables": {},
    "schemas": {},
}

for f in files:
    p = ROOT / f
    h, n = sha(p)
    out["files"][f] = {"exists": p.exists(), "sha16": h, "bytes": n}

if DB.exists():
    con = sqlite3.connect(str(DB))
    con.row_factory = sqlite3.Row
    tables = [r["name"] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    for t in tables:
        try:
            cnt = con.execute(f"SELECT COUNT(*) c FROM {t}").fetchone()["c"]
            cols = [dict(r) for r in con.execute(f"PRAGMA table_info({t})")]
            out["tables"][t] = cnt
            out["schemas"][t] = cols
        except Exception as e:
            out["tables"][t] = f"ERR:{e}"

p = ROOT / "live_export" / "v26_preflight_manifest.json"
p.parent.mkdir(parents=True, exist_ok=True)
p.write_text(json.dumps(out, indent=2, sort_keys=True), encoding="utf-8")

print(json.dumps(out, indent=2, sort_keys=True))
