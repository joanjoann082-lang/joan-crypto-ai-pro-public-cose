#!/data/data/com.termux/files/usr/bin/bash
set -u

cd /storage/emulated/0/Download/joan_crypto_ai_pro_v14 || exit 1
export PYTHONPATH=$PWD
mkdir -p data/v18_6_payoff

while true; do
  python tools/v18_6_payoff_intelligence.py \
    >> data/v18_6_payoff/payoff_stdout.log \
    2>> data/v18_6_payoff/payoff_stderr.log

  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) V18_6_PAYOFF_CYCLE_DONE rc=$?" \
    >> data/v18_6_payoff/payoff_stdout.log

  sleep 60
done
