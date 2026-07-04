
from pathlib import Path
import sys, json
ROOT = Path("/storage/emulated/0/Download/joan_crypto_ai_pro_v14")
sys.path.insert(0, str(ROOT))

from joanbot.storage import get_db
from joanbot.institutional.quant_core_v27_2 import get_core

db = get_db()
core = get_core(db)

before_clean = db.query("SELECT COUNT(*) c FROM edge_memory_v27_2_clean")[0]["c"] if db.query("SELECT name FROM sqlite_master WHERE type='table' AND name='edge_memory_v27_2_clean'") else 0

pos = {
    "id": "V27_2_TEST_POS",
    "symbol": "BTCUSDT",
    "side": "LONG",
    "setup": "V27_2_TEST_SETUP",
    "entry_price": 100.0,
    "stop_loss": 95.0,
    "initial_stop_loss": 95.0,
    "size_usd": 10000.0,
    "opened_at": "2026-01-01T00:00:00+00:00",
    "meta": {"decision": {"feature_summary": {"regime":"TEST","session":"TEST","volatility_bucket":"TEST","news_bucket":"TEST"}}}
}

trade = {
    "id": 999999272,
    "position_id": "V27_2_TEST_POS",
    "symbol": "BTCUSDT",
    "side": "LONG",
    "setup": "V27_2_TEST_SETUP",
    "entry": 100.0,
    "exit": 105.0,
    "size_usd": 10000.0,
    "pnl_usd": 500.0,
    "fees": 0.0,
    "reason": "V27_2_SELF_TEST",
    "ts": "2026-01-01T01:00:00+00:00",
    "payload": "{}"
}

res = core.record_live_trade(pos, trade)
core.rebuild_clean_state()

excluded = db.query("""
SELECT COUNT(*) c
FROM data_quality_exclusions_v27_2
WHERE source='LIVE' AND source_id='999999272'
""")[0]["c"]

polluted = db.query("""
SELECT COUNT(*) c
FROM promotion_state_v27_2
WHERE key LIKE '%V27_2_TEST%' OR payload LIKE '%V27_2_TEST%'
""")[0]["c"]

assert excluded >= 1, "SELF_TEST_NOT_EXCLUDED"
assert polluted == 0, "SELF_TEST_POLLUTED_PROMOTION"
print("V27_2_SELF_TEST_OK", json.dumps(res, sort_keys=True))
