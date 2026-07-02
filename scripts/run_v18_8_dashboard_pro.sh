#!/data/data/com.termux/files/usr/bin/bash
set -u
cd /storage/emulated/0/Download/joan_crypto_ai_pro_v14 || exit 1
export PYTHONPATH=$PWD
python tools/v18_8_quant_dashboard_pro.py
