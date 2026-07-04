from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import json, urllib.request, sqlite3, datetime, sys

from joanbot.institutional import canonical_market_data_contract_v24_9_final as md
from joanbot.institutional import canonical_paper_accounting_v24_4 as acc

def num(x):
    try:
        return float(x)
    except Exception:
        return 0.0

def diff_pct(a, b):
    return abs(a - b) / b * 100.0 if b else 999.0

con = sqlite3.connect("data/joanbot_v14.sqlite")
con.row_factory = sqlite3.Row

url = "https://fapi.binance.com/fapi/v1/premiumIndex?symbol=BTCUSDT"
with urllib.request.urlopen(url, timeout=10) as r:
    raw = json.loads(r.read().decode())

binance_mark = num(raw.get("markPrice"))

snap = md.canonical_price_snapshot(con, "BTCUSDT")
evalr = md.evaluate_symbol(con, "BTCUSDT")
acc_price, acc_meta = acc.canonical_price(con, "BTCUSDT")

snap_price = num(snap.get("price") or snap.get("raw_price") or snap.get("mark_price"))
eval_price = num(evalr.get("price") or evalr.get("mark_price"))
acc_price = num(acc_price)

print("===== VALIDACIO PREU CANONIC FRESC =====")
print("UTC:", datetime.datetime.now(datetime.timezone.utc).isoformat())
print("BINANCE_MARK:", binance_mark)
print("SNAPSHOT:", snap_price, "diff_pct=", round(diff_pct(snap_price, binance_mark), 4), "reason=", snap.get("reason"))
print("EVALUATE:", eval_price, "diff_pct=", round(diff_pct(eval_price, binance_mark), 4), "reason=", evalr.get("reason"))
print("ACCOUNTING:", acc_price, "diff_pct=", round(diff_pct(acc_price, binance_mark), 4), "source=", acc_meta.get("source"))

errors = []
for name, price in [
    ("SNAPSHOT", snap_price),
    ("EVALUATE", eval_price),
    ("ACCOUNTING", acc_price),
]:
    if price <= 0:
        errors.append(f"{name}_NO_PRICE")
    elif diff_pct(price, binance_mark) > 0.25:
        errors.append(f"{name}_DIFF_TOO_HIGH")

# comprovar status actualitzat
try:
    row = con.execute("""
        SELECT symbol, ts, price, reason, source, source_age_min
        FROM institutional_v24_9_max_market_data_status
        WHERE symbol='BTCUSDT'
        ORDER BY rowid DESC
        LIMIT 1
    """).fetchone()
    print("STATUS_MAX:", dict(row) if row else None)
except Exception as e:
    print("STATUS_MAX_ERROR:", repr(e))

if errors:
    print("ERRORS:", errors)
    sys.exit(1)

print("VALIDACIO_PREU_CANONIC_FRESC_OK")
