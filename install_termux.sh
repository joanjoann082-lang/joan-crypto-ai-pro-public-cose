#!/data/data/com.termux/files/usr/bin/bash
set -e
pkg update -y || true
pkg install -y python curl unzip procps || true
mkdir -p data logs backups
python -m py_compile $(find joanbot -name "*.py") tests/smoke_test.py
printf "INSTALL_OK\n"
