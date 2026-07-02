#!/data/data/com.termux/files/usr/bin/bash
cd /storage/emulated/0/Download/joan_crypto_ai_pro_v14 || exit 1
export PYTHONPATH=$PWD
mkdir -p data/v24_0_runtime

start_if_missing() {
  NAME="$1"
  PATTERN="$2"
  SCRIPT="$3"
  STDOUT="$4"
  STDERR="$5"

  COUNT="$(pgrep -f "$PATTERN" | wc -l | tr -d ' ')"
  if [ "$COUNT" = "0" ]; then
    if [ -f "$SCRIPT" ]; then
      mkdir -p "$(dirname "$STDOUT")" "$(dirname "$STDERR")"
      nohup bash "$SCRIPT" >> "$STDOUT" 2>> "$STDERR" &
      echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) STARTED $NAME" >> data/v24_0_runtime/supervisor.log
    else
      echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) MISSING_SCRIPT $NAME $SCRIPT" >> data/v24_0_runtime/supervisor.log
    fi
  fi
}

while true; do
  pkill -f "v23_3_canary_promotion_bridge.py" 2>/dev/null || true
  pkill -f "v23_4_quant_execution_authority.py" 2>/dev/null || true
  pkill -f "run_v23_4_quant_execution_authority_forever.sh" 2>/dev/null || true
  pkill -f "run_max_quant_canary_governance_v17_7_2.py" 2>/dev/null || true
  pkill -f "run_promotion_controller_v17_6_1.py" 2>/dev/null || true
  pkill -f "run_v17_6_1_promotion_controller_forever.sh" 2>/dev/null || true
  pkill -f "v22_1_runtime_manager.py" 2>/dev/null || true

  start_if_missing "DATA_PLANE" "run_v18_9_1_data_plane_forever|v18_9_1_semantic_data_plane" "scripts/run_v18_9_1_data_plane_forever.sh" "data/v18_9_1_data_plane/v24_stdout.log" "data/v18_9_1_data_plane/v24_stderr.log"
  start_if_missing "LIQUIDATION_COLLECTOR" "run_v18_10_liquidation_collector_forever|v18_10_liquidation_collector" "scripts/run_v18_10_liquidation_collector_forever.sh" "data/v18_10_liquidation/v24_stdout.log" "data/v18_10_liquidation/v24_stderr.log"
  start_if_missing "QUANT_BRAIN" "run_v17_5_1_quant_brain_forever|run_quant_brain_v17_5_1" "scripts/run_v17_5_1_quant_brain_forever.sh" "data/v17_5_1/v24_stdout.log" "data/v17_5_1/v24_stderr.log"
  start_if_missing "PAPER_ADAPTER" "run_v17_8_1_paper_canary_adapter_forever|institutional_paper_canary_adapter_v17_8_1" "scripts/run_v17_8_1_paper_canary_adapter_forever.sh" "data/v17_8_1/v24_stdout.log" "data/v17_8_1/v24_stderr.log"
  start_if_missing "MARKET_CONTEXT" "run_v18_2_market_context_forever|v18_2_market_context" "scripts/run_v18_2_market_context_forever.sh" "data/v18_2_market_context/v24_stdout.log" "data/v18_2_market_context/v24_stderr.log"
  start_if_missing "V24_AUTHORITY" "v24_0_quant_production_authority.py --daemon" "scripts/run_v24_0_quant_production_authority_forever.sh" "data/v24_0_quant_authority/service_stdout.log" "data/v24_0_quant_authority/service_stderr.log"

  {
    echo "===== V24 STACK $(date -u +%Y-%m-%dT%H:%M:%SZ) ====="
    ps -ef | grep -Ei "data_plane|liquidation|quant_brain|paper_canary_adapter|market_context|v24_0_quant" | grep -v grep || true
  } > data/v24_0_runtime/status.txt

  sleep 60
done
