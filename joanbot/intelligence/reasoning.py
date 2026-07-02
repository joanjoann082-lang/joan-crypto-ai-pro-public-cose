from __future__ import annotations
from typing import Any, Dict, List
from ..utils import fnum, clamp

class ReasoningEngine:
    """Structured reasoning layer. It does not decide; it explains conflicts and builds trade plans.

    Contract:
      - StrategyEngine proposes raw candidates.
      - DecisionKernel decides final action.
      - ReasoningEngine turns scores/context into auditable explanations and plan constraints.
    """
    def conflict_matrix(self, ctx: Dict[str, Any], side: str) -> Dict[str, Any]:
        tf=ctx.get('technical',{}).get('timeframes',{})
        flags=ctx.get('flags',{})
        der=ctx.get('derivatives',{})
        micro=ctx.get('micro',{})
        macro=ctx.get('macro',{})
        news=ctx.get('news',{})
        conflicts=[]; supports=[]; warnings=[]
        s15=fnum(tf.get('15m',{}).get('score')); s1h=fnum(tf.get('1h',{}).get('score')); s4h=fnum(tf.get('4h',{}).get('score')); sd=fnum(tf.get('1d',{}).get('score'))
        if side=='LONG':
            if s4h>20: supports.append('4H_SUPPORTS_LONG')
            if s1h>10: supports.append('1H_SUPPORTS_LONG')
            if s15<-15: supports.append('15M_PULLBACK_LONG_ENTRY_ZONE')
            if s4h<-20: conflicts.append('4H_AGAINST_LONG')
            if flags.get('late_long'): conflicts.append('LATE_LONG_EXHAUSTION')
            if fnum(der.get('long_short_ratio'),1)>1.8: warnings.append('CROWD_LONG_HEAVY')
        if side=='SHORT':
            if s4h<-20: supports.append('4H_SUPPORTS_SHORT')
            if s1h<-10: supports.append('1H_SUPPORTS_SHORT')
            if s15>15: supports.append('15M_BOUNCE_SHORT_ENTRY_ZONE')
            if s4h>20: conflicts.append('4H_AGAINST_SHORT')
            if flags.get('late_short'): conflicts.append('LATE_SHORT_EXHAUSTION')
            if fnum(der.get('long_short_ratio'),1)<0.65: warnings.append('CROWD_SHORT_HEAVY')
        if fnum(news.get('severity'))>=70: conflicts.append('HIGH_NEWS_EVENT_RISK')
        elif fnum(news.get('severity'))>=35: warnings.append('MEDIUM_NEWS_EVENT_RISK')
        if fnum(macro.get('risk_score'),50)<38 and side=='LONG': conflicts.append('MACRO_RISK_OFF_AGAINST_LONG')
        if fnum(macro.get('risk_score'),50)>66 and side=='SHORT': conflicts.append('MACRO_RISK_ON_AGAINST_SHORT')
        if fnum(micro.get('liquidity_score'),50)<45: conflicts.append('LOW_LIQUIDITY_EXECUTION_RISK')
        if fnum(ctx.get('data_quality',{}).get('score'),100)<65: conflicts.append('LOW_DATA_QUALITY')
        return {'supports':supports,'conflicts':conflicts,'warnings':warnings,'tf_scores':{'15m':s15,'1h':s1h,'4h':s4h,'1d':sd}}

    def plan(self, candidate, ctx: Dict[str, Any], edge: Dict[str, Any], risk: Dict[str, Any], layers: Dict[str, float]) -> Dict[str, Any]:
        price=fnum(ctx.get('price')); stop=fnum(candidate.invalidation); tp1=fnum(candidate.tp1); tp2=fnum(candidate.tp2)
        stop_pct=abs(price-stop)/price*100 if price and stop else 0
        rr1=abs(tp1-price)/abs(price-stop) if price and stop else 0
        rr2=abs(tp2-price)/abs(price-stop) if price and stop else 0
        cm=self.conflict_matrix(ctx, candidate.side)
        prerequisites=[]
        if candidate.side=='LONG':
            prerequisites=['price holds above invalidation','15m momentum stops deteriorating','spread remains acceptable']
        else:
            prerequisites=['retest fails below resistance/VWAP','15m bounce loses momentum','spread remains acceptable']
        if ctx.get('news_bucket')!='LOW': prerequisites.append('no new high severity news during entry window')
        invalidation=[f'{candidate.side} invalid if price reaches {stop:.2f}', 'data quality hard fail', 'macro/news risk flips against trade']
        management=['TP1 partial close 35%', 'move stop to break-even after TP1', 'activate trailing/profit lock after 0.7R', 'giveback protection if MFE collapses']
        confidence_notes=[]
        if edge.get('status') in ('VALIDATED','PROMISING'): confidence_notes.append('edge memory supports setup')
        else: confidence_notes.append('edge not fully validated; keep probe/canary size')
        if cm['conflicts']: confidence_notes.append('conflicts: '+', '.join(cm['conflicts'][:4]))
        return {'entry':price,'stop_loss':stop,'tp1':tp1,'tp2':tp2,'stop_pct':stop_pct,'rr1':rr1,'rr2':rr2,'prerequisites':prerequisites,'invalidation':invalidation,'management':management,'conflict_matrix':cm,'confidence_notes':confidence_notes,'score_layers':layers}

    def human_summary(self, decision: Dict[str, Any]) -> str:
        fs=decision.get('feature_summary',{}); layers=decision.get('score_layers',{}); plan=decision.get('trade_plan',{})
        return (f"{decision.get('symbol')} {decision.get('side')} {decision.get('action')} score={decision.get('final_score'):.1f}\n"
                f"Regime={fs.get('regime')} Session={fs.get('session')} News={fs.get('news_bucket')} DQ={fs.get('dq')}\n"
                f"Layers alpha={layers.get('alpha'):.1f} timing={layers.get('timing'):.1f} execution={layers.get('execution'):.1f} edge={layers.get('edge'):.1f}\n"
                f"Plan SL={plan.get('stop_loss')} TP1={plan.get('tp1')} TP2={plan.get('tp2')} RR1={plan.get('rr1'):.2f}")
