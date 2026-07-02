#!/data/data/com.termux/files/usr/bin/bash

cd "$(dirname "$0")/.."
DB="data/joanbot_v14.sqlite"

echo "===== PROCESS ====="
ps -ef | grep -Ei "python.*joanbot.runtime|python.*joanbot.runner|python.*joanbot.orchestrator" | grep -v grep || echo "NO_PROCESS_ACTIVE"

echo
echo "===== CONTROL V11 CURRENT BLOCK ====="
sqlite3 -line "$DB" "
SELECT
  version,
  global_state,
  decision_tier,
  recommended_action,
  allow_paper_micro_canary,
  max_size_usd,
  edge_symbol,
  edge_side,
  edge_setup,
  edge_profile,
  edge_n,
  edge_lcb,
  r20,
  r50_lcb,
  robust_score,
  shadow_regime_state,
  derivatives_state,
  feedback_state,
  kpi_state,
  overlap_state,
  hard_vetoes,
  reasons
FROM latest_institutional_control_plane_v11;
" 2>/dev/null || echo "NO_CONTROL_V11"

echo
echo "===== OVERLAP GUARD V11 ====="
sqlite3 -line "$DB" "
SELECT *
FROM latest_overlap_guard_v11;
" 2>/dev/null || echo "NO_OVERLAP_GUARD"

echo
echo "===== OPEN POSITIONS / OPEN CANARIES ====="
python - <<'PY'
import sqlite3
DB="data/joanbot_v14.sqlite"
con=sqlite3.connect(DB)
con.row_factory=sqlite3.Row
cur=con.cursor()

def exists(name):
    return cur.execute("SELECT COUNT(*) FROM sqlite_master WHERE name=?", (name,)).fetchone()[0] > 0

def show(table, where="", limit=20):
    if not exists(table):
        print(f"\n{table}: NOT_FOUND")
        return
    try:
        sql=f"SELECT * FROM {table} {where} LIMIT {limit}"
        rows=cur.execute(sql).fetchall()
        print(f"\n--- {table} {where} ---")
        if not rows:
            print("EMPTY")
            return
        for r in rows:
            d=dict(r)
            small={}
            for k,v in d.items():
                if k.lower() in {
                    "id","ts","opened_at","closed_at","symbol","side","setup","profile",
                    "status","entry_price","exit_price","size_usd","pnl_usd","pnl_r",
                    "net_pnl_r","gross_pnl_r","reason","state"
                }:
                    small[k]=v
            print(small if small else d)
    except Exception as e:
        print(f"{table}: ERROR {e}")

show("paper_micro_canary_positions_v11", "WHERE status='OPEN' OR closed_at IS NULL", 20)
show("paper_micro_canary_positions_v10", "WHERE status='OPEN' OR closed_at IS NULL", 20)
show("paper_micro_canary_positions_v9", "WHERE status='OPEN' OR closed_at IS NULL", 20)
show("positions", "WHERE status='OPEN' OR closed_at IS NULL", 20)
show("paper_trades", "WHERE status='OPEN' OR closed_at IS NULL", 20)

con.close()
PY

echo
echo "===== V11 MICRO CANARY TRADES ====="
sqlite3 -header -column "$DB" "
SELECT
  id,
  opened_at,
  closed_at,
  symbol,
  side,
  setup,
  profile,
  horizon_min,
  status,
  ROUND(entry_price,2) AS entry,
  ROUND(exit_price,2) AS exit,
  ROUND(size_usd,2) AS size,
  ROUND(gross_pnl_r,4) AS gross_r,
  ROUND(net_pnl_r,4) AS net_r,
  ROUND(pnl_usd,4) AS pnl_usd,
  reason
FROM paper_micro_canary_positions_v11
ORDER BY id DESC
LIMIT 30;
" 2>/dev/null || echo "NO_V11_CANARY_TABLE"

echo
echo "===== V11 PROFIT SUMMARY ====="
sqlite3 -header -column "$DB" "
SELECT
  COUNT(*) AS total,
  SUM(CASE WHEN status='OPEN' THEN 1 ELSE 0 END) AS open_n,
  SUM(CASE WHEN status='CLOSED' THEN 1 ELSE 0 END) AS closed_n,
  ROUND(SUM(CASE WHEN net_pnl_r > 0 THEN net_pnl_r ELSE 0 END),4) AS gross_profit_r,
  ROUND(ABS(SUM(CASE WHEN net_pnl_r < 0 THEN net_pnl_r ELSE 0 END)),4) AS gross_loss_r,
  ROUND(SUM(net_pnl_r),4) AS total_net_r,
  ROUND(AVG(CASE WHEN status='CLOSED' THEN net_pnl_r END),4) AS expectancy_r,
  ROUND(100.0 * SUM(CASE WHEN net_pnl_r > 0 THEN 1 ELSE 0 END) / NULLIF(SUM(CASE WHEN status='CLOSED' THEN 1 ELSE 0 END),0),2) AS winrate_pct
FROM paper_micro_canary_positions_v11;
" 2>/dev/null || echo "NO_V11_PROFIT"

echo
echo "===== V9 MICRO CANARY TRADES ====="
sqlite3 -header -column "$DB" "
SELECT
  id,
  opened_at,
  closed_at,
  symbol,
  side,
  setup,
  profile,
  horizon_min,
  status,
  ROUND(entry_price,2) AS entry,
  ROUND(exit_price,2) AS exit,
  ROUND(size_usd,2) AS size,
  ROUND(pnl_r,4) AS pnl_r,
  ROUND(pnl_usd,4) AS pnl_usd,
  reason
FROM paper_micro_canary_positions_v9
ORDER BY id DESC
LIMIT 30;
" 2>/dev/null || echo "NO_V9_CANARY_TABLE"

echo
echo "===== LEGACY TRADES TABLE ====="
sqlite3 -header -column "$DB" "
SELECT *
FROM trades
ORDER BY rowid DESC
LIMIT 20;
" 2>/dev/null || echo "NO_TRADES_TABLE"

echo
echo "===== LEGACY POSITIONS TABLE ====="
sqlite3 -header -column "$DB" "
SELECT *
FROM positions
ORDER BY rowid DESC
LIMIT 20;
" 2>/dev/null || echo "NO_POSITIONS_TABLE"

echo
echo "===== ALL TRADE/CANARY TABLES ====="
sqlite3 -header -column "$DB" "
SELECT type, name
FROM sqlite_master
WHERE name LIKE '%trade%'
   OR name LIKE '%position%'
   OR name LIKE '%canary%'
   OR name LIKE '%pnl%'
ORDER BY name;
"

echo
echo "===== PAID API READINESS ====="
sqlite3 -line "$DB" "
SELECT *
FROM latest_paid_api_readiness_gate_v11;
" 2>/dev/null || echo "NO_API_READINESS"

echo
echo "===== DB CHECK ====="
sqlite3 "$DB" "PRAGMA quick_check;"
