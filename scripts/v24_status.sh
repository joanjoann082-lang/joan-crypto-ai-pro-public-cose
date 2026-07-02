#!/data/data/com.termux/files/usr/bin/bash
cd /storage/emulated/0/Download/joan_crypto_ai_pro_v14 || exit 1
export PYTHONPATH=$PWD

echo "===== V24 STATUS ====="
python tools/v24_0_quant_production_authority.py --status 2>/dev/null || true

echo
echo "===== V24 STACK PROCESSES ====="
ps -ef | grep -Ei "data_plane|liquidation|quant_brain|paper_canary_adapter|market_context|v24_0_quant|v22_1_runtime|promotion_controller|governance_v17|v23_3|v23_4" | grep -v grep || true

echo
echo "===== LATEST INTENTS ====="
python - <<'PY'
import sqlite3
con=sqlite3.connect("data/joanbot_v14.sqlite")
con.row_factory=sqlite3.Row
t="institutional_quant_canary_execution_intents_v17_7_2"
if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (t,)).fetchone():
    for r in con.execute(f'''
    SELECT id, ts, version, intent_state, adapter_status, symbol, side, setup, requested_size_mult
    FROM "{t}"
    ORDER BY id DESC
    LIMIT 10
    '''):
        print(dict(r))
con.close()
PY

echo
echo "===== EQUITY ====="
python tools/v23_equity_panel.py 2>/dev/null || true
