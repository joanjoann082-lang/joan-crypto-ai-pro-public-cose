from __future__ import annotations
from typing import Any, Dict, List
import time
from ..utils import fnum, clamp, utc_ms

class DataQualityEngine:
    def evaluate(self, snap: Dict[str, Any]) -> Dict[str, Any]:
        score=100.0; issues=[]
        price=fnum(snap.get('price'))
        if price<=0: score-=50; issues.append('NO_PRICE')
        candles=snap.get('candles',{}) if isinstance(snap.get('candles'),dict) else {}
        for itv in ['1m','5m','15m','1h']:
            cs=candles.get(itv,[])
            if len(cs)<50: score-=8; issues.append(f'LOW_CANDLES_{itv}')
            if cs:
                last=cs[-1]; age=(utc_ms()-int(last.get('close_time') or last.get('open_time') or 0))/1000.0
                max_age={'1m':180,'5m':600,'15m':1600,'1h':4300}.get(itv,9999)
                if age>max_age: score-=12; issues.append(f'STALE_{itv}')
                corrupt=sum(1 for c in cs[-50:] if fnum(c.get('high')) < fnum(c.get('low')) or fnum(c.get('close'))<=0)
                if corrupt: score-=min(20, corrupt*4); issues.append(f'CORRUPT_{itv}_{corrupt}')
        ob=snap.get('orderbook',{})
        if fnum(ob.get('depth_25bps'))<=0: score-=15; issues.append('NO_DEPTH')
        if fnum(ob.get('spread_bps'))>10: score-=15; issues.append('WIDE_SPREAD')
        der=snap.get('derivatives',{})
        if fnum(der.get('open_interest'))<=0: score-=8; issues.append('NO_OI')
        return {'score':clamp(score,0,100),'issues':issues,'hard_ok':score>=55 and 'NO_PRICE' not in issues,'stale':any(x.startswith('STALE') for x in issues)}
