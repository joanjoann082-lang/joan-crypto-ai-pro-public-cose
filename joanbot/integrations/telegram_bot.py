from __future__ import annotations
import json, os, sqlite3, time, urllib.parse, urllib.request, traceback
from pathlib import Path
from ..config import load_env, STATE_PATH, CFG
from ..utils import read_json, fnum
from ..ops.runtime_control import RuntimeControl

DB=Path('data/joanbot_v14.sqlite')
CTRL=RuntimeControl()
load_env()

def token(): return os.getenv('TELEGRAM_BOT_TOKEN','').strip()
def chat_id(): return os.getenv('TELEGRAM_CHAT_ID','').strip()

def api(method, payload=None):
    url=f'https://api.telegram.org/bot{token()}/{method}'
    data=urllib.parse.urlencode(payload or {}).encode() if payload else None
    with urllib.request.urlopen(urllib.request.Request(url,data=data), timeout=25) as r:
        return json.loads(r.read().decode())

def send(text):
    api('sendMessage', {'chat_id':chat_id(),'text':text[:3900],'disable_web_page_preview':'true'})

def q(sql, params=()):
    con=sqlite3.connect(DB); con.row_factory=sqlite3.Row
    rows=[dict(r) for r in con.execute(sql, params).fetchall()]
    con.close(); return rows

def one(sql, params=()):
    r=q(sql, params); return r[0] if r else {}

def money(x):
    try: return f"${float(x):,.2f}"
    except Exception: return '—'

def num(x,n=1):
    try: return f"{float(x):.{n}f}"
    except Exception: return '—'

def menu():
    return (
"🧭 JOANBOT COMMAND CENTER\n\n"
"📌 Consultar\n"
"/status — estat curt del bot\n"
"/idea — millor oportunitat ara\n"
"/why — per què decideix això\n"
"/trades — posicions obertes\n"
"/news — risc de notícies\n"
"/edge — qualitat estadística resumida\n"
"/forward — forward test resumit\n"
"/health — salut tècnica\n\n"
"🎛 Control segur\n"
"/settings — configuració actual\n"
"/recommend — recomanació de score/risc\n"
"/set_open 78 — score mínim per OPEN\n"
"/set_probe 62 — score mínim per PROBE\n"
"/risk 0.5 — multiplicador de mida/risc\n"
"/mode conservative|normal|aggressive\n"
"/pause — bloqueja noves entrades\n"
"/resume — reactiva noves entrades\n"
"/long on|off · /short on|off\n"
"/newtrades on|off\n"
"/confirm — confirma canvi sensible\n"
"/cancel — anul·la canvi pendent\n"
"/undo — desfà últim canvi aplicat\n\n"
"Regla: el bot NO baixa scores sol. Recomana; tu confirmes."
    )

def status():
    st=read_json(STATE_PATH,{})
    wallet=st.get('wallet') or {}
    pol=CTRL.policy(CFG.open_threshold, CFG.probe_threshold)
    d=one('SELECT ts,symbol,action,side,round(final_score,1) score,setup FROM decisions ORDER BY id DESC LIMIT 1')
    cnt=one("SELECT (SELECT count(*) FROM market_snapshots) market,(SELECT count(*) FROM decisions) decisions,(SELECT count(*) FROM news_events) news,(SELECT count(*) FROM runtime_events) runtime")
    return (
        f"📡 STATUS\n\n"
        f"Bot: {'⏸ PAUSAT' if pol['paused'] else '✅ ACTIU'}\n"
        f"Equity paper: {money(wallet.get('equity'))}\n"
        f"Obertes: {len(wallet.get('open',[]) or [])}\n"
        f"Mode: {pol['mode']} · Risk x{num(pol['risk_mult'],2)}\n"
        f"OPEN ≥ {num(pol['open_threshold_effective'],0)} · PROBE ≥ {num(pol['probe_threshold_effective'],0)}\n\n"
        f"Última decisió: {d.get('symbol','—')} {d.get('action','—')} {d.get('side','—')} score {d.get('score','—')}\n"
        f"Setup: {d.get('setup','—')}\n\n"
        f"DB: market {cnt.get('market','—')} · decisions {cnt.get('decisions','—')} · news {cnt.get('news','—')}"
    )

def _decision_payload(row):
    try: return json.loads(row.get('payload') or '{}')
    except Exception: return {}

def idea():
    rows=q('SELECT * FROM decisions ORDER BY id DESC LIMIT 30')
    if not rows: return '🎯 IDEA\nNo hi ha decisions encara.'
    best=max(rows, key=lambda r: fnum(r.get('final_score')))
    p=_decision_payload(best); fs=p.get('feature_summary') or {}
    verdict='NO TOCAR' if best.get('action')=='WAIT' else 'OBSERVAR, encara no fort' if best.get('action')=='PROBE' else 'TRADE POSSIBLE'
    return (
        f"🎯 MILLOR IDEA ARA\n\n"
        f"{best.get('symbol')} {best.get('side')} · {best.get('action')}\n"
        f"Score: {num(best.get('final_score'),1)}/100 · Confiança: {num(best.get('confidence'),0)}/100\n"
        f"Veredicte: {verdict}\n"
        f"Setup: {best.get('setup')}\n\n"
        f"Entry: {money(p.get('entry'))}\n"
        f"SL: {money(p.get('stop_loss'))}\n"
        f"TP1: {money(p.get('take_profit_1'))}\n"
        f"TP2: {money(p.get('take_profit_2'))}\n"
        f"Size paper: {money(best.get('size_usd'))}\n\n"
        f"Context: regime {fs.get('regime','—')} · macro {num(fs.get('macro'),0)} · news {num(fs.get('news'),0)}\n"
        f"Per què: {', '.join((p.get('reasons') or [])[:5])}"
    )

def why():
    rows=q('SELECT * FROM decisions ORDER BY id DESC LIMIT 4')
    if not rows: return '❓ WHY\nNo hi ha decisions.'
    out=['❓ WHY — últimes decisions\n']
    for r in rows:
        p=_decision_payload(r); reasons=(p.get('reasons') or [])[:6]
        out.append(f"{r.get('symbol')} {r.get('action')} {r.get('side')} · score {num(r.get('final_score'),1)}\nSetup: {r.get('setup')}\nRaons: {', '.join(reasons) if reasons else '—'}\n")
    return '\n'.join(out)

def trades():
    st=read_json(STATE_PATH,{})
    rows=(st.get('wallet') or {}).get('open') or []
    if not rows: return '📌 POSICIONS\nNo hi ha posicions obertes.'
    out=['📌 POSICIONS OBERTES\n']
    for r in rows[:8]:
        out.append(f"{r.get('symbol','—')} {r.get('side','—')}\nEntry: {money(r.get('entry'))} · Last: {money(r.get('last_price'))}\nSize: {money(r.get('size') or r.get('size_usd'))} · PnL: {money(r.get('pnl') or r.get('pnl_usd'))}\nSetup: {r.get('setup','—')}\n")
    return '\n'.join(out)

def news():
    rows=q('SELECT ts,source,category,severity,direction,title FROM news_events ORDER BY id DESC LIMIT 8')
    if not rows: return '📰 NEWS\nNo hi ha notícies guardades.'
    out=['📰 NEWS / EVENT RISK\n']
    for r in rows:
        sev=fnum(r.get('severity')); icon='🔴' if sev>=70 else '🟡' if sev>=35 else '⚪'
        out.append(f"{icon} {num(sev,0)}/100 · {r.get('direction','—')} · {r.get('category','—')}\n{str(r.get('title','—'))[:170]}\n")
    return '\n'.join(out)

def edge():
    rows=q("SELECT key,source,n,wins,losses,sum_r,sum_pos_r,sum_neg_r,max_dd_r FROM edge_memory ORDER BY n DESC LIMIT 8")
    if not rows: return '🧠 EDGE\nEncara sense mostra.'
    out=['🧠 EDGE MEMORY — resum\n']
    for r in rows:
        n=fnum(r.get('n')); wins=fnum(r.get('wins')); wr=(wins/n*100) if n else 0
        avg=fnum(r.get('sum_r'))/n if n else 0
        key=str(r.get('key',''))[:42]
        out.append(f"{key}\nN {num(n,0)} · WR {num(wr,0)}% · AvgR {num(avg,3)} · src {r.get('source')}\n")
    return '\n'.join(out)

def forward():
    rows=q('SELECT symbol,outcome,result_r,mfe_r,mae_r FROM forward_results ORDER BY id DESC LIMIT 600')
    vals=[]; wins=0; by={}
    for r in rows:
        rr=fnum(r.get('result_r')); vals.append(rr); wins += 1 if rr>0 else 0
        s=r.get('symbol') or 'UNK'; by.setdefault(s,[]).append(rr)
    if not vals: return '🔎 FORWARD\nEncara sense resultats.'
    out=[f"🔎 FORWARD TEST\n\nMostra: {len(vals)}\nWinrate: {num(wins/len(vals)*100,1)}%\nAvg R: {num(sum(vals)/len(vals),3)}\n"]
    for s, arr in by.items():
        out.append(f"{s}: N {len(arr)} · AvgR {num(sum(arr)/len(arr),3)}")
    return '\n'.join(out)

def settings():
    raw=CTRL.load(); pol=CTRL.policy(CFG.open_threshold, CFG.probe_threshold)
    p=raw.get('pending_change')
    pend='cap'
    if p: pend=f"{p.get('apply')} · caduca en ~{max(0,int((p.get('expires_at',0)-time.time())/60))} min"
    return (
        f"🎛 SETTINGS\n\n"
        f"Paused: {pol['paused']}\nNew trades: {pol['allow_new_trades']}\nLong: {pol['allow_long']} · Short: {pol['allow_short']}\n"
        f"Mode: {pol['mode']}\nRisk mult efectiu: {num(pol['risk_mult'],2)}\nOPEN efectiu: {num(pol['open_threshold_effective'],0)}\nPROBE efectiu: {num(pol['probe_threshold_effective'],0)}\n\n"
        f"Canvi pendent: {pend}\nÚltim canvi: {raw.get('last_applied') or 'cap'}"
    )

def health():
    procs='—'
    try:
        import subprocess
        procs=subprocess.check_output("ps -ef | grep -E 'joanbot.runner|telegram_bot|dashboard' | grep -v grep", shell=True, text=True, timeout=5)
    except Exception: pass
    errs='Sense errors recents.'
    try:
        tail=Path('logs/runner_errors.log').read_text(errors='ignore')[-1000:]
        errs=tail.strip() or errs
    except Exception: pass
    return f"🩺 HEALTH\n\nProcessos:\n{procs[:1200]}\nErrors:\n{errs[:1200]}"

def errors():
    rows=q("SELECT ts,component,level,message FROM runtime_events WHERE level='ERROR' ORDER BY id DESC LIMIT 8")
    if not rows: return '✅ ERRORS\nNo hi ha errors runtime recents.'
    out=['⚠️ ERRORS RECENTS\n']
    for r in rows: out.append(f"{r.get('ts')} · {r.get('component')} · {r.get('message')}")
    return '\n'.join(out)

def recommend():
    rows=q('SELECT result_r FROM forward_results ORDER BY id DESC LIMIT 600')
    vals=[]
    for r in rows:
        try: vals.append(float(r.get('result_r')))
        except Exception: pass
    if len(vals)<200: return '🧠 RECOMMEND\nMostra insuficient. No tocaria scores.'
    avg=sum(vals)/len(vals); wr=sum(1 for x in vals if x>0)/len(vals)*100
    pol=CTRL.policy(CFG.open_threshold, CFG.probe_threshold)
    lines=['🧠 RECOMMEND', '', f'Mostra: {len(vals)} forward cases', f'Winrate: {wr:.1f}%', f'Avg R: {avg:.3f}', f"OPEN actual: {pol['open_threshold_effective']:.0f}", f"Risk: x{pol['risk_mult']:.2f}", '']
    if avg<-0.10 or wr<45:
        lines += ['Diagnòstic: edge recent feble.', 'Recomanació: pujar prudència.', 'Comanda: /set_open 78', 'Després: /confirm', 'Alternativa: /risk 0.5 + /confirm']
    elif avg>0.12 and wr>55:
        lines += ['Diagnòstic: edge recent positiu.', 'Recomanació: mantenir o baixar OPEN només 2 punts.', 'Comanda: /set_open 72', 'Després: /confirm']
    else:
        lines += ['Diagnòstic: zona neutra.', 'Recomanació: no tocar thresholds ara.']
    return '\n'.join(lines)

def pending_change(apply, label):
    CTRL.set_pending(apply, source='telegram', note=label)
    return f"⚠️ Canvi pendent: {label}\n\nEnvia /confirm per aplicar-lo.\nEnvia /cancel per anul·lar.\nCaduca en 5 minuts."

def handle(t: str):
    parts=t.strip().split(); cmd=parts[0].lower() if parts else '/menu'
    try:
        if cmd in ('/start','/help','/menu'): return menu()
        if cmd=='/status': return status()
        if cmd=='/idea': return idea()
        if cmd in ('/why','/why_wait'): return why()
        if cmd in ('/trades','/positions'): return trades()
        if cmd=='/news': return news()
        if cmd=='/edge': return edge()
        if cmd=='/forward': return forward()
        if cmd in ('/settings','/control'): return settings()
        if cmd=='/health': return health()
        if cmd=='/errors': return errors()
        if cmd=='/recommend': return recommend()
        if cmd=='/pause': CTRL.apply_direct({'paused':True}, source='telegram', note='pause'); return '⏸ Pausat. No s’haurien d’obrir noves entrades.'
        if cmd=='/resume': CTRL.apply_direct({'paused':False,'allow_new_trades':True}, source='telegram', note='resume'); return '✅ Reprès. Noves entrades permeses segons thresholds.'
        if cmd=='/confirm': CTRL.confirm(source='telegram'); return '✅ Canvi confirmat i aplicat.'
        if cmd=='/cancel': CTRL.cancel(source='telegram'); return '✅ Canvi pendent cancel·lat.'
        if cmd=='/undo': CTRL.undo(source='telegram'); return '↩️ Últim canvi desfet.'
        if cmd=='/set_open' and len(parts)>=2: return pending_change({'open_threshold':float(parts[1])}, f"OPEN threshold -> {parts[1]}")
        if cmd=='/set_probe' and len(parts)>=2: return pending_change({'probe_threshold':float(parts[1])}, f"PROBE threshold -> {parts[1]}")
        if cmd=='/risk' and len(parts)>=2: return pending_change({'risk_mult':float(parts[1])}, f"Risk multiplier -> {parts[1]}")
        if cmd=='/mode' and len(parts)>=2: return pending_change({'mode':parts[1].lower()}, f"Mode -> {parts[1].lower()}")
        if cmd=='/newtrades' and len(parts)>=2: return pending_change({'allow_new_trades': parts[1].lower() in ('on','true','1','si','sí','yes')}, f"New trades -> {parts[1]}")
        if cmd=='/long' and len(parts)>=2: return pending_change({'allow_long': parts[1].lower() in ('on','true','1','si','sí','yes')}, f"Long -> {parts[1]}")
        if cmd=='/short' and len(parts)>=2: return pending_change({'allow_short': parts[1].lower() in ('on','true','1','si','sí','yes')}, f"Short -> {parts[1]}")
        return 'Comanda no reconeguda. Envia /menu.'
    except Exception as e:
        return f"❌ No aplicat: {e}\nEnvia /settings per veure l’estat."

def main():
    if not token() or not chat_id(): raise SystemExit('telegram not configured')
    send('✅ JoanBot Command Center V24 actiu. Envia /menu.')
    offset=0
    while True:
        try:
            res=api('getUpdates', {'timeout':25, 'offset':offset+1})
            for u in res.get('result',[]):
                offset=max(offset,int(u.get('update_id',0)))
                msg=u.get('message') or {}; chat=str((msg.get('chat') or {}).get('id',''))
                if chat != str(chat_id()): continue
                text=msg.get('text') or ''
                if text: send(handle(text))
        except Exception:
            time.sleep(5)

if __name__=='__main__': main()
