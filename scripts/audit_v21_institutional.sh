#!/data/data/com.termux/files/usr/bin/bash
cd /storage/emulated/0/Download/joan_crypto_ai_pro_v14 || exit 1
echo "=== V21 AUDIT ==="
sqlite3 data/joanbot_v14.sqlite "select 'market',count(*),max(ts) from market_snapshots; select 'macro',count(*),max(ts) from macro_snapshots; select 'news',count(*),max(ts) from news_events; select 'decisions',count(*),max(ts) from decisions; select 'alerts',count(*),max(ts) from alerts; select 'runtime',count(*),max(ts) from runtime_events;"
echo "=== LATEST NEWS ==="
sqlite3 -header -column data/joanbot_v14.sqlite "select ts,source,category,severity,direction,substr(title,1,90) title from news_events order by id desc limit 10;"
echo "=== LATEST FINAL AUTHORITY ==="
python - <<'PY'
import sqlite3,json
con=sqlite3.connect('data/joanbot_v14.sqlite'); con.row_factory=sqlite3.Row
for r in con.execute('select payload from decisions order by id desc limit 12'):
    try:
        p=json.loads(r['payload']); fa=(p.get('feature_summary') or {}).get('final_authority') or {}
        print(p.get('ts'), p.get('symbol'), p.get('action'), p.get('side'), round(p.get('final_score',0),1), fa.get('news_bucket'), fa.get('news_direction'), fa.get('reasons',[])[:4])
    except Exception as e: print('ERR',e)
PY
echo "=== CONTROL ==="; cat data/runtime_control.json 2>/dev/null || echo NO_CONTROL_FILE
echo "=== PROCESSES ==="; ps -ef | grep -E "joanbot.runner|joanbot.ui.dashboard|joanbot.integrations.telegram_bot|watchdog" | grep -v grep || true
echo "=== ERRORS ==="; tail -40 logs/runner_errors.log 2>/dev/null || true; tail -40 logs/telegram_command_bot_errors.log 2>/dev/null || true
