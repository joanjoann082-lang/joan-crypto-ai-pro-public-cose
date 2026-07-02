#!/data/data/com.termux/files/usr/bin/bash
cd "$(dirname "$0")/.."
export PYTHONPATH=$PWD

echo "===== PROCESS ====="
ps -ef | grep -Ei "python.*joanbot.runtime|python.*joanbot.runner|python.*joanbot.orchestrator" | grep -v grep || echo "NO_PROCESS_ACTIVE"

echo "===== CONTROL V10 ====="
sqlite3 -header -column data/joanbot_v14.sqlite "
SELECT
  id,
  ts,
  global_state,
  ROUND(control_score,2) AS control_score,
  recommended_action,
  allow_paper_micro_canary,
  max_size_usd,
  edge_symbol,
  edge_side,
  edge_setup,
  edge_profile,
  edge_horizon_min,
  edge_n,
  ROUND(edge_avg_r,4) AS edge_avg,
  ROUND(edge_lcb_r,4) AS edge_lcb,
  ROUND(robustness_score,2) AS robust_score,
  validation_state,
  shadow_regime_state,
  derivatives_state,
  ROUND(derivatives_score,2) AS deriv_score,
  ROUND(derivatives_data_quality,2) AS deriv_q,
  feedback_state,
  kpi_state,
  hard_vetoes,
  reasons
FROM latest_institutional_control_plane_v10;
" 2>/dev/null || echo "NO_CONTROL_V10_YET"

echo "===== CANARY V10 ====="
sqlite3 -header -column data/joanbot_v14.sqlite "
SELECT
  id, opened_at, closed_at, symbol, side, setup, profile, horizon_min, status,
  ROUND(entry_price,2) AS entry,
  ROUND(exit_price,2) AS exit,
  ROUND(stop_price,2) AS stop,
  ROUND(take_profit_price,2) AS tp,
  ROUND(size_usd,2) AS size,
  ROUND(pnl_r,4) AS gross_r,
  ROUND(net_pnl_r,4) AS net_r,
  ROUND(mfe_r,4) AS mfe,
  ROUND(mae_r,4) AS mae,
  reason
FROM paper_micro_canary_positions_v10
ORDER BY id DESC LIMIT 10;
" 2>/dev/null || echo "NO_CANARY_V10_TABLE_YET"

echo "===== KPI V10 ====="
sqlite3 -header -column data/joanbot_v14.sqlite "
SELECT
  total_n, open_n, closed_n, wins, losses,
  ROUND(winrate,2) AS winrate,
  ROUND(profit_factor,3) AS profit_factor,
  ROUND(expectancy_r,4) AS expectancy_r,
  ROUND(max_drawdown_r,4) AS max_dd_r,
  kpi_state,
  hard_vetoes
FROM latest_micro_canary_kpi_v10;
" 2>/dev/null || echo "NO_KPI_V10_YET"

echo "===== PAID API READINESS ====="
sqlite3 -header -column data/joanbot_v14.sqlite "
SELECT
  readiness_state,
  paid_api_allowed,
  closed_canaries,
  ROUND(profit_factor,3) AS pf,
  ROUND(expectancy_r,4) AS exp_r,
  ROUND(max_drawdown_r,4) AS max_dd,
  derivatives_ready_symbols,
  derivatives_total_symbols,
  critical_errors_24h,
  hard_vetoes,
  required_before_paid_api
FROM latest_paid_api_readiness_gate_v10;
" 2>/dev/null || echo "NO_PAID_API_READINESS_YET"

echo "===== ERRORS ====="
tail -80 data/institutional_runtime_v10_errors.log 2>/dev/null || true
