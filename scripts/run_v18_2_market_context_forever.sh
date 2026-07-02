#!/data/data/com.termux/files/usr/bin/bash
set -u
cd /storage/emulated/0/Download/joan_crypto_ai_pro_v14 || exit 1
export PYTHONPATH=$PWD
mkdir -p data/v18_2_visual

while true; do
  python tools/v18_2_market_context_collector.py \
    >> data/v18_2_visual/market_context_stdout.log \
    2>> data/v18_2_visual/market_context_stderr.log

  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) V18_2_MARKET_CONTEXT_CYCLE_DONE rc=$?" \
    >> data/v18_2_visual/market_context_stdout.log

  sleep 300
done
