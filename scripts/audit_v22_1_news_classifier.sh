#!/data/data/com.termux/files/usr/bin/bash
cd /storage/emulated/0/Download/joan_crypto_ai_pro_v14 || exit 1

echo "=== V22.1 NEWS CLASSIFIER AUDIT ==="

echo
python - <<'PY_AUDIT'
from joanbot.market.macro_news import NewsFilter
nf=NewsFilter()
tests=[
 "Bitcoin plunges below $60,000: nearly 180,000 traders liquidated",
 "Painful Bitcoin sell-off drags Ethereum lower",
 "Bitcoin clings to $62,500 as bears tighten grip on crypto market",
 "ETF inflows boost Bitcoin rally",
 "Binance outage halts withdrawals after exploit rumors",
 "Fed CPI hotter than expected sends yields and DXY higher",
 "Oil spikes after Hormuz attack rattles markets",
]
for t in tests:
    e=nf.score_article(t,'TEST','',{})
    print(e['severity'], e['bucket'], e['direction'], e['category'], e['affected'], '|', t)
PY_AUDIT

echo
sqlite3 -header -column data/joanbot_v14.sqlite "
select 'news' layer, count(*) count, max(ts) latest from news_events;
select 'decisions' layer, count(*) count, max(ts) latest from decisions;
select 'runtime' layer, count(*) count, max(ts) latest from runtime_events;
"

echo
echo "=== LATEST NEWS ==="
sqlite3 -header -column data/joanbot_v14.sqlite "
select ts,source,category,round(severity,1) severity,direction,substr(title,1,120) title
from news_events order by id desc limit 12;
"

echo
echo "=== LATEST FINAL AUTHORITY ==="
python - <<'PY_FA'
import sqlite3,json
con=sqlite3.connect('data/joanbot_v14.sqlite'); con.row_factory=sqlite3.Row
for r in con.execute('select ts,symbol,action,side,final_score,payload from decisions order by id desc limit 12'):
    try:
        p=json.loads(r['payload']); fs=p.get('feature_summary') or {}; fa=fs.get('final_authority') or {}
        print(r['ts'], r['symbol'], r['action'], r['side'], round(r['final_score'],1), fa.get('version'), fa.get('news_severity'), fa.get('news_direction'), fa.get('reasons'))
    except Exception as e:
        print('ERR', e)
PY_FA

echo
echo "=== PROCESSES ==="
ps -ef | grep -E 'joanbot.runner|joanbot.ui.dashboard|watchdog|telegram' | grep -v grep || true

echo
echo "=== ERRORS ==="
tail -30 logs/runner_errors.log 2>/dev/null || true
