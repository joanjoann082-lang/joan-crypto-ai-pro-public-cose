#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

cd /storage/emulated/0/Download/joan_crypto_ai_pro_v14 || exit 1

echo "===== GIT SAFE SYNC ====="

if [ ! -d .git ]; then
  echo "NO_GIT_REPO: no faig git init automàtic. Inicialitza o clona el repo abans."
  exit 0
fi

cat > .gitignore <<'EOF'
__pycache__/
*.pyc
*.pyo
*.swp
*.tmp

# DB / WAL / runtime data
data/*.sqlite
data/*.sqlite-*
data/**/*.sqlite
data/**/*.sqlite-*
data/**/*.db
data/**/*.db-*

# logs / pids / runtime volatile
data/**/*.log
data/**/*.pid
data/**/manual_*
data/**/runtime_*
data/**/kernel_*
data/**/adapter_*
data/**/gateway_*
data/**/supervisor_*
data/**/stderr*
data/**/stdout*

# backups can grow fast
data/*_backups/
data/**/backups/

# secrets/env
.env
*.key
*.pem
secrets/
EOF

git config user.name >/dev/null 2>&1 || git config user.name "Joan Bot"
git config user.email >/dev/null 2>&1 || git config user.email "joanbot-local@users.noreply.github.com"

git status --short

git add .gitignore tools scripts || true

if git diff --cached --quiet; then
  echo "GIT_NO_CODE_CHANGES"
  exit 0
fi

MSG="runtime: V22.1 institutional supervisor $(date -u +%Y-%m-%dT%H:%M:%SZ)"
git commit -m "$MSG"

if git remote | grep -q .; then
  git push
  echo "GIT_PUSH_OK"
else
  echo "GIT_COMMIT_LOCAL_ONLY: falta remote GitHub"
fi
