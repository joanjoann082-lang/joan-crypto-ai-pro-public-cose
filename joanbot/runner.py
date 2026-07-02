from __future__ import annotations
import time, traceback
from .config import CFG, STATE_PATH
from .utils import utc_now_iso, atomic_write_json, fnum
from .storage import get_db
from .market import MarketDataHub
from .features import ContextEngine
from .intelligence import DecisionKernel, EdgeMemory, AlertEngine
from .execution import PaperBroker, ProfitGuard
from .testing import ForwardTester
from .intelligence.universal_shadow_alpha_loop_v2 import UniversalShadowAlphaLoopV2
from .alpha.alpha_promotion_contract_v5 import AlphaPromotionContractV5
from .control.control_plane_v6 import InstitutionalControlPlaneV6
from .alpha.alpha_meta_governance_v5 import AlphaMetaGovernanceV5
from .alpha.alpha_bayesian_posterior_v5 import AlphaBayesianPosteriorV5
from .alpha.alpha_evidence_tensor_v5 import AlphaEvidenceTensorV5
from .ops.retention_v1 import run_retention_safe

class Scheduler:
    def __init__(self): self.last={}
    def due(self,name:str,sec:int)->bool:
        now=time.time(); old=self.last.get(name,0)
        if now-old>=sec: self.last[name]=now; return True
        return False
class Runner:
    def __init__(self): self.db=get_db(); self.market=MarketDataHub(); self.context=ContextEngine(); self.kernel=DecisionKernel(); self.edge=EdgeMemory(); self.broker=PaperBroker(); self.guard=ProfitGuard(self.broker); self.forward=ForwardTester(); self.alpha_shadow=UniversalShadowAlphaLoopV2(self.db); self.alpha_tensor=AlphaEvidenceTensorV5(self.db); self.alpha_posterior=AlphaBayesianPosteriorV5(self.db); self.alpha_meta=AlphaMetaGovernanceV5(self.db); self.alpha_contracts=AlphaPromotionContractV5(self.db); self.control_plane=InstitutionalControlPlaneV6(self.db); self.alerts=AlertEngine(); self.s=Scheduler(); self.global_snap={'macro':{'risk_score':50},'news':{'severity':0},'calendar':{}}; self.symbol_snaps={}; self.contexts={}; self.prices={}; self.started=utc_now_iso(); self.cycles=0; self.errors=[]
    def step_market(self):
        if self.s.due('global',CFG.loop_macro_sec): self.global_snap=self.market.global_snapshot(); self.alerts.evaluate_global(self.global_snap)
        for sym in CFG.symbols:
            if self.s.due(f'market_{sym}',CFG.loop_market_sec): snap=self.market.symbol_snapshot(sym); self.symbol_snaps[sym]=snap; self.prices[sym]=fnum(snap.get('price'))
    def step_context(self):
        for sym,snap in list(self.symbol_snaps.items()):
            if self.s.due(f'context_{sym}',CFG.loop_context_sec): self.contexts[sym]=self.context.build(snap,self.global_snap)
    def step_positions(self):
        if self.s.due('positions',CFG.loop_position_sec):
            for a in self.guard.manage(self.prices): self.alerts.emit('HIGH','POSITION_EVENT',a.get('symbol',''),f"📌 POSITION {a.get('symbol')} {a.get('reason')} pnl {a.get('pnl_usd'):.2f}$",a,300,f"POS|{a.get('symbol')}|{a.get('reason')}|{a.get('position_id')}")
    def step_decisions(self):
        if not self.s.due('decisions',CFG.loop_decision_sec): return
        wallet=self.broker.refresh(); all_decisions=[]
        for sym,ctx in list(self.contexts.items()):
            try:
                ds=self.kernel.decide_for_context(ctx,wallet,mode='LIVE')
                for d in ds: self.db.record_decision('LIVE',d.to_dict()); self.forward.register(d.to_dict(),CFG.forward_horizons); self.alerts.evaluate_decision(d)
                if ds:
                    best=ds[0]; all_decisions.append(best)
                    if best.action == 'OPEN':
                        opened=self.broker.open_from_decision(best)
                        if opened: self.alerts.emit('HIGH','TRADE_OPENED',best.symbol,f"✅ PAPER OPEN {best.symbol} {best.side} {best.setup}\nsize {best.size_usd:.0f}$ entry {best.entry:.2f}",opened,900,f"TRADE_OPENED|{best.symbol}|{best.side}|{best.setup}|{best.entry:.0f}")
            except Exception as e: self.errors.append({'ts':utc_now_iso(),'symbol':sym,'error':repr(e),'trace':traceback.format_exc(limit=3)})
        self.alerts.evaluate_advisor(); return all_decisions
    def step_alpha_shadow(self):
        # Isolated universal alpha learning loop.
        # Writes only to universal_shadow_* tables.
        # Does not touch risk, broker, execution, decision.py, forward_cases or forward_results.
        if not self.s.due('alpha_shadow', int(getattr(CFG, 'loop_alpha_shadow_sec', 300))):
            return
        try:
            try:
                res = self.alpha_shadow.cycle(self.contexts)
                self.db.runtime_event('alpha_shadow', 'INFO', 'universal_shadow_alpha_v2_cycle', res)
            except Exception as e:
                self.db.runtime_event('alpha_shadow', 'ERROR', 'universal_shadow_alpha_v2_cycle_failed', {
                    'error': repr(e),
                    'runner_protected': True,
                })

            try:
                tensor = self.alpha_tensor.refresh()
                posterior = self.alpha_posterior.refresh()
                meta = self.alpha_meta.refresh()
                contracts = self.alpha_contracts.refresh()
                control_plane = self.control_plane.refresh()
                self.db.runtime_event('alpha_operating_layer', 'INFO', 'alpha_operating_layer_v5_refresh', {
                    'tensor': tensor,
                    'posterior': posterior,
                    'meta': meta,
                    'contracts': contracts,
                    'control_plane': control_plane,
                })
            except Exception as e:
                self.db.runtime_event('alpha_operating_layer', 'ERROR', 'alpha_operating_layer_v5_refresh_failed', {
                    'error': repr(e),
                    'runner_protected': True,
                })
        except Exception as e:
            self.errors.append({
                'ts': utc_now_iso(),
                'component': 'alpha_shadow_v2',
                'error': repr(e),
                'trace': traceback.format_exc(limit=3),
            })
            self.db.runtime_event('alpha_shadow', 'ERROR', repr(e), {
                'trace': traceback.format_exc(limit=3),
            })

    def step_forward(self):
        if self.s.due('forward',CFG.loop_forward_sec):
            for r in self.forward.resolve_due(): self.edge.update_many(self.edge.keys_for(r['symbol'],r['side'],r['setup'],'UNKNOWN','UNKNOWN','UNKNOWN','UNKNOWN'),'FORWARD',fnum(r.get('result_r')),r)
    def step_retention(self):
        # Storage hygiene only. No trading decision, no position close, no PnL mutation.
        if self.s.due('retention_v1', int(getattr(CFG, 'loop_retention_sec', 900))):
            run_retention_safe(apply=True)

    def write_state(self):
        if self.s.due('state',CFG.loop_health_sec):
            wallet=self.broker.refresh(); state={'ts':utc_now_iso(),'started':self.started,'cycles':self.cycles,'symbols':list(CFG.symbols),'prices':self.prices,'wallet':wallet,'global':self.global_snap,'contexts':{k:{'regime':v.get('regime'),'session':v.get('session'),'dq':v.get('data_quality',{}).get('score'),'news':v.get('news',{}).get('severity'),'macro':v.get('macro',{}).get('risk_score'),'flags':v.get('flags')} for k,v in self.contexts.items()},'db':self.db.state(),'errors':self.errors[-5:]}; atomic_write_json(STATE_PATH,state)
    def run(self):
        self.db.runtime_event('runner','INFO','runner_start_v21',{'symbols':list(CFG.symbols)})
        while True:
            try: self.cycles+=1; self.step_market(); self.step_context(); self.step_positions(); self.step_decisions(); self.step_alpha_shadow(); self.step_forward(); self.step_retention(); self.write_state(); time.sleep(1)
            except KeyboardInterrupt: raise
            except Exception as e: self.errors.append({'ts':utc_now_iso(),'error':repr(e),'trace':traceback.format_exc(limit=5)}); self.db.runtime_event('runner','ERROR',repr(e),{'trace':traceback.format_exc(limit=3)}); time.sleep(5)
def main(): Runner().run()
if __name__=='__main__': main()
