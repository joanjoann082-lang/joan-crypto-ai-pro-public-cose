#!/data/data/com.termux/files/usr/bin/bash
cd /storage/emulated/0/Download/joan_crypto_ai_pro_v14 || exit 1
export PYTHONPATH=$PWD
mkdir -p data/v24_4_accounting_core

start_if_missing() {
  NAME="$1"
  PATTERN="$2"
  SCRIPT="$3"
  OUT="$4"
  ERR="$5"
  COUNT="$(pgrep -f "$PATTERN" | wc -l | tr -d ' ')"
  if [ "$COUNT" = "0" ]; then
    if [ -f "$SCRIPT" ]; then
      mkdir -p "$(dirname "$OUT")" "$(dirname "$ERR")"
      nohup bash "$SCRIPT" >> "$OUT" 2>> "$ERR" &
      echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) STARTED $NAME" >> data/v24_4_accounting_core/stack.log
    else
      echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) MISSING $NAME $SCRIPT" >> data/v24_4_accounting_core/stack.log
    fi
  fi
}

while true; do
  pkill -f v23_3_canary_promotion_bridge.py 2>/dev/null || true
  pkill -f v23_4_quant_execution_authority.py 2>/dev/null || true
  pkill -f run_v23_4_quant_execution_authority_forever.sh 2>/dev/null || true
  pkill -f institutional_paper_canary_adapter_v17_8_1.py 2>/dev/null || true
  pkill -f run_v17_8_1_paper_canary_adapter_forever.sh 2>/dev/null || true
  pkill -f run_v24_0_quant_stack_forever.sh 2>/dev/null || true
  pkill -f v22_1_runtime_manager.py 2>/dev/null || true

  start_if_missing "DATA_PLANE" "run_v18_9_1_data_plane_forever|v18_9_1_semantic_data_plane" "scripts/run_v18_9_1_data_plane_forever.sh" "data/v18_9_1_data_plane/v24_4_stdout.log" "data/v18_9_1_data_plane/v24_4_stderr.log"
  start_if_missing "LIQUIDATION" "run_v18_10_liquidation_collector_forever|v18_10_liquidation_collector" "scripts/run_v18_10_liquidation_collector_forever.sh" "data/v18_10_liquidation/v24_4_stdout.log" "data/v18_10_liquidation/v24_4_stderr.log"
  start_if_missing "BRAIN" "run_v17_5_1_quant_brain_forever|run_quant_brain_v17_5_1" "scripts/run_v17_5_1_quant_brain_forever.sh" "data/v17_5_1/v24_4_stdout.log" "data/v17_5_1/v24_4_stderr.log"
  start_if_missing "PRICE_CONTRACT" "v24_1_market_price_contract.py --daemon" "scripts/run_v24_1_market_price_contract_forever.sh" "data/v24_1_market_price_contract/v24_4_stdout.log" "data/v24_1_market_price_contract/v24_4_stderr.log"
  start_if_missing "V24_AUTHORITY" "v24_0_quant_production_authority.py --daemon" "scripts/run_v24_0_quant_production_authority_forever.sh" "data/v24_0_quant_authority/v24_4_stdout.log" "data/v24_0_quant_authority/v24_4_stderr.log"
  start_if_missing "V24_4_CANONICAL_ADAPTER" "v24_4_canonical_paper_adapter.py --daemon" "scripts/run_v24_4_canonical_paper_adapter_forever.sh" "data/v24_4_accounting_core/adapter_stdout.log" "data/v24_4_accounting_core/adapter_stderr.log"

  {
    echo "===== V24.4 STACK $(date -u +%Y-%m-%dT%H:%M:%SZ) ====="
    ps -ef | grep -Ei "data_plane|liquidation|quant_brain|market_price_contract|v24_0_quant|v24_4_canonical|paper_canary_adapter_v17_8_1|v23_3|v23_4" | grep -v grep || true
  } > data/v24_4_accounting_core/status.txt

  sleep 60
done
