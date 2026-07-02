from __future__ import annotations
import hashlib, time, json, urllib.parse, urllib.request, os, sqlite3
from pathlib import Path
from typing import Any, Dict
from ..config import CFG, load_env
from ..storage import get_db
from ..utils import utc_now_iso, fnum

STATE=Path('data/telegram_alert_state.json')
DB=Path('data/joanbot_v14.sqlite')

class AlertEngine:
    def __init__(self):
        self.db=get_db(); load_env()

    def _state(self):
        try: return json.loads(STATE.read_text(errors='ignore'))
        except Exception: return {}

    def _save_state(self,s):
        STATE.parent.mkdir(parents=True, exist_ok=True)
        tmp=STATE.with_suffix('.tmp')
        tmp.write_text(json.dumps(s, indent=2, sort_keys=True), encoding='utf-8')
        tmp.replace(STATE)

    def _dedup(self, key: str, ttl: int=900) -> bool:
        st=self._state(); now=time.time(); old=float(st.get(key,0) or 0)
        if now-old<ttl: return False
        st[key]=now; self._save_state(st); return True

    def telegram(self, text: str) -> bool:
        token=os.getenv('TELEGRAM_BOT_TOKEN','').strip(); chat=os.getenv('TELEGRAM_CHAT_ID','').strip()
        enabled=os.getenv('TELEGRAM_ENABLED','true').lower() in ('1','true','yes','on')
        if not token or not chat or not enabled: return False
        try:
            url=f'https://api.telegram.org/bot{token}/sendMessage'
            data=urllib.parse.urlencode({'chat_id':chat,'text':text[:3500],'disable_web_page_preview':'true'}).encode()
            urllib.request.urlopen(urllib.request.Request(url,data=data), timeout=12).read(); return True
        except Exception:
            return False

    def emit(self, severity: str, kind: str, symbol: str, text: str, payload: Dict[str,Any]|None=None, ttl:int=900, dedup_key: str|None=None) -> bool:
        key=dedup_key or hashlib.sha1(f'{kind}|{symbol}|{text[:160]}'.encode()).hexdigest()
        if not self._dedup(key,ttl): return False
        payload=payload or {}; event={'ts':utc_now_iso(),'severity':severity,'kind':kind,'symbol':symbol,'text':text,'payload':payload,'dedup_key':key}
        try:
            self.db.insert_json('alerts', event, {'ts':event['ts'],'severity':severity,'kind':kind,'symbol':symbol,'dedup_key':key, 'text': text[:1000]})
        except TypeError:
            self.db.insert_json('alerts', event, {'ts':event['ts'],'severity':severity,'kind':kind,'symbol':symbol,'dedup_key':key})
        if CFG.telegram_enabled:
            self.telegram(text)
        return True

    def evaluate_decision(self, d) -> None:
        if d.action=='OPEN':
            msg=(f"🚨 TRADE POSSIBLE / OPEN\n\n{d.symbol} {d.side}\nScore: {d.final_score:.1f}/100 · Confiança: {d.confidence:.0f}/100\nSetup: {d.setup}\nSize paper: ${d.size_usd:,.0f}\nEntry: ${d.entry:,.2f}\nSL: ${d.stop_loss:,.2f}\nTP1: ${d.take_profit_1:,.2f}\nTP2: ${d.take_profit_2:,.2f}\n\nPer què: {', '.join(d.reasons[:5])}\n\nAcció: revisar dashboard abans d'actuar en real.")
            self.emit('HIGH','OPEN',d.symbol,msg,d.to_dict(),3600,f"OPEN|{d.symbol}|{d.side}|{d.setup}")
        elif d.action=='PROBE' and d.final_score>=max(CFG.high_quality_probe_threshold, 68):
            msg=(f"👀 TRADE A PROP\n\n{d.symbol} {d.side}\nScore: {d.final_score:.1f}/100\nSetup: {d.setup}\nEstat: PROBE, encara no OPEN.\n\nFalta: edge/risc/confirmació.\nPer què: {', '.join(d.reasons[:5])}")
            self.emit('MEDIUM','TRADE_FORMING',d.symbol,msg,d.to_dict(),3600,f"PROBE|{d.symbol}|{d.side}|{d.setup}")

    def evaluate_global(self, snap: Dict[str, Any]) -> None:
        news=(snap or {}).get('news') or {}
        macro=(snap or {}).get('macro') or {}
        sev=fnum(news.get('severity'))
        direction=str(news.get('direction') or 'UNKNOWN')
        if sev>=85 and direction!='UNKNOWN':
            title=''
            evs=news.get('events') or []
            if evs and isinstance(evs[0],dict): title=str(evs[0].get('title',''))[:180]
            msg=(f"📰 EVENT RISK ALT\n\nSeveritat: {sev:.0f}/100\nDirecció: {direction}\nMacro risk: {fnum(macro.get('risk_score'),50):.0f}/100\n\nNotícia principal:\n{title or 'Sense titular principal'}\n\nEfecte: no obre trades per notícia; només prudència contextual.")
            self.emit('HIGH','NEWS_HIGH','GLOBAL',msg,news,7200,f"NEWS|{direction}|{int(sev//5)*5}")

    def evaluate_advisor(self) -> bool:
        key='ADVISOR_V24'
        if not self._dedup(key,7200): return False
        try:
            con=sqlite3.connect(DB); con.row_factory=sqlite3.Row
            rows=con.execute('SELECT result_r FROM forward_results ORDER BY id DESC LIMIT 600').fetchall()
            con.close()
        except Exception:
            return False
        vals=[]
        for r in rows:
            try: vals.append(float(r['result_r']))
            except Exception: pass
        if len(vals)<200: return False
        avg=sum(vals)/len(vals); wr=sum(1 for x in vals if x>0)/len(vals)*100
        if avg<-0.12 or wr<43:
            msg=(f"⚠️ RECOMANACIÓ DEL BOT\n\nEdge recent feble.\nMostra: {len(vals)} forward cases\nWinrate: {wr:.1f}%\nAvg R: {avg:.3f}\n\nAcció recomanada:\n/set_open 78\n/confirm\n\nAlternativa:\n/risk 0.5\n/confirm")
            return self.telegram(msg)
        if avg>0.15 and wr>56:
            msg=(f"🟢 RECOMANACIÓ DEL BOT\n\nEdge recent positiu.\nMostra: {len(vals)} forward cases\nWinrate: {wr:.1f}%\nAvg R: {avg:.3f}\n\nOpció prudent: mantenir.\nOpció agressiva controlada:\n/set_open 72\n/confirm")
            return self.telegram(msg)
        return False
