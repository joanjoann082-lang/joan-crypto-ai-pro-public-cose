#!/data/data/com.termux/files/usr/bin/bash
set -e

cd "$(dirname "$0")/.."
export PYTHONPATH=$PWD
FAIL=0

echo "===== ALPHA DATA SPINE V1 AUDIT ====="

echo "===== COMPILE ====="
python -m py_compile \
  joanbot/alpha/contracts.py \
  joanbot/alpha/alpha_feature_store_v1.py \
  joanbot/alpha/alpha_label_store_v1.py \
  || FAIL=1

echo "===== IMPORT + BUILD TEST ====="
python - <<'PY' || FAIL=1
from joanbot.alpha.alpha_feature_store_v1 import AlphaFeatureStoreV1
from joanbot.alpha.alpha_label_store_v1 import AlphaLabelStoreV1

fs = AlphaFeatureStoreV1()
ls = AlphaLabelStoreV1()

buckets = fs.current_buckets()
ev = ls.all_evidence()

print("current_buckets", buckets)
print("alpha_evidence_groups", len(ev))

if not isinstance(buckets, dict):
    raise SystemExit("BUCKETS_NOT_DICT")

if len(ev) <= 0:
    raise SystemExit("NO_ALPHA_EVIDENCE_BUILT")

first = ev[0]
assert first["identity"].key()
assert first["identity"].cluster_key()
assert first["evidence"].n >= 1
assert first["evidence"].source

print("ALPHA_DATA_SPINE_BUILD_OK")
PY

echo "===== SAFETY: NOT IN TRADING PATH ====="
if grep -RInE "AlphaFeatureStoreV1|AlphaLabelStoreV1" \
  joanbot/runner.py \
  joanbot/intelligence/decision.py \
  joanbot/intelligence/risk.py \
  joanbot/execution \
  --include="*.py"; then
  echo "FAIL_ALPHA_DATA_SPINE_CONNECTED_TOO_EARLY"
  FAIL=1
else
  echo "TRADING_PATH_UNCHANGED_OK"
fi

echo "===== SAFETY: NO PROTECTED MUTATION ====="
python - <<'PY' || FAIL=1
from pathlib import Path

files = [
    Path("joanbot/alpha/alpha_feature_store_v1.py"),
    Path("joanbot/alpha/alpha_label_store_v1.py"),
]

forbidden = [
    "INSERT INTO decisions", "UPDATE decisions", "DELETE FROM decisions",
    "INSERT INTO positions", "UPDATE positions", "DELETE FROM positions",
    "INSERT INTO trades", "UPDATE trades", "DELETE FROM trades",
    "INSERT INTO forward_cases", "UPDATE forward_cases", "DELETE FROM forward_cases",
    "INSERT INTO forward_results", "UPDATE forward_results", "DELETE FROM forward_results",
]

hits = []
for p in files:
    s = p.read_text()
    for f in forbidden:
        if f in s:
            hits.append((str(p), f))

if hits:
    print("FAIL_FORBIDDEN_MUTATION", hits)
    raise SystemExit(1)

print("NO_FORBIDDEN_MUTATION_OK")
PY

echo "===== DB INTEGRITY ====="
sqlite3 data/joanbot_v14.sqlite "PRAGMA integrity_check;" || FAIL=1

echo "===== GIT DIFF CHECK ====="
git diff --check || FAIL=1

echo "===== STATUS ====="
git status --short

if [ "$FAIL" -eq 0 ]; then
  echo "ALPHA_DATA_SPINE_V1_AUDIT_OK"
else
  echo "ALPHA_DATA_SPINE_V1_AUDIT_FAIL"
  exit 1
fi
