from __future__ import annotations
from typing import Any, Dict
from ..config import CFG
from ..storage import get_db
from ..utils import fnum, utc_now_iso

class ThresholdAdvisor:
    def __init__(self): self.db=get_db()
    def analyze(self) -> Dict[str, Any]:
        rows=self.db.query("SELECT ts,symbol,action,side,setup,final_score,confidence,size_usd,payload FROM decisions ORDER BY id DESC LIMIT 500")
        trades=self.db.query("SELECT pnl_r,pnl_usd FROM trades ORDER BY id DESC LIMIT 80")
        forward=self.db.query("SELECT result_r,outcome FROM forward_results ORDER BY id DESC LIMIT 160")
        near_open=[r for r in rows if str(r.get('action'))=='PROBE' and fnum(r.get('final_score'))>=CFG.open_threshold-4 and fnum(r.get('confidence'))>=55]
        open_rows=[r for r in rows if str(r.get('action'))=='OPEN']; wait_rows=[r for r in rows if str(r.get('action'))=='WAIT']
        fr=[fnum(r.get('result_r')) for r in forward]; tr=[fnum(r.get('pnl_r')) for r in trades]
        f_exp=sum(fr)/len(fr) if fr else 0.0; t_exp=sum(tr)/len(tr) if tr else 0.0; f_win=sum(1 for x in fr if x>0)/len(fr) if fr else 0.0
        evidence={'sample_decisions':len(rows),'near_open_probe':len(near_open),'opens':len(open_rows),'waits':len(wait_rows),'forward_n':len(fr),'forward_expectancy_r':round(f_exp,4),'forward_winrate':round(f_win,4),'trade_n':len(tr),'trade_expectancy_r':round(t_exp,4)}
        recommendation='NO_CHANGE'; reason='No hi ha evidència suficient per tocar thresholds.'; proposed={}
        if len(fr)>=60 and f_exp>0.08 and f_win>0.52 and len(near_open)>=8 and len(open_rows)<max(2,len(rows)*0.02): recommendation='CONSIDER_LOWER_OPEN_THRESHOLD_SMALL'; proposed={'open_threshold':max(70,CFG.open_threshold-2)}; reason='Forward positiu + molts PROBE prop d’OPEN + pocs OPEN. Recomanació petita, no automàtica.'
        elif len(fr)>=60 and f_exp< -0.04: recommendation='CONSIDER_RAISE_THRESHOLDS'; proposed={'open_threshold':min(92,CFG.open_threshold+3),'probe_threshold':min(88,CFG.probe_threshold+3)}; reason='Forward expectancy negativa. Millor més filtre.'
        elif len(tr)>=20 and t_exp< -0.04: recommendation='CONSERVATIVE_MODE_RECOMMENDED'; proposed={'mode':'conservative'}; reason='Trades recents amb expectancy negativa. Reduir agressivitat.'
        return {'ts':utc_now_iso(),'recommendation':recommendation,'reason':reason,'proposed':proposed,'evidence':evidence}
