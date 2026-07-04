from pathlib import Path
import sqlite3, shutil, datetime, json, sys

ROOT = Path("/storage/emulated/0/Download/joan_crypto_ai_pro_v14")
DB = ROOT / "data" / "joanbot_v14.sqlite"
TS = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d_%H%M%S")
BK = ROOT / "backups" / f"higiene_proves_gestio_posicio_{TS}"
BK.mkdir(parents=True, exist_ok=True)

TAULES = [
    "plans_gestio_posicio_neta",
    "mostres_posicio_neta",
    "tancaments_posicio_neta",
    "politica_gestio_posicio_neta",
    "decisions_gestio_posicio_neta",
    "simulacions_sortida_neta",
    "auditoria_gestio_posicio_neta",
]

MARCADORS = [
    "PROVA_GESTIO",
    "PROVA_GESTIO_POSICIO",
]

def qcol(c):
    return '"' + c.replace('"', '""') + '"'

if not DB.exists():
    print("ERROR: no existeix DB", DB)
    sys.exit(1)

# Backup DB abans de tocar res.
(BK / "data").mkdir(parents=True, exist_ok=True)
shutil.copy2(DB, BK / "data" / "joanbot_v14.sqlite")

con = sqlite3.connect(str(DB))
con.row_factory = sqlite3.Row

export = {}
total = 0

for t in TAULES:
    try:
        cols_info = con.execute(f"PRAGMA table_info({t})").fetchall()
        if not cols_info:
            continue

        cols = [r[1] for r in cols_info]
        text_cols = []
        for r in cols_info:
            name = r[1]
            typ = str(r[2] or "").upper()
            if (
                "TEXT" in typ
                or name.lower() in {"key", "setup", "payload", "reason", "accio", "etiqueta", "symbol", "side", "posicio_id"}
            ):
                text_cols.append(name)

        if not text_cols:
            continue

        parts = []
        params = []
        for c in text_cols:
            for m in MARCADORS:
                parts.append(f"UPPER(CAST({qcol(c)} AS TEXT)) LIKE ?")
                params.append(f"%{m.upper()}%")

        where = " OR ".join(parts)
        rows = con.execute(f"SELECT rowid, * FROM {t} WHERE {where}", params).fetchall()
        export[t] = [dict(r) for r in rows]

        n = len(rows)
        if n:
            con.execute(f"DELETE FROM {t} WHERE {where}", params)
            total += n

        print(t, "eliminats", n)

    except Exception as e:
        print(t, "ERROR", repr(e))

con.commit()

# Export de les files eliminades per auditoria.
(BK / "exports").mkdir(parents=True, exist_ok=True)
(BK / "exports" / "proves_gestio_eliminades.json").write_text(
    json.dumps(export, indent=2, sort_keys=True, default=str),
    encoding="utf-8",
)

# Blindar el test perquè no deixi contaminació futura.
test_path = ROOT / "tools" / "prova_gestio_posicio_institucional.py"

if test_path.exists():
    backup_test = BK / "tools" / "prova_gestio_posicio_institucional.py"
    backup_test.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(test_path, backup_test)

    txt = test_path.read_text(encoding="utf-8")

    snippet = r'''

# === HIGIENE_PROVES_GESTIO_POSICIO_V1 ===
# Les proves funcionals poden crear mostres sintètiques, però no poden quedar dins l'aprenentatge net.
try:
    import sqlite3 as _sqlite3
    from pathlib import Path as _Path

    _DB = _Path("data/joanbot_v14.sqlite")
    if _DB.exists():
        _con = _sqlite3.connect(str(_DB))
        _con.row_factory = _sqlite3.Row
        _taules = [
            "plans_gestio_posicio_neta",
            "mostres_posicio_neta",
            "tancaments_posicio_neta",
            "politica_gestio_posicio_neta",
            "decisions_gestio_posicio_neta",
            "simulacions_sortida_neta",
            "auditoria_gestio_posicio_neta",
        ]
        _marcadors = ["PROVA_GESTIO", "PROVA_GESTIO_POSICIO"]
        _total = 0

        def _qcol(c):
            return '"' + c.replace('"', '""') + '"'

        for _t in _taules:
            try:
                _cols_info = _con.execute(f"PRAGMA table_info({_t})").fetchall()
                if not _cols_info:
                    continue
                _text_cols = []
                for _r in _cols_info:
                    _name = _r[1]
                    _typ = str(_r[2] or "").upper()
                    if "TEXT" in _typ or _name.lower() in {"key", "setup", "payload", "reason", "accio", "etiqueta", "symbol", "side", "posicio_id"}:
                        _text_cols.append(_name)
                if not _text_cols:
                    continue
                _parts = []
                _params = []
                for _c in _text_cols:
                    for _m in _marcadors:
                        _parts.append(f"UPPER(CAST({_qcol(_c)} AS TEXT)) LIKE ?")
                        _params.append(f"%{_m.upper()}%")
                _where = " OR ".join(_parts)
                _n = _con.execute(f"SELECT COUNT(*) c FROM {_t} WHERE {_where}", _params).fetchone()["c"]
                if _n:
                    _con.execute(f"DELETE FROM {_t} WHERE {_where}", _params)
                    _total += int(_n)
            except Exception:
                pass
        _con.commit()
        print("HIGIENE_PROVES_GESTIO_POSICIO_OK", _total)
except Exception as _e:
    print("HIGIENE_PROVES_GESTIO_POSICIO_WARN", repr(_e))
'''

    if "HIGIENE_PROVES_GESTIO_POSICIO_V1" not in txt:
        test_path.write_text(txt.rstrip() + "\n\n" + snippet + "\n", encoding="utf-8")

print("HIGIENE_PROVES_GESTIO_POSICIO_COMPLETADA")
print("TOTAL_ELIMINAT:", total)
print("BACKUP:", BK)
