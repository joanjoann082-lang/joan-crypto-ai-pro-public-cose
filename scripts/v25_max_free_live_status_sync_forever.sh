#!/usr/bin/env bash
set +e

BOT="/storage/emulated/0/Download/joan_crypto_ai_pro_v14"
LIVE_REPO="/data/data/com.termux/files/home/joan_bot_live_status_public"
BRANCH="live-status"
INTERVAL_SECONDS=180
LOCKDIR="$BOT/data/v25_max_free_live_status_sync.lock"

if ! mkdir "$LOCKDIR" 2>/dev/null; then
  echo "LIVE_STATUS_SYNC_ALREADY_RUNNING"
  exit 0
fi

trap 'rmdir "$LOCKDIR" 2>/dev/null || true' EXIT

cd "$BOT" || exit 1
export PYTHONPATH="$BOT"

while true; do
  TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "[$TS] LIVE_STATUS_EXPORT_START"

  python tools/v25_max_free_live_status_exporter.py

  if [ -d "$LIVE_REPO/.git" ]; then
    cp live_export/status.json "$LIVE_REPO/status.json" 2>/dev/null || true
    cp live_export/status.md "$LIVE_REPO/status.md" 2>/dev/null || true
    cp live_export/money.md "$LIVE_REPO/money.md" 2>/dev/null || true
    cp live_export/dashboard.html "$LIVE_REPO/dashboard.html" 2>/dev/null || true
    cp live_export/heartbeat.txt "$LIVE_REPO/heartbeat.txt" 2>/dev/null || true

    git -C "$LIVE_REPO" checkout "$BRANCH" >/dev/null 2>&1 || git -C "$LIVE_REPO" checkout -B "$BRANCH"
    git -C "$LIVE_REPO" add status.json status.md money.md dashboard.html heartbeat.txt README.md .gitignore

    if ! git -C "$LIVE_REPO" diff --cached --quiet; then
      git -C "$LIVE_REPO" commit --amend -m "live bot status $TS" >/dev/null 2>&1 || true
      git -C "$LIVE_REPO" push origin "$BRANCH" --force >/dev/null 2>&1 || true
    fi
  else
    echo "LIVE_REPO_MISSING:$LIVE_REPO"
  fi

  echo "[$TS] LIVE_STATUS_EXPORT_DONE"
  sleep "$INTERVAL_SECONDS"
done
