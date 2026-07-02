#!/data/data/com.termux/files/usr/bin/bash
set -u

cd /storage/emulated/0/Download/joan_crypto_ai_pro_v14 || exit 1
export PYTHONPATH=$PWD

mkdir -p data/v18_9_1_data_plane data/v18_9_5_liquidation_binding

while true; do
  python tools/v18_9_1_semantic_data_plane.py \
    >> data/v18_9_1_data_plane/gateway_stdout.log \
    2>> data/v18_9_1_data_plane/gateway_stderr.log

  python tools/v18_9_5_canonical_liquidation_binder.py \
    >> data/v18_9_5_liquidation_binding/binder_stdout.log \
    2>> data/v18_9_5_liquidation_binding/binder_stderr.log

  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) V18_9_5_DATA_PLANE_CANONICAL_CYCLE_DONE rc=$?" \
    >> data/v18_9_5_liquidation_binding/binder_stdout.log

  sleep 60
done
