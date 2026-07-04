from pathlib import Path
import sys, sqlite3, tempfile, json

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from joanbot.institutional.alpha_family_research_core_v6_1 import run

with tempfile.TemporaryDirectory() as td:
    db = Path(td) / "test.sqlite"
    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row

    con.execute("""
    CREATE TABLE resultats_quant_nets(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT, font TEXT, font_id TEXT, position_id TEXT, decision_id INTEGER,
        symbol TEXT, side TEXT, setup TEXT, action TEXT,
        entry_ts TEXT, exit_ts TEXT,
        entry_price REAL, exit_price REAL, size_usd REAL, pnl_usd REAL, fees REAL, risk_usd REAL,
        resultat_r REAL, mfe_r REAL, mae_r REAL,
        regime TEXT, session TEXT, volatility_bucket TEXT, news_bucket TEXT,
        context_key TEXT, qualitat TEXT, estat_promocio TEXT, motiu TEXT, payload TEXT
    )
    """)

    con.execute("""
    CREATE TABLE trades(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT, position_id TEXT, symbol TEXT, side TEXT, setup TEXT,
        pnl_usd REAL, pnl_r REAL, fees REAL, reason TEXT, payload TEXT
    )
    """)

    con.execute("""
    CREATE TABLE universal_shadow_results_v2(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        case_id TEXT, resolved_at TEXT, symbol TEXT, side TEXT, setup TEXT, profile TEXT,
        horizon_min INTEGER, outcome TEXT, result_r REAL, mfe_r REAL, mae_r REAL,
        bars_seen INTEGER, exit_price REAL, payload TEXT
    )
    """)

    con.execute("""
    CREATE TABLE universal_shadow_registry_v2(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT, version TEXT, alpha_key TEXT, symbol TEXT, side TEXT, setup TEXT, profile TEXT,
        horizon_min INTEGER, context_bucket TEXT, n INTEGER, expectancy_r REAL, winrate REAL,
        profit_factor REAL, avg_mfe_r REAL, avg_mae_r REAL, train_exp_r REAL,
        validation_exp_r REAL, stability_score REAL, quality_score REAL, state TEXT,
        recommendation TEXT, reasons TEXT, payload TEXT
    )
    """)

    con.execute("""
    CREATE TABLE research_promotion_decisions_v1(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT, version TEXT, symbol TEXT, side TEXT, setup TEXT, source_status TEXT,
        forward_n INTEGER, forward_exp_r REAL, forward_pf REAL, shrunk_exp_r REAL,
        quality_score REAL, allow_canary_probe INTEGER, allow_direct_open INTEGER,
        size_multiplier_cap REAL, absolute_size_usd_cap REAL, promotion_state TEXT,
        reasons TEXT, payload TEXT
    )
    """)

    for i in range(90):
        r = 0.08 if i % 5 else -0.03
        con.execute("""
        INSERT INTO resultats_quant_nets(
            ts,font,symbol,side,setup,action,entry_ts,exit_ts,entry_price,exit_price,size_usd,pnl_usd,fees,risk_usd,
            resultat_r,mfe_r,mae_r,regime,session,volatility_bucket,news_bucket,context_key,qualitat,estat_promocio,motiu,payload
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            "2026-01-01T00:00:00+00:00","FORWARD","ETHUSDT","LONG","TREND_PULLBACK_LONG","FORWARD_RESULT",
            "2026-01-01T00:00:00+00:00","2026-01-01T02:00:00+00:00",100,101,0,0,0,1,
            r,0.35,-0.12,"TRENDING_BULL","EUROPE","NORMAL","LOW",
            "CTX","NET","RESEARCH","TIME","{}"
        ))

    for i in range(60):
        r = -0.08 if i % 4 else 0.02
        con.execute("""
        INSERT INTO resultats_quant_nets(
            ts,font,symbol,side,setup,action,entry_ts,exit_ts,entry_price,exit_price,size_usd,pnl_usd,fees,risk_usd,
            resultat_r,mfe_r,mae_r,regime,session,volatility_bucket,news_bucket,context_key,qualitat,estat_promocio,motiu,payload
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            "2026-01-01T00:00:00+00:00","FORWARD","BTCUSDT","SHORT","TREND_BOUNCE_SHORT","FORWARD_RESULT",
            "2026-01-01T00:00:00+00:00","2026-01-01T02:00:00+00:00",100,101,0,0,0,1,
            r,0.04,-0.40,"TRENDING_BULL","US","NORMAL","LOW",
            "CTX","NET","RESEARCH","TIME","{}"
        ))

    for i in range(120):
        con.execute("""
        INSERT INTO universal_shadow_results_v2(
            case_id,resolved_at,symbol,side,setup,profile,horizon_min,outcome,result_r,mfe_r,mae_r,bars_seen,exit_price,payload
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, ("c","2026-01-01","ETHUSDT","LONG","TREND_PULLBACK_LONG","P",120,"WIN",0.05,0.25,-0.08,10,101,"{}"))

    con.execute("""
    INSERT INTO universal_shadow_registry_v2(
        symbol,side,setup,horizon_min,n,expectancy_r,profit_factor,quality_score,state,recommendation,payload
    ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
    """, ("ETHUSDT","LONG","TREND_PULLBACK_LONG",120,500,0.06,1.7,80,"VALIDABLE","PROMOTE","{}"))

    con.execute("""
    INSERT INTO research_promotion_decisions_v1(
        symbol,side,setup,quality_score,allow_canary_probe,allow_direct_open,promotion_state,reasons,payload
    ) VALUES(?,?,?,?,?,?,?,?,?)
    """, ("ETHUSDT","LONG","TREND_PULLBACK_LONG",75,1,0,"VALIDABLE","[]","{}"))

    con.commit()

    summary = run(db)

    con2 = sqlite3.connect(str(db))
    con2.row_factory = sqlite3.Row
    tables = [r["name"] for r in con2.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    fam_n = con2.execute("SELECT COUNT(*) c FROM alpha_family_clusters_v6_1").fetchone()["c"]
    contracts_n = con2.execute("SELECT COUNT(*) c FROM alpha_family_promotion_contracts_v6_1").fetchone()["c"]
    canary_n = con2.execute("SELECT COUNT(*) c FROM alpha_family_promotion_contracts_v6_1 WHERE allow_micro_canary=1").fetchone()["c"]
    toxic_n = con2.execute("SELECT COUNT(*) c FROM alpha_family_clusters_v6_1 WHERE cluster_state='FAMILY_TOXIC'").fetchone()["c"]

    checks = {
        "summary_ok": summary["obs_n"] >= 250,
        "families_created": fam_n >= 2,
        "contracts_created": contracts_n >= 2,
        "detects_canary_candidate": canary_n >= 1,
        "detects_toxic": toxic_n >= 1,
        "children_table_created": "alpha_family_children_v6_1" in tables,
        "audit_table_created": "alpha_family_research_audit_v6_1" in tables,
        "no_runtime_tables": "execution_control_policy_v6" not in tables and "execution_control_audit_v6" not in tables,
    }

print("VALIDACIO_ALPHA_FAMILY_RESEARCH_CORE_V6_1")
print(json.dumps(checks, indent=2, ensure_ascii=False, sort_keys=True))

if not all(checks.values()):
    raise SystemExit(1)

print("VALIDACIO_ALPHA_FAMILY_RESEARCH_CORE_V6_1_OK")
