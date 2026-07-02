#!/data/data/com.termux/files/usr/bin/bash
set -u

cd /storage/emulated/0/Download/joan_crypto_ai_pro_v14 || exit 1
export PYTHONPATH=$PWD
mkdir -p data/v17_8_1

while true; do
  python tools/run_institutional_paper_canary_adapter_v17_8_1.py \
    >> data/v17_8_1/adapter_stdout.log \
    2>> data/v17_8_1/adapter_stderr.log

  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) V17_8_1_ADAPTER_CYCLE_DONE rc=$?" \
    >> data/v17_8_1/adapter_stdout.log

  sleep 60
done
