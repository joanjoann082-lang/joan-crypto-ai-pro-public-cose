#!/data/data/com.termux/files/usr/bin/bash
cd /storage/emulated/0/Download/joan_crypto_ai_pro_v14 || exit 1
export PYTHONPATH=$PWD
mkdir -p data/v24_1_market_price_contract
python tools/v24_1_market_price_contract.py --daemon --interval 30
