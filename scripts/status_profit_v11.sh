#!/data/data/com.termux/files/usr/bin/bash

cd "$(dirname "$0")/.."
DB="data/joanbot_v14.sqlite"

echo "===== SYSTEM ====="
date
uptime

echo
echo "===== PROCESS ====="
ps -ef | grep -Ei "python.*joanbot.runtime|python.*joanbot.runner|python.*joanbot.orchestrator" | grep -v grep || echo "NO_PROCESS_ACTIVE"

echo
echo "===== DB SIZE ====="
ls -lh data/joanbot_v14.sqlite* 2>/dev/null || true

echo
echo "===== ERRORS V11 ====="
tail -80 data/institutional_runtime_v11_errors.log 2>/dev/null || echo "NO_V11_ERRORS_LOG"

echo
echo "===== CONTROL / KPI / PROFIT ====="
python - <<'PY'
import sqlite3, json, math
from pathlib import Path

DB = "data/joanbot_v14.sqlite"

def connect():
    return sqlite3.connect(DB)

def exists(cur, name):
    return cur.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE name=?",
        (name,)
    ).fetchone()[0] > 0

def cols(cur, table):
    try:
        return [r[1] for r in cur.execute(f"PRAGMA table_info({table})").fetchall()]
    except Exception:
        return []

def qone(cur, table):
    if not exists(cur, table):
        return None
    try:
        return cur.execute(f"SELECT * FROM {table} LIMIT 1").fetchone()
    except Exception:
        return None

def print_latest(cur, view, fields=None):
    if not exists(cur, view):
        print(f"{view}: NOT_FOUND")
        return

    cur.row_factory = sqlite3.Row
    row = cur.execute(f"SELECT * FROM {view} LIMIT 1").fetchone()
    if not row:
        print(f"{view}: EMPTY")
        return

    print(f"\n--- {view} ---")
    d = dict(row)

    if fields:
        for k in fields:
            if k in d:
                print(f"{k}: {d[k]}")
    else:
        for k, v in d.items():
            txt = str(v)
            if len(txt) > 220:
                txt = txt[:220] + "..."
            print(f"{k}: {txt}")

def choose_pnl_col(columns):
    for c in ["net_pnl_r", "pnl_r", "gross_pnl_r", "realized_pnl_r"]:
        if c in columns:
            return c
    return None

def max_drawdown(vals):
    peak = 0.0
    cum = 0.0
    dd = 0.0
    for v in vals:
        cum += v
        peak = max(peak, cum)
        dd = min(dd, cum - peak)
    return dd

con = connect()
con.row_factory = sqlite3.Row
cur = con.cursor()

print_latest(cur, "latest_derivatives_regime_v10", [
    "version", "symbol", "selected_side", "data_state", "data_quality",
    "derivatives_state", "allow_v11_canary", "reduce_size",
    "veto_canary", "selected_score", "opposite_score",
    "delta_score", "confidence_score", "hard_vetoes", "reasons"
])

print_latest(cur, "latest_institutional_control_plane_v11", [
    "version", "global_state", "decision_tier", "control_score",
    "confidence_score", "recommended_action", "allow_paper_micro_canary",
    "max_size_usd", "edge_symbol", "edge_side", "edge_setup",
    "edge_profile", "edge_n", "edge_lcb", "r20", "r50_lcb",
    "robust_score", "shadow_regime_state", "derivatives_state",
    "feedback_state", "kpi_state", "overlap_state",
    "hard_vetoes", "reasons"
])

print_latest(cur, "latest_institutional_decision_order_v11", [
    "version", "phase", "flow_hash", "selected_symbol", "selected_side",
    "selected_setup", "selected_profile", "selected_horizon_min",
    "ordered_stage_count", "missing_stage_count", "hard_vetoes"
])

print("\n--- MICRO CANARY V11 PERFORMANCE ---")
table = "paper_micro_canary_positions_v11"
if not exists(cur, table):
    print("NO_CANARY_TABLE")
else:
    columns = cols(cur, table)
    pnl_col = choose_pnl_col(columns)

    if not pnl_col:
        print("NO_PNL_R_COLUMN_FOUND")
        print("columns:", columns)
    else:
        rows = cur.execute(f"SELECT * FROM {table} ORDER BY id ASC").fetchall()
        total = len(rows)

        closed = []
        open_n = 0

        for r in rows:
            d = dict(r)
            status = str(d.get("status", "")).upper()
            is_closed = status == "CLOSED" or bool(d.get("closed_at"))
            is_open = status == "OPEN" or (not d.get("closed_at") and status != "CLOSED")

            if is_closed:
                try:
                    closed.append(float(d.get(pnl_col) or 0.0))
                except Exception:
                    closed.append(0.0)
            elif is_open:
                open_n += 1

        wins = [x for x in closed if x > 0]
        losses = [x for x in closed if x < 0]

        gross_profit = sum(wins)
        gross_loss = abs(sum(losses))
        pf = gross_profit / gross_loss if gross_loss > 0 else (999.0 if gross_profit > 0 else 0.0)
        exp = sum(closed) / len(closed) if closed else 0.0
        wr = len(wins) / len(closed) * 100 if closed else 0.0
        dd = max_drawdown(closed) if closed else 0.0

        print(f"pnl_column: {pnl_col}")
        print(f"total_canaries: {total}")
        print(f"open_canaries: {open_n}")
        print(f"closed_canaries: {len(closed)}")
        print(f"wins: {len(wins)}")
        print(f"losses: {len(losses)}")
        print(f"winrate_pct: {wr:.2f}")
        print(f"profit_factor: {pf:.3f}")
        print(f"expectancy_r: {exp:.4f}")
        print(f"sum_r: {sum(closed):.4f}")
        print(f"max_drawdown_r: {dd:.4f}")

        if len(closed) < 10:
            verdict = "NO_AVALUABLE_ENCARA_MOSTRA_LT_10"
        elif pf >= 1.15 and exp >= 0.03 and dd > -4.0:
            verdict = "GUANYA_NET_PRELIMINAR"
        elif pf < 1.0 or exp < 0:
            verdict = "NO_GUANYA_O_EDGE_NEGATIU"
        else:
            verdict = "MIXT_ENCARA_NO_CONFIRMAT"

        print(f"VERDICT: {verdict}")

        print("\nlast_10_closed:")
        recent = cur.execute(f"""
            SELECT *
            FROM {table}
            WHERE status='CLOSED' OR closed_at IS NOT NULL
            ORDER BY id DESC
            LIMIT 10
        """).fetchall()

        for r in recent:
            d = dict(r)
            print({
                "id": d.get("id"),
                "symbol": d.get("symbol"),
                "side": d.get("side"),
                "setup": d.get("setup"),
                "status": d.get("status"),
                pnl_col: d.get(pnl_col),
                "reason": d.get("reason"),
                "opened_at": d.get("opened_at"),
                "closed_at": d.get("closed_at"),
            })

print_latest(cur, "latest_micro_canary_kpi_v11")

print_latest(cur, "latest_ablation_engine_v12", [
    "scenario", "eligible_control_n", "opened_n", "closed_n",
    "profit_factor", "expectancy_r", "max_drawdown_r",
    "ablation_state", "hard_vetoes"
])

print_latest(cur, "latest_paid_api_readiness_gate_v11", [
    "readiness_state", "paid_api_allowed", "closed_canaries",
    "profit_factor", "expectancy_r", "max_drawdown_r",
    "derivatives_ready_n", "derivatives_total_n",
    "critical_errors_24h", "ablation_state",
    "ablation_closed_n", "ablation_expectancy_r",
    "ablation_profit_factor", "hard_vetoes", "required_before_paid_api"
])

print("\n--- DB HEALTH ---")
try:
    print("quick_check:", cur.execute("PRAGMA quick_check;").fetchone()[0])
except Exception as e:
    print("quick_check_error:", repr(e))

con.close()
PY

echo
echo "===== TOP TABLE SIZE ====="
sqlite3 -header -column "$DB" "
SELECT
  name,
  ROUND(SUM(pgsize)/1024.0/1024.0,2) AS mb
FROM dbstat
GROUP BY name
ORDER BY SUM(pgsize) DESC
LIMIT 12;
" 2>/dev/null || echo "DBSTAT_NOT_AVAILABLE"
