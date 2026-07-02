#!/usr/bin/env bash
cd /storage/emulated/0/Download/joan_crypto_ai_pro_v14 || exit 1

echo "=== V24 COMMAND CENTER AUDIT ==="
echo
ps -ef | grep -E "joanbot.runner|joanbot.ui.dashboard|telegram_command_bot|joanbot.integrations.telegram_bot|watchdog|core.runner|runner.runner|runtime_supervisor|joanbot.dashboard" | grep -v grep || true

echo
echo "=== DB STATUS ==="
sqlite3 -header -column data/joanbot_v14.sqlite "
select 'market', count(*), max(ts) from market_snapshots;
select 'macro', count(*), max(ts) from macro_snapshots;
select 'news', count(*), max(ts) from news_events;
select 'decisions', count(*), max(ts) from decisions;
select 'alerts', count(*), max(ts) from alerts;
select 'runtime', count(*), max(ts) from runtime_events;
select 'control_audit', count(*), max(ts) from runtime_control_audit;
"

echo
echo "=== CONTROL ==="
cat data/runtime_control.json 2>/dev/null || echo NO_CONTROL

echo
echo "=== RECENT DECISIONS RUNTIME CONTROL ==="
python - <<'PY'
import sqlite3,json
con=sqlite3.connect('data/joanbot_v14.sqlite'); con.row_factory=sqlite3.Row
rows=con.execute('select ts,symbol,action,side,final_score,payload from decisions order by id desc limit 8').fetchall()
for r in rows:
    p=json.loads(r['payload'])
    fs=p.get('feature_summary') or {}
    rc=fs.get('runtime_control')
    txt=json.dumps(p)[:4000]
    bad=any(x in txt for x in ['NEWS_SEVERE_REDUCE','V21_FINAL_AUTHORITY','SCHEDULED_MACRO_EVENT_RISK'])
    print(r['ts'], r['symbol'], r['action'], r['side'], round(r['final_score'],1), 'runtime=', rc, 'bad_news_gate=', bad)
PY

echo
echo "=== ERRORS ==="
tail -40 logs/runner_errors.log 2>/dev/null || true
tail -40 logs/telegram_command_bot_errors.log 2>/dev/null || true

echo
echo "AUDIT_V24_DONE"
