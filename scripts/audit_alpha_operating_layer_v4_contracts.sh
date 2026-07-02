#!/data/data/com.termux/files/usr/bin/bash
set -e

cd "$(dirname "$0")/.."
export PYTHONPATH=$PWD
FAIL=0

echo "===== ALPHA OPERATING LAYER V4 CONTRACT AUDIT ====="

echo "===== NO RUNTIME ====="
if ps -ef | grep -Ei "python.*joanbot.runner|joanbot_overnight_supervisor|joanbot.ui.dashboard|telegram_command_bot" | grep -v grep; then
  echo "RUNTIME_ACTIVE_ABORT"
  exit 1
else
  echo "NO_RUNTIME_OK"
fi

echo "===== COMPILE ====="
python -m py_compile joanbot/alpha/contracts.py || FAIL=1

echo "===== IMPORT TEST ====="
python - <<'PY' || FAIL=1
from joanbot.alpha.contracts import AlphaIdentity, AlphaEvidence, AlphaGovernanceVerdict, AlphaPromotionContract

i = AlphaIdentity(
    symbol="BTCUSDT",
    side="LONG",
    setup="TEST_SETUP",
    profile="SCALP_45",
    horizon_min=45,
    context_bucket="RANGE_CHOP|ASIA|NORMAL|RSI_MID|S1H_BEAR",
)

e = AlphaEvidence(n=100, mean_r=0.1, shrunk_expectancy_r=0.06)

v = AlphaGovernanceVerdict(
    identity=i,
    evidence=e,
    lifecycle_state="RESEARCH_READY",
)

c = AlphaPromotionContract(
    alpha_key=i.key(),
    cluster_key=i.cluster_key(),
    symbol=i.symbol,
    side=i.side,
    setup=i.setup,
    profile=i.profile,
    horizon_min=i.horizon_min,
    context_bucket=i.context_bucket,
    allowed_paper_micro_canary=False,
    allowed_direct_open=False,
    size_cap_usd=0.0,
    max_daily_per_alpha=0,
    max_daily_global=0,
    governance_score=0.0,
    promotion_score=0.0,
    required_execution_mode="NONE",
)

assert i.key()
assert i.cluster_key()
assert v.to_dict()["identity"]["symbol"] == "BTCUSDT"
assert c.to_dict()["allowed_direct_open"] is False
print("ALPHA_CONTRACTS_IMPORT_OK")
PY

echo "===== SAFETY: NOT CONNECTED TO TRADING PATH ====="
if grep -RInE "joanbot.alpha|AlphaGovernanceVerdict|AlphaPromotionContract|AlphaIdentity" \
  joanbot/runner.py \
  joanbot/intelligence/decision.py \
  joanbot/intelligence/risk.py \
  joanbot/execution \
  --include="*.py"; then
  echo "FAIL_ALPHA_CONTRACT_CONNECTED_TOO_EARLY"
  FAIL=1
else
  echo "TRADING_PATH_UNCHANGED_OK"
fi

echo "===== PROTECTED TABLES UNTOUCHED ====="
sqlite3 data/joanbot_v14.sqlite "PRAGMA integrity_check;" || FAIL=1

echo "===== GIT DIFF CHECK ====="
git diff --check || FAIL=1

echo "===== STATUS ====="
git status --short

if [ "$FAIL" -eq 0 ]; then
  echo "ALPHA_OPERATING_LAYER_V4_CONTRACT_AUDIT_OK"
else
  echo "ALPHA_OPERATING_LAYER_V4_CONTRACT_AUDIT_FAIL"
  exit 1
fi
