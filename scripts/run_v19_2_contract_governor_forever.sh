#!/data/data/com.termux/files/usr/bin/bash
set -u

cd /storage/emulated/0/Download/joan_crypto_ai_pro_v14 || exit 1
export PYTHONPATH=$PWD
mkdir -p data/v19_2_contract_governor

while true; do
  python tools/v19_2_contract_research_governor.py --emit-one \
    >> data/v19_2_contract_governor/contract_stdout.log \
    2>> data/v19_2_contract_governor/contract_stderr.log

  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) V19_2_CONTRACT_GOVERNOR_CYCLE_DONE rc=$?" \
    >> data/v19_2_contract_governor/contract_stdout.log

  sleep 300
done
