#!/data/data/com.termux/files/usr/bin/bash
set -u

cd /storage/emulated/0/Download/joan_crypto_ai_pro_v14 || exit 1
export PYTHONPATH=$PWD
mkdir -p data/v17_5_1

while true; do
  python tools/run_quant_brain_v17_5_1.py --write-db \
    >> data/v17_5_1/quant_brain_stdout.log \
    2>> data/v17_5_1/quant_brain_stderr.log

  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) V17_5_1_QUANT_BRAIN_CYCLE_DONE rc=$?" \
    >> data/v17_5_1/quant_brain_stdout.log

  sleep 300
done
