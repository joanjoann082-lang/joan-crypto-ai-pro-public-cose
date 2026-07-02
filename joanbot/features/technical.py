from __future__ import annotations
from typing import Any, Dict, List
from ..utils import fnum, ema, rsi, atr, pct, clamp

class TechnicalEngine:
    def analyze_tf(self, candles: List[Dict[str, Any]]) -> Dict[str, Any]:
        closes=[fnum(c.get('close')) for c in candles]
        highs=[fnum(c.get('high')) for c in candles]; lows=[fnum(c.get('low')) for c in candles]; vols=[fnum(c.get('volume')) for c in candles]
        if len(closes)<30: return {'state':'NO_DATA','score':0,'rsi':50,'atr_pct':0,'ema20':0,'ema50':0,'ema200':0,'ret':0,'vol_ratio':1}
        price=closes[-1]; e20=ema(closes,20); e50=ema(closes,50); e200=ema(closes,200 if len(closes)>=200 else len(closes))
        rs=rsi(closes,14); a=atr(candles,14); atr_pct=a/price*100 if price else 0
        ret=pct(price, closes[-25] if len(closes)>=25 else closes[0])
        vol_base=sum(vols[-31:-1])/max(1,len(vols[-31:-1])) if len(vols)>31 else (sum(vols)/max(1,len(vols)))
        vol_ratio=vols[-1]/vol_base if vol_base else 1
        score=0
        score += 16 if price>e20 else -16; score += 20 if price>e50 else -20; score += 18 if price>e200 else -18
        score += 10 if e20>e50 else -10; score += 8 if ret>0 else -8
        if 45<=rs<=62: score+=6
        elif rs>75: score-=8
        elif rs<28: score+=3
        if vol_ratio>1.5: score+=4
        state='BULL' if score>=30 else 'BEAR' if score<=-30 else 'RANGE'
        return {'state':state,'score':score,'price':price,'ema20':e20,'ema50':e50,'ema200':e200,'rsi':rs,'atr':a,'atr_pct':atr_pct,'ret':ret,'vol_ratio':vol_ratio}

    def analyze(self, candles_by_tf: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Any]:
        out={tf:self.analyze_tf(cs) for tf,cs in candles_by_tf.items()}
        weights={'1m':0.08,'5m':0.12,'15m':0.20,'1h':0.30,'4h':0.20,'1d':0.10}
        composite=sum(out.get(tf,{}).get('score',0)*w for tf,w in weights.items())
        regime='TRENDING_BULL' if composite>=24 else 'TRENDING_BEAR' if composite<=-24 else 'RANGE_CHOP'
        conflicts=[]
        if out.get('4h',{}).get('state')=='BULL' and out.get('15m',{}).get('state')=='BEAR': conflicts.append('4H_BULL_15M_PULLBACK')
        if out.get('4h',{}).get('state')=='BEAR' and out.get('15m',{}).get('state')=='BULL': conflicts.append('4H_BEAR_15M_BOUNCE')
        return {'timeframes':out,'composite_score':composite,'regime':regime,'conflicts':conflicts}
