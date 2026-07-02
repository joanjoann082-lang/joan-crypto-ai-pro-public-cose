#!/data/data/com.termux/files/usr/bin/bash
cd /storage/emulated/0/Download/joan_crypto_ai_pro_v14 || exit 1
export PYTHONPATH=$PWD

echo "===== V24.4 STACK STATUS ====="
cat data/v24_4_accounting_core/status.txt 2>/dev/null || true

echo
echo "===== V24 AUTHORITY SUMMARY ====="
cat data/v24_0_quant_authority/summary.md 2>/dev/null | sed -n '1,120p' || true

echo
echo "===== V24.4 ADAPTER HEALTH ====="
python - <<'PY'
import sqlite3, json
con=sqlite3.connect("data/joanbot_v14.sqlite")
con.row_factory=sqlite3.Row

for t in ["institutional_v24_4_canonical_adapter_health", "institutional_v24_4_canonical_adapter_audit", "institutional_v24_4_accounting_repair_audit"]:
    if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (t,)).fetchone():
        print("NO_TABLE", t)
        continue
    cols=[r[1] for r in con.execute(f'PRAGMA table_info("{t}")')]
    order="id" if "id" in cols else cols[0]
    print("TABLE", t)
    for r in con.execute(f'SELECT * FROM "{t}" ORDER BY "{order}" DESC LIMIT 5'):
        d=dict(r)
        if "payload" in d and d["payload"]:
            try: d["payload"]=json.loads(d["payload"])
            except Exception: pass
        print(d)
    print()

con.close()
PY

echo
echo "===== PAPER POSITIONS ====="
python - <<'PY'
import sqlite3
con=sqlite3.connect("data/joanbot_v14.sqlite")
con.row_factory=sqlite3.Row
t="paper_micro_canary_positions_v11"
cols=[r[1] for r in con.execute(f'PRAGMA table_info("{t}")')]
wanted=["id","symbol","side","setup","status","entry_price","stop_loss_price","take_profit_price","exit_price","size_usd","pnl_usd","net_pnl_usd","pnl_r","net_pnl_r","mfe_r","mae_r","manager_state","close_reason","closed_at","last_managed_at"]
existing=[c for c in wanted if c in cols]
for r in con.execute(f'SELECT {",".join(existing)} FROM "{t}" ORDER BY id DESC LIMIT 8'):
    print(dict(r))
con.close()
PY

echo
echo "===== EQUITY PANEL ====="
python tools/v23_equity_panel.py 2>/dev/null || true
