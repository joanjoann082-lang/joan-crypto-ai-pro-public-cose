from pathlib import Path
import re

p = Path("joanbot/institutional_v16/alpha_kernel_v16.py")
if not p.exists():
    raise SystemExit("ALPHA_KERNEL_V16_NOT_FOUND")

s = p.read_text()
backup = p.with_suffix(".py.before_v16_3b_storage_spine")
backup.write_text(s)

marker = "ALPHA_KERNEL_V16_3B_STORAGE_SPINE"

if marker in s:
    print("ALPHA_KERNEL_ALREADY_PATCHED_V16_3B")
    raise SystemExit(0)

# imports
if "import hashlib" not in s:
    s = s.replace("import json\n", "import json\nimport hashlib\nimport zlib\n", 1)
elif "import zlib" not in s:
    s = s.replace("import hashlib\n", "import hashlib\nimport zlib\n", 1)

helper = r'''
# ALPHA_KERNEL_V16_3B_STORAGE_SPINE
def stable_payload_json_v16(payload):
    try:
        return json.dumps(payload or {}, separators=(",", ":"), sort_keys=True, ensure_ascii=False, default=str)
    except Exception:
        return json.dumps({"payload_repr": repr(payload)}, separators=(",", ":"), sort_keys=True, ensure_ascii=False, default=str)


def archive_payload_v16(cur, payload, kind="alpha"):
    raw = stable_payload_json_v16(payload)
    raw_b = raw.encode("utf-8")
    payload_hash = hashlib.sha256(raw_b).hexdigest()
    compressed = zlib.compress(raw_b, 6)

    cur.execute("""
        INSERT INTO alpha_payload_library_v16 (
            payload_hash, created_at, version, kind,
            raw_bytes, compressed_bytes, use_count, compressed_payload
        )
        VALUES (?, ?, ?, ?, ?, ?, 1, ?)
        ON CONFLICT(payload_hash) DO UPDATE SET
            use_count = use_count + 1;
    """, (
        payload_hash,
        now_iso(),
        VERSION,
        kind,
        len(raw_b),
        len(compressed),
        compressed,
    ))

    slim = {
        "storage": "ALPHA_PAYLOAD_LIBRARY_V16",
        "payload_ref": payload_hash,
        "kind": kind,
        "raw_bytes": len(raw_b),
        "compressed_bytes": len(compressed),
        "compression": "zlib",
        "archived": True,
    }

    return payload_hash, json.dumps(slim, separators=(",", ":"), sort_keys=True, ensure_ascii=False, default=str)
'''

s = s.replace("\ndef clamp(x, lo, hi):", "\n" + helper + "\ndef clamp(x, lo, hi):", 1)

schema = r'''
        cur.execute("""
            CREATE TABLE IF NOT EXISTS alpha_payload_library_v16 (
                payload_hash TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                version TEXT NOT NULL,
                kind TEXT NOT NULL,
                raw_bytes INTEGER NOT NULL,
                compressed_bytes INTEGER NOT NULL,
                use_count INTEGER NOT NULL DEFAULT 1,
                compressed_payload BLOB NOT NULL
            );
        """)
'''

needle = '        cur.execute("""\n            CREATE TABLE IF NOT EXISTS alpha_data_contract_v16'
if needle not in s:
    p.write_text(backup.read_text())
    raise SystemExit("SCHEMA_INSERT_POINT_NOT_FOUND_ROLLBACK")

s = s.replace(needle, schema + "\n" + needle, 1)

needle2 = "    def write_research_and_registry(self, cur, s):\n        cur.execute("
if needle2 not in s:
    p.write_text(backup.read_text())
    raise SystemExit("WRITE_RESEARCH_INSERT_POINT_NOT_FOUND_ROLLBACK")

s = s.replace(
    needle2,
    '    def write_research_and_registry(self, cur, s):\n'
    '        _payload_hash, _slim_payload = archive_payload_v16(cur, s.get("payload", {}), "research_registry")\n'
    '        cur.execute(',
    1,
)

# Replace heavy payload writes in this function.
s = s.replace('js(s["payload"])', '_slim_payload')

p.write_text(s)
print("PATCH_ALPHA_KERNEL_STORAGE_SPINE_OK")
