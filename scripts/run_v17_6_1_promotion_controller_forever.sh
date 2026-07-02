#!/data/data/com.termux/files/usr/bin/bash
set -u

cd /storage/emulated/0/Download/joan_crypto_ai_pro_v14 || exit 1
export PYTHONPATH=$PWD
mkdir -p data/v17_6_1

while true; do
  python tools/run_promotion_controller_v17_6_1.py --write-db --emit-review-queue \
    >> data/v17_6_1/promotion_controller_stdout.log \
    2>> data/v17_6_1/promotion_controller_stderr.log

  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) V17_6_1_PROMOTION_CONTROLLER_CYCLE_DONE rc=$?" \
    >> data/v17_6_1/promotion_controller_stdout.log

  sleep 300
done
