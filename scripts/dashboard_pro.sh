#!/data/data/com.termux/files/usr/bin/bash
cd /storage/emulated/0/Download/joan_crypto_ai_pro_v14 || exit 1
export PYTHONPATH=$PWD

if [ -f tools/v20_2_canonical_dashboard.py ]; then
  python tools/v20_2_canonical_dashboard.py
elif [ -f tools/v18_8_dashboard_pro.py ]; then
  python tools/v18_8_dashboard_pro.py
elif [ -f tools/v18_7_quant_lab_dashboard.py ]; then
  python tools/v18_7_quant_lab_dashboard.py
else
  echo "FAIL: no dashboard visual existent trobat"
  exit 1
fi
