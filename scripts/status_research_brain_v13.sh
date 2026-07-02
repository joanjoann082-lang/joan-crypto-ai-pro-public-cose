#!/data/data/com.termux/files/usr/bin/bash
cd "$(dirname "$0")/.."
DB="data/joanbot_v14.sqlite"

echo "===== RESEARCH BRAIN V13 CONTRACT ====="
sqlite3 -line "$DB" "
SELECT
  ts,
  version,
  contract_state,
  contract_score,
  policy,
  size_mult,
  selected_symbol,
  selected_side,
  selected_setup,
  selected_profile,
  selected_horizon_min,
  hard_vetoes
FROM latest_research_brain_contract_v13;
" 2>/dev/null || echo "NO_RESEARCH_BRAIN_CONTRACT"

echo
echo "===== TOP RESEARCH CANDIDATES V13 ====="
sqlite3 -header -column "$DB" "
SELECT
  symbol,
  side,
  setup,
  profile,
  horizon_min,
  research_state,
  ROUND(research_score,2) AS score,
  ROUND(posterior_mean_r,4) AS post_mean,
  ROUND(posterior_lcb_r,4) AS post_lcb,
  ROUND(wf_score,2) AS wf,
  ROUND(stability_score,2) AS stable,
  ROUND(live_score,2) AS live,
  ROUND(derivatives_score,2) AS der,
  ROUND(tail_risk_score,2) AS tail,
  ROUND(risk_budget_score,2) AS risk,
  ROUND(recommended_size_mult,2) AS size,
  hard_vetoes
FROM research_brain_candidates_v13
ORDER BY id DESC
LIMIT 20;
" 2>/dev/null || echo "NO_RESEARCH_CANDIDATES"

echo
echo "===== V11 KPI ====="
sqlite3 -line "$DB" "
SELECT *
FROM latest_micro_canary_kpi_v11;
" 2>/dev/null || echo "NO_KPI"

echo
echo "===== V11 CONTROL ====="
sqlite3 -line "$DB" "
SELECT
  global_state,
  decision_tier,
  control_score,
  confidence_score,
  recommended_action,
  allow_paper_micro_canary,
  edge_symbol,
  edge_side,
  edge_setup,
  edge_profile,
  edge_n,
  edge_lcb,
  r20,
  r50_lcb,
  robust_score,
  derivatives_state,
  kpi_state,
  overlap_state,
  hard_vetoes
FROM latest_institutional_control_plane_v11;
" 2>/dev/null || echo "NO_CONTROL_V11"
