from __future__ import annotations
import json
from collections import Counter, defaultdict
from typing import Any, Dict, List
from ..storage import get_db
from ..utils import fnum

class MissedOpportunityAnalyzer:
    """Analyzes whether WAIT/PROBE/OPEN decisions were justified after forward resolution.

    It separates:
      - good blocks: WAIT that would have lost
      - bad waits: WAIT that reached TP-like result
      - good probes: PROBE with positive forward R
      - bad probes: PROBE with negative forward R
    """
    def __init__(self): self.db=get_db()
    def _case_payload(self, case_id: str) -> Dict[str,Any]:
        rows=self.db.query('SELECT payload FROM forward_cases WHERE id=?', (case_id,))
        if not rows: return {}
        try: return json.loads(rows[0]['payload'])
        except Exception: return {}
    def analyze(self, limit:int=1000) -> Dict[str,Any]:
        rows=self.db.query('SELECT * FROM forward_results ORDER BY id DESC LIMIT ?', (limit,))
        summary=Counter(); by_reason=Counter(); by_setup=defaultdict(list); examples=[]
        for r in rows:
            payload=self._case_payload(r['case_id']); d=payload.get('decision',{}) if isinstance(payload,dict) else {}
            action=d.get('action'); result=fnum(r.get('result_r')); setup=d.get('setup','UNKNOWN'); symbol=d.get('symbol','?'); side=d.get('side','?')
            if action=='WAIT' and result>0.75: k='BAD_WAIT_MISSED_PROFIT'
            elif action=='WAIT' and result<0: k='GOOD_WAIT_AVOIDED_LOSS'
            elif action=='PROBE' and result>0: k='GOOD_PROBE'
            elif action=='PROBE' and result<0: k='BAD_PROBE'
            elif action=='OPEN' and result>0: k='GOOD_OPEN'
            elif action=='OPEN' and result<0: k='BAD_OPEN'
            else: k='NEUTRAL'
            summary[k]+=1; by_setup[f'{symbol}|{side}|{setup}'].append(result)
            if k in ('BAD_WAIT_MISSED_PROFIT','BAD_PROBE','BAD_OPEN') and len(examples)<12:
                examples.append({'case_id':r['case_id'],'class':k,'symbol':symbol,'side':side,'setup':setup,'result_r':result,'score':d.get('final_score'),'reasons':d.get('reasons',[])[:8]})
            for reason in d.get('reasons',[])[:12]:
                if k=='BAD_WAIT_MISSED_PROFIT': by_reason[str(reason)]+=1
        setups=[]
        for k,arr in by_setup.items():
            if len(arr)>=3: setups.append({'key':k,'n':len(arr),'avg_r':sum(arr)/len(arr),'winrate':sum(1 for x in arr if x>0)/len(arr)*100})
        setups.sort(key=lambda x:x['avg_r'])
        return {'n':len(rows),'summary':dict(summary),'bad_wait_blockers':dict(by_reason.most_common(12)),'worst_setups':setups[:8],'best_setups':list(reversed(setups[-8:])),'examples':examples}
