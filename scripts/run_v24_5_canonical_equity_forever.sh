#!/data/data/com.termux/files/usr/bin/bash
cd /storage/emulated/0/Download/joan_crypto_ai_pro_v14 || exit 1
export PYTHONPATH=$PWD
mkdir -p data/v24_5_canonical_equity
while true; do
  python tools/v24_5_canonical_equity_panel.py >> data/v24_5_canonical_equity/service_stdout.log 2>> data/v24_5_canonical_equity/service_stderr.log || true
  sleep 60
done
