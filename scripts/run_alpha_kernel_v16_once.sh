#!/data/data/com.termux/files/usr/bin/bash
set -e
cd "$(dirname "$0")/.."
export PYTHONPATH=$PWD
python -m joanbot.institutional_v16.alpha_kernel_v16
