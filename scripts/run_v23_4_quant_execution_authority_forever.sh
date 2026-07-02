#!/data/data/com.termux/files/usr/bin/bash
cd /storage/emulated/0/Download/joan_crypto_ai_pro_v14 || exit 1
export PYTHONPATH=$PWD
mkdir -p data/v23_4_quant_execution_authority

while true; do
  python tools/v23_4_quant_execution_authority.py --once \
    >> data/v23_4_quant_execution_authority/stdout.log \
    2>> data/v23_4_quant_execution_authority/stderr.log || true
  sleep 90
done
