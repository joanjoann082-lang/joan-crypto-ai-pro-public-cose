#!/data/data/com.termux/files/usr/bin/bash
cd /storage/emulated/0/Download/joan_crypto_ai_pro_v14 || exit 1
export PYTHONPATH=$PWD
mkdir -p data/v24_0_quant_authority
python tools/v24_0_quant_production_authority.py --daemon --interval 90
