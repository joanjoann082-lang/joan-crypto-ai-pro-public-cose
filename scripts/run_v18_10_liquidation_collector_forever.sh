#!/data/data/com.termux/files/usr/bin/bash
set -u

cd /storage/emulated/0/Download/joan_crypto_ai_pro_v14 || exit 1
export PYTHONPATH=$PWD
mkdir -p data/v18_10_liquidations

while true; do
  python tools/v18_10_liquidation_collector.py \
    >> data/v18_10_liquidations/collector_stdout.log \
    2>> data/v18_10_liquidations/collector_stderr.log

  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) V18_10_LIQ_COLLECTOR_EXIT rc=$?" \
    >> data/v18_10_liquidations/collector_stdout.log

  sleep 5
done
