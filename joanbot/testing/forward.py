from __future__ import annotations
import uuid, json, time
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List
from ..storage import get_db
from ..utils import fnum, utc_now_iso

class ForwardTester:
    def __init__(self): self.db=get_db()
    def register(self, decision: Dict[str, Any], horizons=(15,60,240)) -> None:
        for h in horizons:
            cid=str(uuid.uuid4())[:14]
            due=(datetime.now(timezone.utc)+timedelta(minutes=h)).isoformat()
            payload={'decision':decision,'horizon_min':h}
            self.db.execute('INSERT OR REPLACE INTO forward_cases(id,created_at,due_at,horizon_min,symbol,side,action,setup,entry,sl,tp1,tp2,status,payload) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)', (cid,utc_now_iso(),due,h,decision.get('symbol'),decision.get('side'),decision.get('action'),decision.get('setup'),decision.get('entry'),decision.get('stop_loss'),decision.get('take_profit_1'),decision.get('take_profit_2'),'PENDING',json.dumps(payload,sort_keys=True)))
    def resolve_due(self) -> List[Dict[str, Any]]:
        now=utc_now_iso(); rows=self.db.query("SELECT * FROM forward_cases WHERE status='PENDING' AND due_at<=? LIMIT 200", (now,))
        out=[]
        for r in rows:
            symbol=r['symbol']; h=int(r['horizon_min']); entry=fnum(r['entry']); sl=fnum(r['sl']); tp1=fnum(r['tp1']); tp2=fnum(r['tp2']); side=r['side']
            candles=self.db.latest_candles(symbol,'1m',max(5,h+5))
            # use last h candles path approximation
            path=candles[-h:] if len(candles)>=h else candles
            outcome='TIME'; mfe=mae=result=0.0; risk=abs(entry-sl) or 1
            for c in path:
                high=fnum(c.get('high')); low=fnum(c.get('low'))
                if side=='LONG':
                    mfe=max(mfe,(high-entry)/risk); mae=min(mae,(low-entry)/risk)
                    if low<=sl: outcome='SL'; result=-1.0; break
                    if high>=tp2: outcome='TP2'; result=2.0; break
                    if high>=tp1 and result<1.0: outcome='TP1'; result=1.0
                else:
                    mfe=max(mfe,(entry-low)/risk); mae=min(mae,(entry-high)/risk)
                    if high>=sl: outcome='SL'; result=-1.0; break
                    if low<=tp2: outcome='TP2'; result=2.0; break
                    if low<=tp1 and result<1.0: outcome='TP1'; result=1.0
            if outcome=='TIME' and path:
                close=fnum(path[-1].get('close')); result=((close-entry)/risk if side=='LONG' else (entry-close)/risk)
            res={'case_id':r['id'],'resolved_at':now,'symbol':symbol,'outcome':outcome,'result_r':result,'mfe_r':mfe,'mae_r':mae,'horizon_min':h,'action':r['action'],'setup':r['setup'],'side':side}
            self.db.execute('UPDATE forward_cases SET status=? WHERE id=?', ('RESOLVED',r['id']))
            self.db.insert_json('forward_results', res, {'case_id':r['id'],'resolved_at':now,'symbol':symbol,'outcome':outcome,'result_r':result,'mfe_r':mfe,'mae_r':mae})
            out.append(res)
        return out
    def report(self, limit:int=500) -> Dict[str, Any]:
        rows=self.db.query('SELECT * FROM forward_results ORDER BY id DESC LIMIT ?', (limit,))
        if not rows: return {'n':0}
        vals=[fnum(r['result_r']) for r in rows]; good=sum(1 for v in vals if v>0); bad=sum(1 for v in vals if v<0)
        by_action={}
        for r in rows:
            p=json.loads(r.get('payload') or '{}') if r.get('payload') else {}
        return {'n':len(rows),'avg_r':sum(vals)/len(vals),'winrate':good/max(1,good+bad)*100,'tp_rate':sum(1 for r in rows if r['outcome'] in ('TP1','TP2'))/len(rows)*100,'sl_rate':sum(1 for r in rows if r['outcome']=='SL')/len(rows)*100}
