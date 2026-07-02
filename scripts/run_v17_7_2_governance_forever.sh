#!/data/data/com.termux/files/usr/bin/bash
cd /storage/emulated/0/Download/joan_crypto_ai_pro_v14 || exit 1
export PYTHONPATH=$PWD
mkdir -p data/v17_7_2_governance

while true; do
  echo "===== GOVERNANCE CYCLE $(date -u +%Y-%m-%dT%H:%M:%SZ) =====" >> data/v17_7_2_governance/stdout.log
  python tools/run_max_quant_canary_governance_v17_7_2.py \
    >> data/v17_7_2_governance/stdout.log \
    2>> data/v17_7_2_governance/stderr.log || true
  sleep 90
done
