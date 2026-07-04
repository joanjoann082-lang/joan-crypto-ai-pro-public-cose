from __future__ import annotations
from typing import Any, Dict, List
from ..config import CFG
from ..models import Decision
from ..utils import fnum, clamp
from .memory import EdgeMemory
from .strategy import StrategyEngine
from .risk import RiskEngine
from .reasoning import ReasoningEngine
from .statistical_edge_authority_v1 import StatisticalEdgeAuthorityV1
from ..ops.runtime_control import RuntimeControl
from ..institutional.nucli_quantitatiu_net import get_core as NucliQuantitatiuNet

class DecisionKernel:
    def __init__(self):
        self.strategy=StrategyEngine(); self.memory=EdgeMemory(); self.risk=RiskEngine(); self.reasoning=ReasoningEngine(); self.control=RuntimeControl(); self.stat_edge=StatisticalEdgeAuthorityV1(); self.nucli_quant=NucliQuantitatiuNet(self.memory.db)

    def _edge_keys(self, cand, ctx):
        return self.memory.keys_for(cand.symbol,cand.side,cand.setup,ctx['regime'],ctx['session'],ctx['volatility_bucket'],ctx['news_bucket'])

    def decide_for_context(self, ctx: Dict[str, Any], wallet: Dict[str, Any], mode: str='LIVE') -> List[Decision]:
        decisions=[]
        policy = self.control.policy(CFG.open_threshold, CFG.probe_threshold)
        for cand in self.strategy.candidates(ctx):
            if cand.side=='LONG' and not CFG.allow_long: continue
            if cand.side=='SHORT' and not CFG.allow_short: continue
            edge=self.memory.view(self._edge_keys(cand,ctx))
            authority=self.stat_edge.evaluate(cand, ctx, edge)
            edge=dict(edge)
            edge['statistical_edge_authority_v1']=authority
            edge['status']=authority.get('edge_status', edge.get('status','INSUFFICIENT'))
            edge['score_adjustment']=fnum(edge.get('score_adjustment'),0)+fnum(authority.get('score_adjustment'),0)
            edge['size_multiplier']=fnum(edge.get('size_multiplier'),0.6)*fnum(authority.get('size_multiplier'),1.0)
            try:
                edge=self.nucli_quant.ajusta_edge_candidat(cand, ctx, edge)
            except Exception as _e_quant:
                edge.setdefault('reasons', []).append('ERROR_NUCLI_QUANT_NET_'+repr(_e_quant)[:80])
            risk=self.risk.size(cand,ctx,edge,wallet)

            # AUTHORITY_SIZE_USD_CAP_APPLIED
            authority_size_cap = fnum(authority.get('size_usd_cap'), 0.0)
            if authority_size_cap > 0 and risk.get('allowed'):
                risk['size_usd'] = min(fnum(risk.get('size_usd')), authority_size_cap)
                risk.setdefault('reasons', []).append('AUTHORITY_SIZE_USD_CAP_APPLIED')
            dq=fnum(ctx['data_quality'].get('score'),50); liq=fnum(ctx['micro'].get('liquidity_score'),50); der=fnum(ctx['derivatives'].get('derivatives_score'),50); macro=fnum(ctx['macro'].get('risk_score'),50); news=fnum(ctx['news'].get('severity'))
            layers={
                'alpha': cand.raw_alpha,
                'timing': cand.timing_score,
                'execution': (liq-50)*0.22 + (dq-50)*0.18,
                'context': (macro-50)*0.12 - max(0,news-35)*0.22,
                'edge': fnum(edge.get('score_adjustment')),
                'risk': 0 if risk.get('allowed') else -100,
                'derivatives': (der-50)*0.14,
            }
            final=layers['alpha']*0.45+layers['timing']*0.25+50*0.10+layers['execution']+layers['context']+layers['edge']+layers['derivatives']+layers['risk']
            reasons=list(cand.reason)+edge.get('reasons',[])+authority.get('reasons',[])+risk.get('reasons',[])
            reasons.append('STAT_EDGE_AUTHORITY_'+str(authority.get('authority_status','UNKNOWN')))
            if not ctx['data_quality'].get('hard_ok'):
                final-=100; reasons.append('DATA_QUALITY_HARD_BLOCK')
            if cand.side=='LONG' and ctx['flags'].get('late_long'):
                final-=18; reasons.append('LATE_LONG_BLOCK_OR_REDUCE')
            if cand.side=='SHORT' and ctx['flags'].get('late_short'):
                final-=18; reasons.append('LATE_SHORT_BLOCK_OR_REDUCE')
            if news>=CFG.news_high_block_threshold:
                final-=35; reasons.append('HIGH_NEWS_EVENT_RISK')

            # V24: Runtime control is the only Telegram-adjustable gate.
            # Defaults are neutral, so if no setting is changed, behaviour remains equivalent.
            if policy.get('paused'):
                final-=100; risk['allowed']=False; risk['size_usd']=0; reasons.append('RUNTIME_PAUSED')
            if not policy.get('allow_new_trades'):
                final-=100; risk['allowed']=False; risk['size_usd']=0; reasons.append('RUNTIME_NEW_TRADES_OFF')
            if cand.side=='LONG' and not policy.get('allow_long'):
                final-=100; risk['allowed']=False; risk['size_usd']=0; reasons.append('RUNTIME_LONG_OFF')
            if cand.side=='SHORT' and not policy.get('allow_short'):
                final-=100; risk['allowed']=False; risk['size_usd']=0; reasons.append('RUNTIME_SHORT_OFF')
            if risk.get('allowed') and fnum(risk.get('size_usd'))>0:
                rm=fnum(policy.get('risk_mult'),1.0)
                if abs(rm-1.0)>1e-9:
                    risk['size_usd']=fnum(risk.get('size_usd'))*rm
                    reasons.append(f"RUNTIME_RISK_MULT_{rm:.2f}")


            confidence=clamp((final-45)*1.35 + fnum(edge.get('effective_n'))*0.45,0,100)
            open_th=fnum(policy.get('open_threshold_effective'), CFG.open_threshold)
            probe_th=fnum(policy.get('probe_threshold_effective'), CFG.probe_threshold)
            if authority.get('authority_status') in ('QUARANTINED','BLOCKED'):
                final=-100
                risk['allowed']=False
                risk['size_usd']=0
                reasons.append('OPEN_BLOCKED_BY_STATISTICAL_EDGE_AUTHORITY_V1')
            if final>=open_th and risk.get('allowed') and authority.get('allow_open') and edge.get('status') in ('VALIDATED','PROMISING'):
                action='OPEN'
            elif final>=probe_th and risk.get('allowed') and authority.get('allow_probe'):
                action='PROBE'
                if risk['size_usd']>0:
                    risk['size_usd']=min(risk['size_usd'], wallet.get('equity',CFG.initial_equity)*0.015)
            else:
                action='WAIT'; risk['size_usd']=0

            trade_plan=self.reasoning.plan(cand, ctx, edge, risk, layers)
            feature_summary={'regime':ctx['regime'],'session':ctx['session'],'volatility_bucket':ctx['volatility_bucket'],'news_bucket':ctx['news_bucket'],'dq':dq,'liq':liq,'macro':macro,'news':news,'flags':ctx['flags'], 'technical':ctx.get('technical',{}), 'levels':ctx.get('levels',{}), 'micro':ctx.get('micro',{}), 'derivatives':ctx.get('derivatives',{}), 'mapa_causal':ctx.get('mapa_causal',{}), 'trade_plan': trade_plan, 'conflicts': trade_plan.get('conflict_matrix',{}).get('conflicts',[]), 'supports': trade_plan.get('conflict_matrix',{}).get('supports',[]), 'statistical_edge_authority_v1': authority, 'runtime_control': {'mode': policy.get('mode'), 'open_threshold': open_th, 'probe_threshold': probe_th, 'risk_mult': policy.get('risk_mult'), 'paused': policy.get('paused'), 'allow_new_trades': policy.get('allow_new_trades'), 'allow_long': policy.get('allow_long'), 'allow_short': policy.get('allow_short')}}
            decisio=Decision(cand.symbol, action, cand.side, cand.setup, cand.trade_type, final, confidence, fnum(risk.get('size_usd')), fnum(ctx['price']), cand.invalidation, cand.tp1, cand.tp2, reasons, layers, risk, edge, feature_summary)
            try:
                decisio=self.nucli_quant.aplica_politica_decisio(decisio, wallet)
            except Exception as _e_quant_policy:
                decisio.reasons.append('ERROR_POLITICA_QUANT_NETA_'+repr(_e_quant_policy)[:80])
            decisions.append(decisio)
        decisions.sort(key=lambda d: ({'OPEN':2,'PROBE':1,'WAIT':0}.get(str(d.action).upper(),0), d.final_score), reverse=True)
        return decisions
