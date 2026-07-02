#!/data/data/com.termux/files/usr/bin/bash
cd "$(dirname "$0")/.."
export PYTHONPATH=$PWD

python scripts/v16_storage_spine_guard.py >> data/v16/storage_spine_guard.log 2>> data/v16/storage_spine_guard_errors.log || true
