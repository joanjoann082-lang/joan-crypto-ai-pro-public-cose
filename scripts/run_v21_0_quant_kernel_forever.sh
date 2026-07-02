#!/data/data/com.termux/files/usr/bin/bash
set -u

cd /storage/emulated/0/Download/joan_crypto_ai_pro_v14 || exit 1
export PYTHONPATH=$PWD
mkdir -p data/v21_0_quant_kernel

while true; do
  python tools/v21_0_institutional_quant_kernel.py --emit \
    >> data/v21_0_quant_kernel/kernel_stdout.log \
    2>> data/v21_0_quant_kernel/kernel_stderr.log

  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) V21_0_KERNEL_CYCLE_DONE rc=$?" \
    >> data/v21_0_quant_kernel/kernel_stdout.log

  sleep 300
done
