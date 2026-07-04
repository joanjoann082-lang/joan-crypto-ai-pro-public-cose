#!/data/data/com.termux/files/usr/bin/bash
cd /storage/emulated/0/Download/joan_crypto_ai_pro_v14 || exit 1
while true; do
  python tools/publish_status_github_v25_3.py > data/github_status_publish_last.log 2> data/github_status_publish_error.log
  sleep 120
done
