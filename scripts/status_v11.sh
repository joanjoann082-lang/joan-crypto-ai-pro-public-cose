#!/data/data/com.termux/files/usr/bin/bash
cd "$(dirname "$0")/.."
export PYTHONPATH=$PWD

echo "===== PROCESS ====="
ps -ef | grep -Ei "python.*joanbot.runtime|python.*joanbot.runner|python.*joanbot.orchestrator" | grep -v grep || echo "NO_PROCESS_ACTIVE"

echo

echo "===== ERRORS V11 ====="
tail -120 data/institutional_runtime_v11_errors.log 2>/dev/null || echo "NO_V11_ERROR_LOG"

echo

echo "===== DERIVATIVES REGIME V10.2 ====="
sqlite3 -header -column data/joanbot_v14.sqlite "
SELECT
  version,
  symbol,
  selected_side,
  data_state,
  ROUND(data_quality,2) AS data_q,
  derivatives_state,
  allow_v10_canary,
  reduce_size,
  veto_canary,
  ROUND(selected_score,2) AS selected,
  ROUND(opposite_score,2) AS opposite,
  ROUND(directional_delta,2) AS delta,
  ROUND(contradiction_index,2) AS contradiction,
  ROUND(confidence_score,2) AS confidence,
  ROUND(regime_quality_score,2) AS regime_q,
  hard_vetoes,
  reasons
FROM latest_derivatives_regime_v10;
" 2>/dev/null || echo "NO_DERIVATIVES_REGIME_YET"

echo

echo "===== CONTROL V11.2 ====="
sqlite3 -header -column data/joanbot_v14.sqlite "
SELECT
  version,
  global_state,
  decision_tier,
  ROUND(control_score,2) AS control_score,
  ROUND(confidence_score,2) AS confidence,
  recommended_action,
  allow_paper_micro_canary,
  max_size_usd,
  edge_symbol,
  edge_side,
  edge_setup,
  edge_profile,
  edge_n,
  ROUND(edge_lcb_r,4) AS edge_lcb,
  ROUND(edge_recent20_avg_r,4) AS r20,
  ROUND(edge_recent50_lcb_r,4) AS r50_lcb,
  ROUND(robustness_score,2) AS robust,
  shadow_regime_state,
  derivatives_state,
  derivatives_data_quality,
  feedback_state,
  kpi_state,
  overlap_state,
  hard_vetoes,
  reasons
FROM latest_institutional_control_plane_v11;
" 2>/dev/null || echo "NO_CONTROL_V11_YET"

echo

echo "===== ORDER V11 ====="
sqlite3 -header -column data/joanbot_v14.sqlite "
SELECT phase, flow_hash, selected_symbol, selected_side, selected_setup,
       ordered_stage_count, missing_stage_count, hard_vetoes
FROM latest_institutional_decision_order_v11;
" 2>/dev/null || echo "NO_ORDER_V11_YET"

echo

echo "===== MICRO CANARY V11 ====="
sqlite3 -header -column data/joanbot_v14.sqlite "
SELECT id, opened_at, closed_at, symbol, side, setup, profile, horizon_min, status,
       ROUND(entry_price,2) AS entry, ROUND(exit_price,2) AS exit,
       ROUND(size_usd,2) AS size, ROUND(net_pnl_usd,4) AS net_usd,
       ROUND(net_pnl_r,4) AS net_r, reason
FROM paper_micro_canary_positions_v11
ORDER BY id DESC LIMIT 10;
" 2>/dev/null || echo "NO_CANARY_V11_TABLE_YET"

echo

echo "===== ABLATION V12 ====="
sqlite3 -header -column data/joanbot_v14.sqlite "
SELECT scenario, eligible_control_n, opened_n, closed_n,
       ROUND(profit_factor,3) AS pf,
       ROUND(expectancy_r,4) AS exp_r,
       ROUND(max_drawdown_r,4) AS max_dd,
       ablation_state,
       hard_vetoes
FROM latest_institutional_ablation_v12
ORDER BY scenario_rank;
" 2>/dev/null || echo "NO_ABLATION_V12_YET"

echo

echo "===== PAID API READINESS V11.2 ====="
sqlite3 -header -column data/joanbot_v14.sqlite "
SELECT readiness_state, paid_api_allowed, closed_canaries,
       ROUND(profit_factor,3) AS pf, ROUND(expectancy_r,4) AS exp_r,
       ROUND(max_drawdown_r,4) AS max_dd, derivatives_ready_symbols,
       derivatives_total_symbols, critical_errors_24h,
       ablation_state, ablation_closed_n, ROUND(ablation_expectancy_r,4) AS abl_exp,
       ROUND(ablation_profit_factor,3) AS abl_pf,
       hard_vetoes, required_before_paid_api
FROM latest_paid_api_readiness_gate_v11;
" 2>/dev/null || echo "NO_READINESS_V11_YET"
