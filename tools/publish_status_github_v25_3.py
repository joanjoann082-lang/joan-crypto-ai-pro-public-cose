from pathlib import Path
import os, json, base64, urllib.request, urllib.error, datetime, subprocess, sqlite3

ROOT = Path("/storage/emulated/0/Download/joan_crypto_ai_pro_v14")
ENV = ROOT / "data" / "github_status_publish.env"

def load_env():
    if ENV.exists():
        for line in ENV.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ[k.strip()] = v.strip()

def utc():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()

def read_json(path):
    try:
        p = ROOT / path
        if p.exists():
            return json.loads(p.read_text())
    except Exception as e:
        return {"error": repr(e)}
    return {}

def sh(cmd):
    try:
        return subprocess.check_output(cmd, shell=True, text=True, stderr=subprocess.STDOUT, timeout=5).strip()
    except Exception as e:
        return f"ERROR: {e}"


def query_db(sql, params=()):
    db_path = ROOT / "data" / "joanbot_v14.sqlite"
    if not db_path.exists():
        return []
    try:
        con = sqlite3.connect(str(db_path))
        con.row_factory = sqlite3.Row
        rows = [dict(r) for r in con.execute(sql, params).fetchall()]
        con.close()
        return rows
    except Exception as e:
        return [{"error": repr(e)}]

def build_quant_net_status():
    counts = {}
    for table in ["resultats_quant_nets", "exclusions_qualitat_dades", "memoria_edge_neta", "estat_promocio_quant", "auditoria_quant_neta"]:
        rows = query_db(f"SELECT COUNT(*) c FROM {table}")
        counts[table] = rows[0].get("c") if rows and "error" not in rows[0] else None

    quality = query_db("""
        SELECT qualitat,font,COUNT(*) n
        FROM resultats_quant_nets
        GROUP BY qualitat,font
        ORDER BY qualitat,font
    """)
    states = query_db("""
        SELECT estat,COUNT(*) n,ROUND(AVG(live_exp_r),5) live_exp,ROUND(AVG(forward_exp_r),5) forward_exp
        FROM estat_promocio_quant
        GROUP BY estat
        ORDER BY n DESC
    """)
    top = query_db("""
        SELECT key,estat,ROUND(score_compost,2) score,live_n,ROUND(live_exp_r,4) live_exp,forward_n,ROUND(forward_exp_r,4) forward_exp,ROUND(mida_recomanada_usd,2) size
        FROM estat_promocio_quant
        WHERE estat!='QUARANTENA'
        ORDER BY estat='VALIDAT' DESC, estat='CANARI' DESC, estat='EXPLORAR' DESC, score_compost DESC
        LIMIT 10
    """)
    quarantine = query_db("""
        SELECT key,live_n,ROUND(live_exp_r,4) live_exp,forward_n,ROUND(forward_exp_r,4) forward_exp,vetos
        FROM estat_promocio_quant
        WHERE estat='QUARANTENA'
        ORDER BY live_exp_r ASC, forward_exp_r ASC
        LIMIT 10
    """)

    return "\n".join([
        "NUCLI QUANTITATIU NET:",
        "comptadors: " + json.dumps(counts, sort_keys=True),
        "qualitat: " + json.dumps(quality, ensure_ascii=False, sort_keys=True),
        "estats: " + json.dumps(states, ensure_ascii=False, sort_keys=True),
        "top_executable: " + json.dumps(top, ensure_ascii=False, sort_keys=True),
        "quarantena: " + json.dumps(quarantine, ensure_ascii=False, sort_keys=True),
    ])

def api(method, url, token, payload=None):
    data = None
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "joan-bot-status-publisher",
    }
    if payload is not None:
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=data, headers=headers, method=method)

    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            body = r.read().decode()
            return r.status, json.loads(body or "{}")
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="ignore")
        try:
            body = json.loads(body)
        except Exception:
            pass
        return e.code, body

def build_status():
    status = read_json("live_export/status.json")
    money = status.get("money", status if isinstance(status, dict) else {})
    health = status.get("health", {})
    control = read_json("data/runtime_controls_v25.json")

    process = sh("ps -ef | grep -E 'joanbot.runner|runner.py' | grep -v grep || true")
    guard_tail = sh("tail -10 data/runtime_guard_v25_2b.jsonl 2>/dev/null || true")
    err_tail = sh("tail -20 data/runner_errors.log 2>/dev/null || true")

    return f"""JOAN BOT LIVE STATUS

updated_utc: {utc()}

PROCESS:
{process}

HEALTH:
state: {health.get("state") if isinstance(health, dict) else health}
problems: {health.get("problems") if isinstance(health, dict) else None}
status_updated: {status.get("ts") or status.get("updated_utc") or money.get("ts")}

MONEY:
base_equity: {money.get("base_equity")}
marked_equity: {money.get("marked_equity")}
cash_balance_realized: {money.get("cash_balance_realized") or money.get("cash")}
open_positions: {money.get("open_positions")}
open_exposure_usd: {money.get("open_exposure_usd")}
realized_pnl_usd: {money.get("realized_pnl_usd")}
total_pnl_usd: {money.get("total_pnl_usd")}
win_rate_pct: {money.get("win_rate_pct")}
profit_factor: {money.get("profit_factor")}
expectancy_usd_per_trade: {money.get("expectancy_usd_per_trade")}
max_realized_drawdown_usd: {money.get("max_realized_drawdown_usd")}

CONTROL:
{json.dumps(control, indent=2, sort_keys=True)}

GUARD_LAST:
{guard_tail}

{build_quant_net_status()}

RUNNER_ERRORS_LAST:
{err_tail}
"""

def main():
    load_env()

    token = os.environ.get("GITHUB_TOKEN")
    owner = os.environ.get("GITHUB_OWNER")
    repo = os.environ.get("GITHUB_REPO")
    branch = os.environ.get("GITHUB_BRANCH", "main")
    path = os.environ.get("GITHUB_PATH", "bot_status.txt")

    if not token or "POSA_EL_TOKEN" in token:
        raise SystemExit("ERROR: falta GITHUB_TOKEN a data/github_status_publish.env")
    if not owner or "POSA_EL_TEU" in owner:
        raise SystemExit("ERROR: falta GITHUB_OWNER a data/github_status_publish.env")
    if not repo:
        raise SystemExit("ERROR: falta GITHUB_REPO")

    text = build_status()
    (ROOT / "live_export" / "github_public_status_preview.txt").write_text(text, encoding="utf-8")

    url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}"
    get_url = f"{url}?ref={branch}"

    status_code, current = api("GET", get_url, token)
    sha = current.get("sha") if isinstance(current, dict) else None

    payload = {
        "message": f"update bot status {utc()}",
        "content": base64.b64encode(text.encode()).decode(),
        "branch": branch,
    }

    if sha:
        payload["sha"] = sha

    put_code, put_resp = api("PUT", url, token, payload)

    if put_code not in (200, 201):
        raise SystemExit(f"ERROR_GITHUB_PUBLISH {put_code}: {put_resp}")

    raw_url = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{path}"
    print("PUBLISHED_OK")
    print(raw_url)

if __name__ == "__main__":
    main()
