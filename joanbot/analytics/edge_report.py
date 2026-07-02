from __future__ import annotations
import json, math
from collections import Counter, defaultdict
from typing import Any, Dict, List
from ..storage import get_db
from ..utils import fnum

class EdgeReport:
    def __init__(self): self.db=get_db()
    def decisions_summary(self, limit:int=2000) -> Dict[str, Any]:
        rows=self.db.query('SELECT * FROM decisions ORDER BY id DESC LIMIT ?', (limit,))
        actions=Counter(r['action'] for r in rows); symbols=Counter(r['symbol'] for r in rows); setups=Counter(r['setup'] for r in rows)
        blockers=Counter()
        scores=[]
        for r in rows:
            scores.append(fnum(r.get('final_score')))
            try: payload=json.loads(r.get('payload') or '{}')
            except Exception: payload={}
            for reason in payload.get('reasons',[])[:12]:
                blockers[str(reason)] += 1
        return {'n':len(rows),'actions':dict(actions),'symbols':dict(symbols),'setups':dict(setups.most_common(10)),'avg_score':sum(scores)/max(1,len(scores)),'top_reasons':dict(blockers.most_common(12))}
    def trades_summary(self) -> Dict[str, Any]:
        rows=self.db.query('SELECT * FROM trades ORDER BY id DESC LIMIT 5000')
        pnl=[fnum(r['pnl_usd']) for r in rows]
        wins=[x for x in pnl if x>0]; losses=[x for x in pnl if x<0]
        pf=sum(wins)/abs(sum(losses)) if losses else (999 if wins else 0)
        by=defaultdict(list)
        for r,x in zip(rows,pnl): by[f"{r['symbol']}|{r['side']}|{r['setup']}"].append(x)
        groups=[]
        for k,arr in by.items():
            if len(arr)>=2: groups.append({'key':k,'n':len(arr),'pnl':sum(arr),'expectancy':sum(arr)/len(arr),'winrate':sum(1 for a in arr if a>0)/len(arr)*100})
        groups.sort(key=lambda x:x['expectancy'], reverse=True)
        return {'n':len(rows),'pnl':sum(pnl),'winrate':len(wins)/max(1,len(wins)+len(losses))*100,'profit_factor':pf,'expectancy':sum(pnl)/max(1,len(pnl)),'best':groups[:8],'worst':list(reversed(groups[-8:]))}
    def forward_summary(self) -> Dict[str, Any]:
        rows=self.db.query('SELECT * FROM forward_results ORDER BY id DESC LIMIT 5000')
        vals=[fnum(r['result_r']) for r in rows]
        return {'n':len(rows),'avg_r':sum(vals)/max(1,len(vals)),'winrate':sum(1 for v in vals if v>0)/max(1,sum(1 for v in vals if v!=0))*100,'tp':sum(1 for r in rows if r['outcome'] in ('TP1','TP2')),'sl':sum(1 for r in rows if r['outcome']=='SL')}
    def full(self) -> Dict[str, Any]: return {'decisions':self.decisions_summary(),'trades':self.trades_summary(),'forward':self.forward_summary(),'db':self.db.state()}
