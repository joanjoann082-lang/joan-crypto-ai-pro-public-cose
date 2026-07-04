from pathlib import Path
import ast, datetime, hashlib, json, os, re, sqlite3, subprocess, zipfile

ROOT = Path.cwd()
DB = ROOT / "data" / "joanbot_v14.sqlite"
OUTDIR = ROOT / "live_export" / "auditoria_sortides_institucional"
OUTDIR.mkdir(parents=True, exist_ok=True)

TS = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d_%H%M%S")
REPORT_JSON = OUTDIR / f"auditoria_sortides_institucional_{TS}.json"
REPORT_TXT = OUTDIR / f"auditoria_sortides_institucional_{TS}.txt"
PACK_ZIP = OUTDIR / f"pack_codi_sortides_institucional_{TS}.zip"

IMPORTANT_FILES = [
    "joanbot/execution/broker.py",
    "joanbot/execution/paper_broker.py",
    "joanbot/execution/execution_intelligence.py",

    "joanbot/institutional/gestio_posicio_institucional_neta.py",
    "joanbot/institutional/sortides_estadistiques_netes.py",
    "joanbot/institutional/canonical_paper_accounting_v24_4.py",
    "joanbot/institutional/canonical_market_data_contract_v24_9_final.py",
    "joanbot/institutional/nucli_quantitatiu_net.py",
    "joanbot/institutional/outcome_learning_v26.py",
    "joanbot/institutional/quant_core_v27.py",
    "joanbot/institutional/quant_core_v27_2.py",

    "tools/panell_gestio_posicio_institucional.py",
    "tools/valida_gestio_posicio_institucional.py",
    "tools/panell_quantitatiu_net.py",
    "tools/valida_consolidacio_quantitativa_neta.py",
    "tools/valida_preu_canonic_fresc.py",
]

KEYWORDS_AUTORITAT = [
    "gestio_posicio_institucional_neta",
    "GestioPosicioInstitucional",
    "gestio_posicio",
    "sortides_estadistiques",
    "SortidesEstadistiques",
    "profit_guard",
    "take_profit",
    "tp1",
    "tp2",
    "tp3",
    "tp4",
    "partial",
    "parcial",
    "trailing",
    "giveback",
    "retorn_guany",
    "mfe",
    "mae",
    "break_even",
    "breakeven",
    "stop_loss",
    "close_position",
    "mark_positions",
    "open_from_decision",
]

DB_TABLES = [
    "positions",
    "trades",
    "resultats_quant_nets",
    "memoria_edge_neta",
    "estat_causal_quant",
    "estat_promocio_quant",

    "plans_gestio_posicio_neta",
    "mostres_posicio_neta",
    "decisions_gestio_posicio_neta",
    "politica_gestio_posicio_neta",
    "tancaments_posicio_neta",
    "simulacions_sortida_neta",
    "auditoria_gestio_posicio_neta",

    "trajectoria_sortida_neta",
    "mostres_sortida_estadistica_neta",
    "politica_sortida_estadistica_neta",
    "decisions_sortida_estadistica_neta",
    "auditoria_sortida_estadistica_neta",
]

def utc_now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()

def run(cmd, timeout=12):
    try:
        return subprocess.check_output(
            cmd,
            shell=True,
            cwd=str(ROOT),
            text=True,
            stderr=subprocess.STDOUT,
            timeout=timeout,
        ).strip()
    except Exception as e:
        return f"ERROR: {repr(e)}"

def read_file(rel):
    p = ROOT / rel
    if not p.exists():
        return ""
    return p.read_text(encoding="utf-8", errors="ignore")

def file_hash(rel):
    p = ROOT / rel
    if not p.exists():
        return None
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

def count_keywords(text):
    out = {}
    low = text.lower()
    for k in KEYWORDS_AUTORITAT:
        out[k] = low.count(k.lower())
    return {k: v for k, v in out.items() if v}

def find_lines(text, terms, context=2, max_hits=80):
    lines = text.splitlines()
    hits = []
    for i, line in enumerate(lines, start=1):
        low = line.lower()
        if any(t.lower() in low for t in terms):
            start = max(1, i - context)
            end = min(len(lines), i + context)
            snippet = "\n".join(f"{j}: {lines[j-1]}" for j in range(start, end + 1))
            hits.append({"line": i, "snippet": snippet})
            if len(hits) >= max_hits:
                break
    return hits

def ast_functions(rel):
    text = read_file(rel)
    if not text:
        return {"error": "file_missing"}
    try:
        tree = ast.parse(text)
    except Exception as e:
        return {"error": repr(e)}

    lines = text.splitlines()
    funcs = []

    class Visitor(ast.NodeVisitor):
        def __init__(self):
            self.stack = []

        def visit_ClassDef(self, node):
            self.stack.append(node.name)
            self.generic_visit(node)
            self.stack.pop()

        def visit_FunctionDef(self, node):
            qname = ".".join(self.stack + [node.name]) if self.stack else node.name
            body_len = None
            if hasattr(node, "end_lineno") and node.end_lineno:
                body_len = node.end_lineno - node.lineno + 1
            funcs.append({
                "name": qname,
                "line": node.lineno,
                "end_line": getattr(node, "end_lineno", None),
                "body_lines": body_len,
                "args": [a.arg for a in node.args.args],
            })
            self.generic_visit(node)

        def visit_AsyncFunctionDef(self, node):
            self.visit_FunctionDef(node)

    Visitor().visit(tree)
    return funcs

def extract_function(rel, names):
    text = read_file(rel)
    if not text:
        return {}
    try:
        tree = ast.parse(text)
    except Exception as e:
        return {"error": repr(e)}

    lines = text.splitlines()
    wanted = set(names)
    found = {}

    class Visitor(ast.NodeVisitor):
        def __init__(self):
            self.stack = []

        def visit_ClassDef(self, node):
            self.stack.append(node.name)
            self.generic_visit(node)
            self.stack.pop()

        def visit_FunctionDef(self, node):
            qname = ".".join(self.stack + [node.name]) if self.stack else node.name
            short = node.name
            if qname in wanted or short in wanted:
                if getattr(node, "end_lineno", None):
                    src = "\n".join(lines[node.lineno - 1: node.end_lineno])
                    found[qname] = src
            self.generic_visit(node)

    Visitor().visit(tree)
    return found

def connect_db():
    if not DB.exists():
        return None
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    return con

def db_table_exists(con, table):
    try:
        r = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        return bool(r)
    except Exception:
        return False

def db_count(con, table):
    try:
        return con.execute(f'SELECT COUNT(*) c FROM "{table}"').fetchone()["c"]
    except Exception as e:
        return f"ERR: {repr(e)}"

def db_schema(con, table):
    try:
        rows = con.execute(f'PRAGMA table_info("{table}")').fetchall()
        return [{"name": r[1], "type": r[2], "notnull": r[3], "pk": r[5]} for r in rows]
    except Exception as e:
        return [{"error": repr(e)}]

def db_recent(con, table, limit=10):
    if not db_table_exists(con, table):
        return []
    try:
        cols = [r["name"] for r in db_schema(con, table) if "name" in r]
        order_col = None
        for c in ["id", "ts", "updated_at", "opened_at", "created_at", "rowid"]:
            if c in cols or c == "rowid":
                order_col = c
                break

        preferred = [
            "ts", "updated_at", "opened_at", "symbol", "side", "setup", "key", "estat",
            "reason", "accio", "n", "live_n", "forward_n", "avg_r", "pf", "resultat_r",
            "mfe_r", "mae_r", "captura_r", "retorn_guany_r", "mida", "score", "vetos"
        ]
        select_cols = [c for c in preferred if c in cols]
        if not select_cols:
            select_cols = cols[:8]

        sql = f'SELECT {", ".join([chr(34)+c+chr(34) for c in select_cols])} FROM "{table}"'
        if order_col:
            sql += f' ORDER BY "{order_col}" DESC' if order_col != "rowid" else " ORDER BY rowid DESC"
        sql += f" LIMIT {int(limit)}"
        return [dict(r) for r in con.execute(sql).fetchall()]
    except Exception as e:
        return [{"error": repr(e)}]

def db_contamination(con, table):
    if not db_table_exists(con, table):
        return {"exists": False}
    try:
        schema = db_schema(con, table)
        text_cols = [r["name"] for r in schema if "name" in r and (r.get("type") or "").upper() in ("TEXT", "", "VARCHAR")]
        if not text_cols:
            return {"exists": True, "matches": 0, "columns_checked": []}

        markers = ["PROVA", "TEST", "SINTETIC", "SYNTHETIC", "SELF_TEST", "PROVA_GESTIO"]
        where_parts = []
        params = []
        for c in text_cols:
            for m in markers:
                where_parts.append(f'UPPER(CAST("{c}" AS TEXT)) LIKE ?')
                params.append(f"%{m}%")
        where = " OR ".join(where_parts)
        n = con.execute(f'SELECT COUNT(*) c FROM "{table}" WHERE {where}', params).fetchone()["c"]

        sample = []
        if n:
            cols = text_cols[:6]
            sql = f'SELECT {", ".join([chr(34)+c+chr(34) for c in cols])} FROM "{table}" WHERE {where} LIMIT 10'
            sample = [dict(r) for r in con.execute(sql, params).fetchall()]

        return {
            "exists": True,
            "matches": n,
            "columns_checked": text_cols,
            "sample": sample,
        }
    except Exception as e:
        return {"exists": True, "error": repr(e)}

def db_report():
    con = connect_db()
    if con is None:
        return {"db_exists": False}

    out = {"db_exists": True, "tables": {}, "contamination": {}}

    for t in DB_TABLES:
        exists = db_table_exists(con, t)
        item = {
            "exists": exists,
            "count": db_count(con, t) if exists else None,
            "schema": db_schema(con, t) if exists else [],
            "recent": db_recent(con, t) if exists else [],
        }
        out["tables"][t] = item
        if any(x in t for x in ["gestio", "sortida"]):
            out["contamination"][t] = db_contamination(con, t)

    # Resum LIVE per setup, si existeix
    try:
        out["live_setup_summary"] = [
            dict(r) for r in con.execute("""
                SELECT
                  symbol, side, setup,
                  COUNT(*) n,
                  SUM(CASE WHEN resultat_r > 0 THEN 1 ELSE 0 END) wins,
                  ROUND(100.0 * SUM(CASE WHEN resultat_r > 0 THEN 1 ELSE 0 END) / COUNT(*), 2) winrate,
                  ROUND(AVG(resultat_r), 4) avg_r,
                  ROUND(SUM(CASE WHEN resultat_r > 0 THEN resultat_r ELSE 0 END), 4) gross_win_r,
                  ROUND(SUM(CASE WHEN resultat_r < 0 THEN resultat_r ELSE 0 END), 4) gross_loss_r,
                  ROUND(
                    SUM(CASE WHEN resultat_r > 0 THEN resultat_r ELSE 0 END) /
                    NULLIF(ABS(SUM(CASE WHEN resultat_r < 0 THEN resultat_r ELSE 0 END)), 0),
                    4
                  ) pf
                FROM resultats_quant_nets
                WHERE qualitat='NET' AND font='LIVE'
                GROUP BY symbol, side, setup
                ORDER BY avg_r DESC
            """).fetchall()
        ]
    except Exception as e:
        out["live_setup_summary_error"] = repr(e)

    # Posicions obertes sense payload complet
    try:
        out["open_positions"] = [
            dict(r) for r in con.execute("""
                SELECT id, opened_at, symbol, side, setup, status, entry, size_usd, pnl_usd
                FROM positions
                WHERE status='OPEN'
                ORDER BY opened_at DESC
                LIMIT 20
            """).fetchall()
        ]
    except Exception as e:
        out["open_positions_error"] = repr(e)

    con.close()
    return out

def pack_code():
    with zipfile.ZipFile(PACK_ZIP, "w", compression=zipfile.ZIP_DEFLATED) as z:
        manifest = []
        for rel in IMPORTANT_FILES:
            p = ROOT / rel
            if p.exists() and p.is_file():
                z.write(p, rel)
                manifest.append({
                    "path": rel,
                    "sha256": file_hash(rel),
                    "size": p.stat().st_size,
                })

        # També incloure fitxers execution/institutional petits que mencionen sortides/gestió.
        for folder in ["joanbot/execution", "joanbot/institutional"]:
            base = ROOT / folder
            if not base.exists():
                continue
            for p in base.glob("*.py"):
                rel = str(p.relative_to(ROOT))
                if rel in IMPORTANT_FILES:
                    continue
                txt = p.read_text(encoding="utf-8", errors="ignore")
                if any(k.lower() in txt.lower() for k in KEYWORDS_AUTORITAT):
                    z.write(p, rel)
                    manifest.append({
                        "path": rel,
                        "sha256": file_hash(rel),
                        "size": p.stat().st_size,
                        "included_by_keyword": True,
                    })

        z.writestr("AUDIT_MANIFEST.json", json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False))
    return str(PACK_ZIP)

def build_report():
    files_report = {}
    for rel in IMPORTANT_FILES:
        text = read_file(rel)
        p = ROOT / rel
        files_report[rel] = {
            "exists": p.exists(),
            "size": p.stat().st_size if p.exists() else None,
            "sha256": file_hash(rel),
            "keyword_counts": count_keywords(text) if text else {},
            "functions": ast_functions(rel) if text else [],
            "critical_lines": find_lines(text, KEYWORDS_AUTORITAT, context=2, max_hits=60) if text else [],
        }

    broker_extracts = extract_function("joanbot/execution/broker.py", [
        "__init__",
        "PaperBroker.__init__",
        "open_from_decision",
        "PaperBroker.open_from_decision",
        "mark_positions",
        "PaperBroker.mark_positions",
        "close_position",
        "PaperBroker.close_position",
        "refresh",
        "PaperBroker.refresh",
    ])

    gestio_extracts = extract_function("joanbot/institutional/gestio_posicio_institucional_neta.py", [
        "__init__",
        "ensure",
        "registra_pla",
        "crea_pla",
        "crea_pla_inicial",
        "gestiona_posicio",
        "decideix_accions",
        "registra_mostra",
        "registra_tancament",
        "reconstrueix_politica",
        "simula_sortides",
        "actualitza_politica",
    ])

    report = {
        "meta": {
            "created_utc": utc_now(),
            "root": str(ROOT),
            "db_path": str(DB),
        },
        "runtime": {
            "runner_process": run("ps -ef | grep -E 'python -m joanbot.runner|joanbot.runner' | grep -v grep || true"),
            "runner_errors_tail": run("tail -80 data/runner_errors.log 2>/dev/null || true"),
        },
        "git": {
            "branch": run("git rev-parse --abbrev-ref HEAD"),
            "head": run("git rev-parse HEAD"),
            "upstream": run("git rev-parse @{u}"),
            "status_short_no_untracked": run("git status --short --untracked-files=no"),
            "log5": run("git log --oneline --decorate -5"),
        },
        "architecture_checks": {
            "broker_exists": (ROOT / "joanbot/execution/broker.py").exists(),
            "gestio_exists": (ROOT / "joanbot/institutional/gestio_posicio_institucional_neta.py").exists(),
            "lateral_sortides_exists": (ROOT / "joanbot/institutional/sortides_estadistiques_netes.py").exists(),
            "broker_mentions_lateral_sortides": "sortides_estadistiques" in read_file("joanbot/execution/broker.py").lower(),
            "broker_mentions_gestio_institucional": "gestio_posicio_institucional" in read_file("joanbot/execution/broker.py").lower(),
            "broker_mentions_tp3_tp4": bool(re.search(r"tp3|tp4", read_file("joanbot/execution/broker.py"), re.I)),
            "gestio_mentions_tp3_tp4": bool(re.search(r"tp3|tp4", read_file("joanbot/institutional/gestio_posicio_institucional_neta.py"), re.I)),
            "gestio_mentions_mfe_mae": bool(re.search(r"mfe|mae", read_file("joanbot/institutional/gestio_posicio_institucional_neta.py"), re.I)),
            "gestio_mentions_idempotence": bool(re.search(r"idempot|ja_execut|executat|accions|action_key", read_file("joanbot/institutional/gestio_posicio_institucional_neta.py"), re.I)),
            "gestio_blocks_tests": bool(re.search(r"PROVA|TEST|SINTETIC|SYNTHETIC|SELF_TEST", read_file("joanbot/institutional/gestio_posicio_institucional_neta.py"), re.I)),
        },
        "files": files_report,
        "extracts": {
            "broker_critical_functions": broker_extracts,
            "gestio_candidate_functions": gestio_extracts,
        },
        "database": db_report(),
    }

    return report

def write_txt_summary(report):
    lines = []
    a = report["architecture_checks"]
    db = report["database"]

    lines.append("===== AUDITORIA SORTIDES INSTITUCIONAL =====")
    lines.append(f"UTC: {report['meta']['created_utc']}")
    lines.append("")
    lines.append("===== GIT =====")
    for k, v in report["git"].items():
        lines.append(f"{k}: {v}")
    lines.append("")
    lines.append("===== RUNTIME =====")
    lines.append(report["runtime"]["runner_process"] or "RUNNER_NO_TROBAT")
    lines.append("")
    lines.append("===== ARCHITECTURE CHECKS =====")
    for k, v in a.items():
        lines.append(f"{k}: {v}")

    lines.append("")
    lines.append("===== DB COUNTS =====")
    if db.get("db_exists"):
        for t, info in db["tables"].items():
            if info["exists"]:
                lines.append(f"{t}: {info['count']}")
    else:
        lines.append("DB_NO_TROBADA")

    lines.append("")
    lines.append("===== CONTAMINACIO PROVA/TEST EN GESTIO/SORTIDA =====")
    if db.get("db_exists"):
        for t, info in db.get("contamination", {}).items():
            lines.append(f"{t}: matches={info.get('matches')} exists={info.get('exists')}")

    lines.append("")
    lines.append("===== LIVE SETUP SUMMARY =====")
    for r in db.get("live_setup_summary", []):
        lines.append(json.dumps(r, sort_keys=True, ensure_ascii=False))

    lines.append("")
    lines.append("===== OPEN POSITIONS =====")
    for r in db.get("open_positions", []):
        lines.append(json.dumps(r, sort_keys=True, ensure_ascii=False))

    lines.append("")
    lines.append("===== FITXERS CLAU =====")
    for rel, info in report["files"].items():
        lines.append(f"{rel}: exists={info['exists']} size={info['size']} sha={info['sha256']}")
        if info.get("keyword_counts"):
            lines.append("  keywords=" + json.dumps(info["keyword_counts"], sort_keys=True, ensure_ascii=False))

    lines.append("")
    lines.append(f"REPORT_JSON={REPORT_JSON}")
    lines.append(f"REPORT_TXT={REPORT_TXT}")
    lines.append(f"PACK_ZIP={PACK_ZIP}")

    REPORT_TXT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return "\n".join(lines)

def main():
    report = build_report()
    pack_path = pack_code()
    report["artifacts"] = {
        "report_json": str(REPORT_JSON),
        "report_txt": str(REPORT_TXT),
        "pack_zip": pack_path,
    }

    REPORT_JSON.write_text(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False, default=str), encoding="utf-8")
    summary = write_txt_summary(report)

    print(summary)
    print("")
    print("===== RESUM_PER_CHAT =====")
    print("Enganxa'm des de '===== AUDITORIA SORTIDES INSTITUCIONAL =====' fins a 'PACK_ZIP=...'")
    print("Millor encara: puja també aquest ZIP:")
    print(pack_path)

if __name__ == "__main__":
    main()
