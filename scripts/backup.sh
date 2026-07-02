#!/data/data/com.termux/files/usr/bin/bash
cd "$(dirname "$0")/.." || exit 1
mkdir -p backups
tar -czf backups/joanbot_backup_$(date +%Y%m%d_%H%M%S).tar.gz data logs .env 2>/dev/null || true
ls -lh backups | tail
