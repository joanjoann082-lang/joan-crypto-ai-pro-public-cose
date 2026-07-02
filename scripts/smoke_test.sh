#!/data/data/com.termux/files/usr/bin/bash
cd "$(dirname "$0")/.." || exit 1
export PYTHONPATH=$PWD
python tests/smoke_test.py
