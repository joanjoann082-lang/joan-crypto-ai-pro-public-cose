#!/data/data/com.termux/files/usr/bin/bash
cd "$(dirname "$0")/.." || exit 1
export PYTHONPATH=$PWD
echo "=== COMPILE ==="
python -m py_compile $(find joanbot -name "*.py") tests/smoke_test.py && echo COMPILE_OK
echo "=== INTERNET ==="
curl -s --max-time 8 https://api.binance.com/api/v3/time || echo BINANCE_FAIL
echo
echo "=== HEALTH ==="
python -m joanbot.cli status
