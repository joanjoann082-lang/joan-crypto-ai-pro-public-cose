
from pathlib import Path
import sys, json
ROOT = Path("/storage/emulated/0/Download/joan_crypto_ai_pro_v14")
sys.path.insert(0, str(ROOT))
from joanbot.storage import get_db
from joanbot.institutional.quant_core_v27 import get_core

db = get_db()
core = get_core(db)
before = db.query("SELECT COUNT(*) c FROM outcome_ledger_v27")[0]["c"]

pos = {
    "id": "V27_TEST_POS",
    "symbol": "BTCUSDT",
    "side": "LONG",
    "setup": "V27_TEST_SETUP",
    "entry_price": 100.0,
    "stop_loss": 95.0,
    "initial_stop_loss": 95.0,
    "size_usd": 10000.0,
    "opened_at": "2026-01-01T00:00:00+00:00",
    "meta": {"decision": {"feature_summary": {"regime":"TEST","session":"TEST","volatility_bucket":"TEST","news_bucket":"TEST"}}}
}
trade = {
    "id": 999999001,
    "position_id": "V27_TEST_POS",
    "symbol": "BTCUSDT",
    "side": "LONG",
    "setup": "V27_TEST_SETUP",
    "entry": 100.0,
    "exit": 105.0,
    "size_usd": 10000.0,
    "pnl_usd": 500.0,
    "fees": 0.0,
    "reason": "V27_TEST",
    "ts": "2026-01-01T01:00:00+00:00",
    "payload": "{}"
}
res = core.record_live_trade(pos, trade)
after = db.query("SELECT COUNT(*) c FROM outcome_ledger_v27")[0]["c"]
rows = db.query("SELECT * FROM promotion_state_v27 WHERE key LIKE 'SETUP|BTCUSDT|LONG|V27_TEST_SETUP|%' LIMIT 1")
assert res and abs(res["result_r"] - 1.0) < 1e-9, res
assert after >= before + 1, (before, after)
assert rows, "NO_PROMOTION_STATE"
print("V27_TEST_OK", json.dumps(res, sort_keys=True))
