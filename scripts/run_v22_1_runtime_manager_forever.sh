#!/data/data/com.termux/files/usr/bin/bash
set -u

cd /storage/emulated/0/Download/joan_crypto_ai_pro_v14 || exit 1
export PYTHONPATH=$PWD
mkdir -p data/v22_1_runtime_manager

command -v termux-wake-lock >/dev/null 2>&1 && termux-wake-lock || true

python tools/v22_1_runtime_manager.py --daemon --interval 60 \
  >> data/v22_1_runtime_manager/runtime_manager_stdout.log \
  2>> data/v22_1_runtime_manager/runtime_manager_stderr.log
