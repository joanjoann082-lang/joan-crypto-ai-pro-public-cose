import sqlite3, json, math
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path("/storage/emulated/0/Download/joan_crypto_ai_pro_v14")

def find_db():
    candidates = [
        ROOT / "data" / "trading_journal.sqlite",
        ROOT / "data" / "joanbot.sqlite",
        ROOT / "data" / "bot.sqlite",
    ]
    for p in candidates:
        if p.exists():
            return p
    found = list((ROOT / "data").glob("*.sqlite")) + list((ROOT / "data").glob("*.db"))
    return found[0] if found else None

def f(x, default=0.0):
    try:
        if x is None:
            return default
        return float(x)
    except Exception:
        return default

def q(con, sql):
    try:
        con.row_factory = sqlite3.Row
        return [dict(r) for r in con.execute(sql).fetchall()]
    except Exception as e:
        return [{"ERROR": repr(e), "SQL": sql[:120]}]

def table_exists(con, name):
    r = q(con, f"SELECT name FROM sqlite_master WHERE type='table' AND name='{name}'")
    return bool(r and "ERROR" not in r[0])

db = find_db()
print("===== EDGE LEARNING AUDIT V25.4 =====")
print("UTC:", datetime.now(timezone.utc).isoformat())
print("DB:", db)

if not db:
    raise SystemExit("NO_DB_FOUND")

con = sqlite3.connect(str(db))
con.row_factory = sqlite3.Row

print("\n===== TABLE COUNTS =====")
for t in ["decisions","positions","trades","edge_memory","forward_cases","forward_results","runtime_events"]:
    if table_exists(con,t):
        c = q(con, f"SELECT COUNT(*) c FROM {t}")[0]["c"]
        print(f"{t}: {c}")
    else:
        print(f"{t}: MISSING")

print("\n===== TRADE PERFORMANCE BY SETUP/SIDE =====")
if table_exists(con,"trades"):
    rows = q(con, """
    SELECT symbol, side, setup,
           COUNT(*) n,
           ROUND(SUM(pnl_usd),6) pnl,
           ROUND(AVG(pnl_usd),6) avg_pnl,
           SUM(CASE WHEN pnl_usd>0 THEN 1 ELSE 0 END) wins,
           SUM(CASE WHEN pnl_usd<0 THEN 1 ELSE 0 END) losses
    FROM trades
    GROUP BY symbol, side, setup
    ORDER BY n DESC, pnl ASC
    LIMIT 30
    """)
    for r in rows:
        n=f(r.get("n"))
        wr=f(r.get("wins"))/max(1,n)*100
        print(f"{r.get('symbol')} {r.get('side')} {r.get('setup')} | n={int(n)} pnl={r.get('pnl')} avg={r.get('avg_pnl')} wr={wr:.1f}%")

print("\n===== EDGE MEMORY TOP/BOTTOM =====")
if table_exists(con,"edge_memory"):
    rows = q(con, """
    SELECT key, source, n, wins, losses, sum_r, sum_pos_r, sum_neg_r,
           CASE WHEN ABS(sum_neg_r)>0 THEN sum_pos_r/ABS(sum_neg_r) ELSE NULL END pf,
           CASE WHEN n>0 THEN sum_r/n ELSE NULL END exp_r
    FROM edge_memory
    ORDER BY exp_r ASC
    LIMIT 15
    """)
    print("-- WORST --")
    for r in rows:
        print(f"{r.get('source')} n={r.get('n')} expR={r.get('exp_r')} pf={r.get('pf')} key={r.get('key')}")
    rows = q(con, """
    SELECT key, source, n, wins, losses, sum_r, sum_pos_r, sum_neg_r,
           CASE WHEN ABS(sum_neg_r)>0 THEN sum_pos_r/ABS(sum_neg_r) ELSE NULL END pf,
           CASE WHEN n>0 THEN sum_r/n ELSE NULL END exp_r
    FROM edge_memory
    ORDER BY exp_r DESC
    LIMIT 15
    """)
    print("-- BEST --")
    for r in rows:
        print(f"{r.get('source')} n={r.get('n')} expR={r.get('exp_r')} pf={r.get('pf')} key={r.get('key')}")

print("\n===== FORWARD RESULTS =====")
if table_exists(con,"forward_results"):
    rows = q(con, """
    SELECT symbol, outcome,
           COUNT(*) n,
           ROUND(AVG(result_r),6) avg_r,
           SUM(CASE WHEN result_r>0 THEN 1 ELSE 0 END) wins,
           SUM(CASE WHEN result_r<0 THEN 1 ELSE 0 END) losses
    FROM forward_results
    GROUP BY symbol, outcome
    ORDER BY n DESC
    LIMIT 30
    """)
    for r in rows:
        n=f(r.get("n"))
        wr=f(r.get("wins"))/max(1,n)*100
        print(f"{r.get('symbol')} {r.get('outcome')} | n={int(n)} avgR={r.get('avg_r')} wr={wr:.1f}%")

print("\n===== DECISIONS ACTION MIX =====")
if table_exists(con,"decisions"):
    rows = q(con, """
    SELECT action, side, setup,
           COUNT(*) n,
           ROUND(AVG(final_score),4) avg_score,
           ROUND(AVG(size_usd),4) avg_size
    FROM decisions
    GROUP BY action, side, setup
    ORDER BY n DESC
    LIMIT 40
    """)
    for r in rows:
        print(f"{r.get('action')} {r.get('side')} {r.get('setup')} | n={r.get('n')} avg_score={r.get('avg_score')} avg_size={r.get('avg_size')}")

print("\n===== LATEST EDGE/AUTHORITY REASONS FROM DECISIONS =====")
if table_exists(con,"decisions"):
    rows = q(con, """
    SELECT ts, symbol, action, side, setup, final_score, payload
    FROM decisions
    ORDER BY id DESC
    LIMIT 20
    """)
    for r in rows:
        try:
            p=json.loads(r.get("payload") or "{}")
            reasons=p.get("reasons", [])[:8]
            edge=p.get("edge", {})
            print(f"{r.get('ts')} {r.get('symbol')} {r.get('action')} {r.get('side')} {r.get('setup')} score={r.get('final_score')}")
            print(" edge_status:", edge.get("status"), "n:", edge.get("effective_n"), "expR:", edge.get("expectancy_r"), "pf:", edge.get("profit_factor"))
            print(" reasons:", reasons)
        except Exception as e:
            print("PAYLOAD_ERROR", repr(e))

print("\n===== RECENT ALPHA/RUNTIME EVENTS =====")
if table_exists(con,"runtime_events"):
    rows = q(con, """
    SELECT ts, component, level, message
    FROM runtime_events
    WHERE component LIKE '%alpha%' OR component LIKE '%edge%' OR message LIKE '%alpha%' OR message LIKE '%edge%'
    ORDER BY id DESC
    LIMIT 30
    """)
    for r in rows:
        print(f"{r.get('ts')} | {r.get('component')} | {r.get('level')} | {r.get('message')}")

