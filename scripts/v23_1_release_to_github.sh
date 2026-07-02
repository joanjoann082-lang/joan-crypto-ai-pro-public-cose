#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail
cd /storage/emulated/0/Download/joan_crypto_ai_pro_v14 || exit 1
export PYTHONPATH=$PWD
python tools/v23_1_release_gate.py release
