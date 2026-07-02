#!/data/data/com.termux/files/usr/bin/bash
cd "$(dirname "$0")/.."
DB="data/joanbot_v14.sqlite"

echo "===== FINAL GATE V16 ====="
sqlite3 -line "$DB" "
SELECT
  ts, version, gate_state, policy, allow_trade, size_mult,
  selected_symbol, selected_side, selected_setup, selected_profile,
  selected_horizon_min, hard_vetoes
FROM latest_alpha_final_gate_v16;
" 2>/dev/null || echo "NO_FINAL_GATE_V16"

echo
echo "===== TOP RESEARCH V16 ====="
sqlite3 -header -column "$DB" "
SELECT
  symbol, side, setup, profile, horizon_min,
  research_state,
  sample_n AS n,
  live_n,
  ROUND(expectancy_r,4) AS exp,
  ROUND(profit_factor,2) AS pf,
  ROUND(raw_lcb_r,4) AS raw_lcb,
  ROUND(posterior_lcb_r,4) AS post_lcb,
  ROUND(cpcv_score,2) AS cpcv,
  ROUND(feature_score,2) AS feature,
  ROUND(attribution_score,2) AS attr,
  ROUND(risk_score,2) AS risk,
  ROUND(final_score,2) AS final,
  ROUND(recommended_size_mult,2) AS size,
  hard_vetoes
FROM alpha_research_v16
ORDER BY id DESC
LIMIT 25;
" 2>/dev/null || echo "NO_RESEARCH_V16"

echo
echo "===== REGISTRY V16 ====="
sqlite3 -header -column "$DB" "
SELECT
  symbol, side, setup, profile, horizon_min,
  lifecycle_state, confidence_state,
  sample_n AS n, live_n,
  ROUND(expectancy_r,4) AS exp,
  ROUND(profit_factor,2) AS pf,
  ROUND(posterior_lcb_r,4) AS post_lcb,
  ROUND(final_score,2) AS final,
  ROUND(size_mult,2) AS size,
  hard_vetoes
FROM alpha_setup_registry_v16
ORDER BY id DESC
LIMIT 20;
" 2>/dev/null || echo "NO_REGISTRY_V16"

echo
echo "===== FEATURE STORE V16 ====="
sqlite3 -header -column "$DB" "
SELECT
  symbol, side, feature_quality,
  ROUND(mark_price,2) AS mark,
  funding_rate,
  ROUND(open_interest,2) AS oi,
  ROUND(spread_bps,3) AS spread,
  ROUND(imbalance_20,4) AS imb20,
  ROUND(liquidation_15m_usd,2) AS liq15,
  ROUND(liquidation_signed_15m_usd,2) AS signed15,
  liquidation_pressure,
  derivatives_state,
  ROUND(derivatives_confidence,2) AS der_conf,
  derivatives_side_conflict,
  etf_flow_state,
  options_flow_state
FROM alpha_feature_store_v16
ORDER BY id DESC
LIMIT 12;
" 2>/dev/null || echo "NO_FEATURE_STORE_V16"

echo
echo "===== ATTRIBUTION V16 ====="
sqlite3 -header -column "$DB" "
SELECT
  symbol, side, setup, profile, feature_name,
  sample_n,
  ROUND(high_bucket_expectancy,4) AS high_exp,
  ROUND(low_bucket_expectancy,4) AS low_exp,
  ROUND(effect_r,4) AS effect,
  direction,
  sample_quality
FROM alpha_feature_attribution_v16
ORDER BY id DESC
LIMIT 35;
" 2>/dev/null || echo "NO_ATTRIBUTION_V16"

echo
echo "===== CANONICAL R V16 ====="
sqlite3 -header -column "$DB" "
SELECT source_table, source_id, symbol, side, setup,
ROUND(canonical_r,4) AS R, label_quality, reason
FROM canonical_r_labels_v16
ORDER BY id DESC
LIMIT 12;
" 2>/dev/null || echo "NO_R_LABELS_V16"

echo
echo "===== PROCESS ====="
ps -ef | grep -Ei "institutional_runtime_v11|alpha_kernel_v16_overnight|liquidation_stream_v16" | grep -v grep || true

echo
echo "===== DB SIZE ====="
ls -lh data/joanbot_v14.sqlite data/joanbot_v14.sqlite-wal 2>/dev/null || true
