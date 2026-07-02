from __future__ import annotations
from typing import Any, Dict, List
from ..utils import fnum, clamp

class LevelsEngine:
    def _vwap(self, candles: List[Dict[str, Any]]) -> float:
        num=0.0; den=0.0
        for c in candles:
            typical=(fnum(c.get('high'))+fnum(c.get('low'))+fnum(c.get('close')))/3
            qv=fnum(c.get('quote_volume')) or typical*fnum(c.get('volume'))
            vol=fnum(c.get('volume'))
            num += typical*vol; den += vol
        return num/den if den else 0.0
    def _profile(self, candles: List[Dict[str, Any]], bins: int=32) -> Dict[str, float]:
        if not candles: return {'poc':0,'vah':0,'val':0}
        lows=[fnum(c.get('low')) for c in candles]; highs=[fnum(c.get('high')) for c in candles]
        lo=min(lows); hi=max(highs)
        if hi<=lo: return {'poc':fnum(candles[-1].get('close')),'vah':hi,'val':lo}
        bucket=[0.0]*bins
        for c in candles:
            p=fnum(c.get('close')); v=fnum(c.get('volume')); idx=int(clamp((p-lo)/(hi-lo)*(bins-1),0,bins-1)); bucket[idx]+=v
        max_i=max(range(bins), key=lambda i: bucket[i]); poc=lo+(hi-lo)*(max_i/(bins-1))
        total=sum(bucket); target=total*0.70; order=sorted(range(bins), key=lambda i: bucket[i], reverse=True); acc=0; used=[]
        for i in order:
            acc+=bucket[i]; used.append(i)
            if acc>=target: break
        val=lo+(hi-lo)*(min(used)/(bins-1)); vah=lo+(hi-lo)*(max(used)/(bins-1))
        return {'poc':poc,'vah':vah,'val':val}
    def analyze(self, candles_by_tf: Dict[str, List[Dict[str, Any]]], price: float) -> Dict[str, Any]:
        c1h=candles_by_tf.get('1h',[]); c15=candles_by_tf.get('15m',[]); c1d=candles_by_tf.get('1d',[])
        day=c15[-96:] if len(c15)>=96 else c15; week=c1h[-168:] if len(c1h)>=168 else c1h; month=c1h[-720:] if len(c1h)>=720 else c1h
        vwap_d=self._vwap(day); vwap_w=self._vwap(week); vwap_m=self._vwap(month)
        # anchored VWAP from last 7d low/high proxy
        last7=c1h[-168:] if len(c1h)>=168 else c1h
        if last7:
            idx_low=min(range(len(last7)), key=lambda i:fnum(last7[i].get('low'))); idx_high=max(range(len(last7)), key=lambda i:fnum(last7[i].get('high')))
            avwap_low=self._vwap(last7[idx_low:]); avwap_high=self._vwap(last7[idx_high:])
        else:
            avwap_low=avwap_high=0
        cycles={}
        for name,n in [('24h',24),('3d',72),('7d',168),('30d',720),('90d',2160),('200d',4800)]:
            arr=c1h[-n:] if len(c1h)>=min(n,1) else c1h
            if arr:
                cycles[name]={'high':max(fnum(c.get('high')) for c in arr),'low':min(fnum(c.get('low')) for c in arr),'close_pos':(price-min(fnum(c.get('low')) for c in arr))/max(1e-9,(max(fnum(c.get('high')) for c in arr)-min(fnum(c.get('low')) for c in arr)))}
        prof=self._profile(day or c1h[-100:])
        distances={k:(price-v)/price*100 if price and v else 0 for k,v in {'vwap_d':vwap_d,'vwap_w':vwap_w,'vwap_m':vwap_m,'avwap_low':avwap_low,'avwap_high':avwap_high,'poc':prof['poc'],'vah':prof['vah'],'val':prof['val']}.items()}
        return {'vwap_d':vwap_d,'vwap_w':vwap_w,'vwap_m':vwap_m,'anchored_vwap_low':avwap_low,'anchored_vwap_high':avwap_high,'cycles':cycles,'volume_profile':prof,'distances_pct':distances}
