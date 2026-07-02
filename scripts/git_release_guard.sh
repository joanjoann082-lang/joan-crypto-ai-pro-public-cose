#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

cd /storage/emulated/0/Download/joan_crypto_ai_pro_v14 || exit 1

echo "===== GIT RELEASE GUARD V1.1 ====="

if [ ! -d .git ]; then
  echo "FAIL: NO_GIT_REPO"
  exit 1
fi

cat > .gitignore <<'EOF'
__pycache__/
*.pyc
*.pyo
*.swp
*.tmp

.env
*.key
*.pem
secrets/

data/
logs/
*.log
*.pid

*.sqlite
*.sqlite-*
*.db
*.db-*

*.bak
*.backup
*.old
*.BROKEN*
*broken*
*_before_*
*_backup_*
audit_export_*
EOF

echo "===== RESET STAGING ====="
git reset

echo "===== STAGE ONLY APPROVED FILES ====="
git add .gitignore
git add scripts/git_release_guard.sh
git add scripts/git_safe_sync.sh 2>/dev/null || true
git add scripts/run_v22_1_runtime_manager_forever.sh
git add tools/v22_1_runtime_manager.py

echo "===== STAGED FILES ====="
git diff --cached --name-only

echo "===== BLOCK IF STAGED SUSPICIOUS ====="
BAD="$(git diff --cached --name-only | grep -Ei '(^data/|^logs/|\.sqlite|\.db|\.log|\.pid|\.bak|backup|broken|audit_export|^\.env$|secret|key|pem)' || true)"
if [ -n "$BAD" ]; then
  echo "FAIL: suspicious files staged. No commit."
  echo "$BAD"
  git reset
  exit 2
fi

echo "===== STAGED DIFF STAT ====="
git diff --cached --stat

if git diff --cached --quiet; then
  echo "GIT_NO_APPROVED_CHANGES"
  exit 0
fi

MSG="ops: add allowlist git release guard $(date -u +%Y-%m-%dT%H:%M:%SZ)"
git commit -m "$MSG"

if git remote | grep -q .; then
  git push
  echo "GIT_PUSH_OK"
else
  echo "GIT_COMMIT_LOCAL_ONLY_NO_REMOTE"
fi

echo "===== STATUS REMAINING, NOT COMMITTED ====="
git status --short | head -80
