#!/data/data/com.termux/files/usr/bin/bash
set -u

cd /storage/emulated/0/Download/joan_crypto_ai_pro_v14 || exit 1
export PYTHONPATH=$PWD
mkdir -p data/v20_0_quant_brain

while true; do
  python tools/v20_0_institutional_quant_research_brain.py \
    >> data/v20_0_quant_brain/brain_stdout.log \
    2>> data/v20_0_quant_brain/brain_stderr.log

  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) V20_0_QUANT_BRAIN_CYCLE_DONE rc=$?" \
    >> data/v20_0_quant_brain/brain_stdout.log

  sleep 300
done
