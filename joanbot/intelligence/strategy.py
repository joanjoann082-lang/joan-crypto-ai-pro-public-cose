from __future__ import annotations
from typing import Any, Dict, List
from ..models import Candidate
from ..utils import fnum, clamp

class StrategyEngine:
    def candidates(self, ctx: Dict[str, Any]) -> List[Candidate]:
        symbol=ctx['symbol']; price=fnum(ctx['price']); tech=ctx['technical']['timeframes']; lvl=ctx['levels']; flags=ctx['flags']; micro=ctx['micro']; der=ctx['derivatives']; macro=ctx['macro']; news=ctx['news']
        out=[]
        tf15=tech.get('15m',{}); tf1h=tech.get('1h',{}); tf4h=tech.get('4h',{})
        regime=ctx['regime']; atrp=max(0.25, fnum(tf1h.get('atr_pct'),1.0)); atr_abs=fnum(tf1h.get('atr')) or price*atrp/100
        mapa_causal=ctx.get('mapa_causal',{})
        def score_causal(side):
            return fnum(mapa_causal.get('score_long' if side=='LONG' else 'score_short'),0.0)
        def stops(side, mult=1.45):
            risk=atr_abs*mult
            if side=='LONG': return price-risk, price+risk*1.25, price+risk*2.25
            return price+risk, price-risk*1.25, price-risk*2.25
        # Trend pullback long
        bull_4h=fnum(tf4h.get('score'))>18; bull_1h=fnum(tf1h.get('score'))>8; pullback_15=fnum(tf15.get('score'))<8 and fnum(tf15.get('rsi'),50)<58
        raw=50 + fnum(tf4h.get('score'))*0.25 + fnum(tf1h.get('score'))*0.35 - max(0,fnum(tf15.get('rsi'))-68)*0.45 + clamp(score_causal('LONG'),-8,8)
        reason=[]
        if bull_4h: reason.append('4H_BULL')
        if abs(score_causal('LONG'))>=3: reason.append(f"CAUSAL_LONG_{score_causal('LONG'):.1f}")
        if bull_1h: reason.append('1H_BULL')
        if pullback_15: reason.append('15M_PULLBACK')
        if flags.get('late_long'): raw-=18; reason.append('LATE_LONG_RISK')
        if fnum(ctx['macro'].get('risk_score'),50)<40: raw-=8; reason.append('MACRO_RISK_OFF')
        if fnum(ctx['news'].get('severity'))>65: raw-=10; reason.append('NEWS_RISK')
        if bull_4h and bull_1h:
            sl,tp1,tp2=stops('LONG'); out.append(Candidate(symbol,'LONG','TREND_PULLBACK_LONG','SWING' if regime!='RANGE_CHOP' else 'SCALP',raw,raw*0.75,sl,tp1,tp2,reason,{}))
        # Reversal/capitulation long
        raw2=48 - fnum(tf15.get('score'))*0.15 - fnum(tf1h.get('score'))*0.10 + max(0,35-fnum(tf15.get('rsi'),50))*0.8 + max(0, fnum(der.get('long_liq_usd'))/1_000_000)*2 + clamp(score_causal('LONG')*0.7,-6,6)
        reason2=['CAPITULATION_LONG_CHECK']
        if abs(score_causal('LONG'))>=3: reason2.append(f"CAUSAL_LONG_{score_causal('LONG'):.1f}")
        if flags.get('late_short'): raw2+=12; reason2.append('LATE_SHORT_EXHAUSTION')
        if fnum(ctx['derivatives'].get('liq_imbalance'))>0.25: raw2+=8; reason2.append('SHORT_LIQ_PRESSURE')
        if fnum(tf15.get('rsi'))<34:
            sl,tp1,tp2=stops('LONG',1.25); out.append(Candidate(symbol,'LONG','CAPITULATION_REBOUND_LONG','SCALP',raw2,raw2*0.65,sl,tp1,tp2,reason2,{}))
        # Trend short
        bear_4h=fnum(tf4h.get('score'))<-18; bear_1h=fnum(tf1h.get('score'))<-8; bounce_15=fnum(tf15.get('score'))>-8 and fnum(tf15.get('rsi'),50)>42
        raw3=50 - fnum(tf4h.get('score'))*0.25 - fnum(tf1h.get('score'))*0.35 - max(0,32-fnum(tf15.get('rsi')))*0.4 + clamp(score_causal('SHORT'),-8,8)
        reason3=[]
        if bear_4h: reason3.append('4H_BEAR')
        if abs(score_causal('SHORT'))>=3: reason3.append(f"CAUSAL_SHORT_{score_causal('SHORT'):.1f}")
        if bear_1h: reason3.append('1H_BEAR')
        if bounce_15: reason3.append('15M_BOUNCE')
        if flags.get('late_short'): raw3-=18; reason3.append('LATE_SHORT_RISK')
        if fnum(ctx['macro'].get('risk_score'),50)<42: raw3+=6; reason3.append('MACRO_SUPPORTS_SHORT')
        if bear_4h and bear_1h:
            sl,tp1,tp2=stops('SHORT'); out.append(Candidate(symbol,'SHORT','TREND_BOUNCE_SHORT','SWING' if regime!='RANGE_CHOP' else 'SCALP',raw3,raw3*0.75,sl,tp1,tp2,reason3,{}))
        # Squeeze short/long from extremes
        if flags.get('squeeze_risk',0)>55:
            direction='SHORT' if fnum(tf15.get('rsi'))>68 else 'LONG' if fnum(tf15.get('rsi'))<32 else ''
            if direction:
                sl,tp1,tp2=stops(direction,1.15); out.append(Candidate(symbol,direction,f'SQUEEZE_REVERSAL_{direction}','SCALP',58+flags.get('squeeze_risk',0)*0.18,50,sl,tp1,tp2,[f'SQUEEZE_RISK_{flags.get("squeeze_risk",0):.0f}'],{}))
        return out
