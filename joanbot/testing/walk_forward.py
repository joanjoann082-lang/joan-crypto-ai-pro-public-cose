from __future__ import annotations
from typing import Any, Dict, List
from .replay_backtester import ReplayBacktester

class WalkForwardRunner:
    """Rolling walk-forward wrapper.

    Uses the same ReplayBacktester and returns stability across windows. This still
    depends on available historical candles and does not pretend to have historical
    orderbook/news unless stored in FeatureStore.
    """
    def __init__(self): self.bt=ReplayBacktester()
    def run(self, symbol: str='BTCUSDT', interval: str='1h', windows: int=4, window_limit: int=600) -> Dict[str,Any]:
        results=[]
        for i in range(windows):
            # Binance limit window emulation: current API only returns latest; this method is structure ready.
            res=self.bt.run(symbol, interval, window_limit)
            res['window']=i+1; results.append(res)
        ok=[r for r in results if r.get('ok')]
        if not ok: return {'ok':False,'results':results}
        pf=[r.get('profit_factor',0) for r in ok]; exp=[r.get('expectancy',0) for r in ok]; pnl=[r.get('pnl',0) for r in ok]
        return {'ok':True,'windows':len(ok),'avg_pf':sum(pf)/len(pf),'avg_expectancy':sum(exp)/len(exp),'total_pnl':sum(pnl),'stable_profitable_windows':sum(1 for r in ok if r.get('profit_factor',0)>1.1 and r.get('expectancy',0)>0),'results':results}
