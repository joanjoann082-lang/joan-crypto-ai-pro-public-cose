from __future__ import annotations
from typing import Any, Dict, List
from ..config import CFG
from ..utils import fnum, clamp

class RiskEngine:
    def wallet_metrics(self, wallet: Dict[str, Any]) -> Dict[str, float]:
        equity=fnum(wallet.get('equity'), CFG.initial_equity); opens=wallet.get('open',[])
        total=sum(fnum(p.get('size_usd')) for p in opens if isinstance(p,dict)); by_sym={}; by_side={}
        for p in opens if isinstance(opens,list) else []:
            by_sym[p.get('symbol')]=by_sym.get(p.get('symbol'),0)+fnum(p.get('size_usd')); by_side[p.get('side')]=by_side.get(p.get('side'),0)+fnum(p.get('size_usd'))
        return {'equity':equity,'total_exposure':total,'by_symbol':by_sym,'by_side':by_side,'open_n':len(opens)}
    def size(self, candidate, ctx: Dict[str, Any], edge: Dict[str, Any], wallet: Dict[str, Any]) -> Dict[str, Any]:
        m=self.wallet_metrics(wallet); equity=m['equity']; price=fnum(ctx['price']); stop=fnum(candidate.invalidation); stop_pct=abs(price-stop)/price if price and stop else 0
        reasons=[]
        if stop_pct<=0.001: return {'allowed':False,'size_usd':0,'risk_usd':0,'risk_pct':0,'leverage':0,'stop_pct':stop_pct,'size_multiplier':0,'reasons':['STOP_TOO_CLOSE_OR_INVALID']}
        risk_pct=CFG.base_risk_pct
        # quality multipliers
        q=fnum(ctx['data_quality'].get('score'),50)/100; liq=fnum(ctx['micro'].get('liquidity_score'),50)/100; macro=fnum(ctx['macro'].get('risk_score'),50)/100; news_sev=fnum(ctx['news'].get('severity'))
        edge_mult=fnum(edge.get('size_multiplier'),0.6); edge_exp=fnum(edge.get('expectancy_r'))
        if edge_exp>0.15: risk_pct*=1.18
        elif edge_exp<0: risk_pct*=0.70
        risk_pct *= clamp(q,0.35,1.05) * clamp(liq,0.40,1.10) * clamp(0.70+macro*0.55,0.45,1.10) * edge_mult
        if news_sev>=70: risk_pct*=CFG.event_risk_size_floor; reasons.append('HIGH_NEWS_EVENT_RISK_SIZE_REDUCED')
        if candidate.trade_type=='SCALP': risk_pct*=0.75
        if edge.get('status') in ('INSUFFICIENT','MIXED'): risk_pct=min(risk_pct, CFG.probe_risk_pct*1.5); reasons.append('EDGE_NOT_VALIDATED_PROBE_SIZE')
        risk_pct=clamp(risk_pct, 0.00025, CFG.max_risk_pct)
        theoretical=equity*risk_pct/stop_pct
        caps=[]
        caps.append(equity*CFG.max_total_exposure_pct - m['total_exposure'])
        caps.append(equity*CFG.max_symbol_exposure_pct - m['by_symbol'].get(candidate.symbol,0))
        caps.append(equity*CFG.max_side_exposure_pct - m['by_side'].get(candidate.side,0))
        cap=max(0,min(caps))
        size=max(0,min(theoretical,cap))
        if m['open_n']>=CFG.max_positions: size=0; reasons.append('MAX_POSITIONS')
        sym_count=sum(1 for p in wallet.get('open',[]) if isinstance(p,dict) and p.get('symbol')==candidate.symbol)
        if sym_count>=CFG.max_per_symbol: size=0; reasons.append('MAX_SYMBOL_POSITIONS')
        if size<CFG.min_notional and size>0:
            reasons.append('BELOW_MIN_USEFUL_NOTIONAL')
            size=0
        risk_usd=size*stop_pct; lev=size/equity if equity else 0
        allowed=size>0
        return {'allowed':allowed,'size_usd':size,'risk_usd':risk_usd,'risk_pct':risk_usd/equity if equity else 0,'leverage':lev,'stop_pct':stop_pct,'size_multiplier':size/theoretical if theoretical else 0,'reasons':reasons}
