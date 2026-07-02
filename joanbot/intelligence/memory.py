from __future__ import annotations
import json, math, time
from typing import Any, Dict, List, Tuple
from ..storage import get_db
from ..utils import fnum, wilson_lcb, clamp, utc_now_iso

SOURCE_WEIGHTS={'LIVE':1.0,'FORWARD':0.45,'SHADOW':0.25,'BACKTEST':0.12}
STATUS_MIN_N={'VALIDATED':30,'PROMISING':12,'PROBE_ONLY':6,'INSUFFICIENT':0}

class EdgeMemory:
    def __init__(self): self.db=get_db()
    def keys_for(self, symbol: str, side: str, setup: str, regime: str, session: str, vol: str, news: str) -> List[str]:
        return [
            f'SETUP|{symbol}|{side}|{setup}|{regime}|{session}|{vol}|{news}',
            f'SETUP|{symbol}|{side}|{setup}|{regime}|{session}',
            f'SETUP|{symbol}|{side}|{setup}|{regime}',
            f'SYM_SIDE_REGIME|{symbol}|{side}|{regime}',
            f'SYM_SIDE|{symbol}|{side}',
            f'SIDE_REGIME|{side}|{regime}',
            f'SIDE|{side}',
            'GLOBAL'
        ]
    def update(self, key: str, source: str, result_r: float, payload: Dict[str, Any]|None=None) -> None:
        source=source.upper(); payload=payload or {}
        row=self.db.query('SELECT * FROM edge_memory WHERE key=? AND source=?', (key, source))
        if row:
            r=row[0]; n=fnum(r['n'])+1; wins=fnum(r['wins'])+(1 if result_r>0 else 0); losses=fnum(r['losses'])+(1 if result_r<0 else 0); sum_r=fnum(r['sum_r'])+result_r; pos=fnum(r['sum_pos_r'])+max(0,result_r); neg=fnum(r['sum_neg_r'])+min(0,result_r); maxdd=min(fnum(r['max_dd_r']), result_r)
            self.db.execute('UPDATE edge_memory SET updated_at=?, n=?, wins=?, losses=?, sum_r=?, sum_pos_r=?, sum_neg_r=?, max_dd_r=?, payload=? WHERE key=? AND source=?', (utc_now_iso(), n,wins,losses,sum_r,pos,neg,maxdd,json.dumps(payload),key,source))
        else:
            self.db.execute('INSERT OR REPLACE INTO edge_memory(key,source,updated_at,n,wins,losses,sum_r,sum_pos_r,sum_neg_r,max_dd_r,payload) VALUES(?,?,?,?,?,?,?,?,?,?,?)', (key,source,utc_now_iso(),1,1 if result_r>0 else 0,1 if result_r<0 else 0,result_r,max(0,result_r),min(0,result_r),min(0,result_r),json.dumps(payload)))
    def update_many(self, keys: List[str], source: str, result_r: float, payload: Dict[str, Any]|None=None) -> None:
        for k in keys: self.update(k, source, result_r, payload)
    def _combined(self, key: str) -> Dict[str, float]:
        rows=self.db.query('SELECT * FROM edge_memory WHERE key=?', (key,))
        n=wins=losses=sum_r=pos=neg=0.0
        for r in rows:
            weight=SOURCE_WEIGHTS.get(str(r['source']).upper(),0.1)
            n += fnum(r['n'])*weight; wins += fnum(r['wins'])*weight; losses += fnum(r['losses'])*weight; sum_r += fnum(r['sum_r'])*weight; pos += fnum(r['sum_pos_r'])*weight; neg += fnum(r['sum_neg_r'])*weight
        pf=pos/abs(neg) if neg<0 else (999 if pos>0 else 0)
        wr=wins/max(1e-9,wins+losses); exp=sum_r/max(1e-9,n); lcb=wilson_lcb(wins, wins+losses) if (wins+losses)>0 else 0
        return {'key':key,'effective_n':n,'wins':wins,'losses':losses,'winrate':wr,'expectancy_r':exp,'profit_factor':pf,'lcb':lcb,'sum_r':sum_r}
    def view(self, keys: List[str]) -> Dict[str, Any]:
        candidates=[self._combined(k) for k in keys]
        # Prefer more specific keys only if evidence enough. fallback hierarchical.
        chosen=None
        for c in candidates:
            if c['effective_n']>=8:
                chosen=c; break
        if chosen is None: chosen=max(candidates, key=lambda c:c['effective_n']) if candidates else {'key':'NONE','effective_n':0,'winrate':0.5,'expectancy_r':0,'profit_factor':0,'lcb':0}
        n=chosen['effective_n']; exp=chosen['expectancy_r']; pf=chosen['profit_factor']; lcb=chosen['lcb']
        reasons=[]; status='INSUFFICIENT'; adj=0.0; mult=0.75
        if n<6:
            status='INSUFFICIENT'; adj=-3; mult=0.55; reasons.append('LOW_EDGE_SAMPLE')
        elif exp>0.12 and pf>1.18 and lcb>0.43:
            status='PROMISING'; adj=6; mult=0.85; reasons.append('PROMISING_EDGE')
        elif n>=30 and exp>0.08 and pf>1.25 and lcb>0.46:
            status='VALIDATED'; adj=12; mult=1.0; reasons.append('VALIDATED_EDGE')
        elif exp<-0.08 and pf<0.85 and n>=12:
            status='NEGATIVE'; adj=-18; mult=0.35; reasons.append('NEGATIVE_EDGE')
        else:
            status='MIXED'; adj=-2; mult=0.65; reasons.append('MIXED_EDGE')
        return {**chosen,'status':status,'score_adjustment':adj,'size_multiplier':mult,'reasons':reasons,'all_candidates':candidates[:5]}
    def top(self, limit:int=20) -> List[Dict[str,Any]]:
        rows=self.db.query('SELECT DISTINCT key FROM edge_memory LIMIT 5000')
        views=[self._combined(r['key']) for r in rows]
        views.sort(key=lambda x:(x['expectancy_r'], x['profit_factor'], x['effective_n']), reverse=True)
        return views[:limit]
