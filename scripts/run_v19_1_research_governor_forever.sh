#!/data/data/com.termux/files/usr/bin/bash
set -u
cd /storage/emulated/0/Download/joan_crypto_ai_pro_v14 || exit 1
export PYTHONPATH=$PWD
mkdir -p data/v19_1_research_governor

while true; do
  python tools/v19_1_institutional_research_governor.py --emit-one \
    >> data/v19_1_research_governor/governor_stdout.log \
    2>> data/v19_1_research_governor/governor_stderr.log

  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) V19_1_GOVERNOR_CYCLE_DONE rc=$?" \
    >> data/v19_1_research_governor/governor_stdout.log

  sleep 300
done
