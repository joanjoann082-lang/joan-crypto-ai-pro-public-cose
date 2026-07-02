from __future__ import annotations
import json, statistics
from typing import Any, Dict, List, Optional
from .db import get_db
from ..utils import fnum, utc_now_iso

class FeatureStore:
    """Query layer over SQLite feature store.

    This is not another decision layer. It provides consistent historical windows to
    market/context/backtest/report modules so live, forward, and replay use the same data contract.
    """
    def __init__(self): self.db=get_db()
    def candles(self, symbol: str, interval: str, limit: int=1000) -> List[Dict[str,Any]]:
        return self.db.latest_candles(symbol, interval, limit)
    def feature_window(self, symbol: str, limit: int=500) -> List[Dict[str,Any]]:
        rows=self.db.query('SELECT * FROM features WHERE symbol=? ORDER BY id DESC LIMIT ?', (symbol, limit))
        out=[]
        for r in reversed(rows):
            try: p=json.loads(r.get('payload') or '{}')
            except Exception: p={}
            out.append({**r,'payload_obj':p})
        return out
    def derivatives_window(self, symbol: str, limit: int=500) -> List[Dict[str,Any]]:
        return list(reversed(self.db.query('SELECT * FROM derivatives_snapshots WHERE symbol=? ORDER BY id DESC LIMIT ?', (symbol, limit))))
    def orderflow_window(self, symbol: str, limit: int=500) -> List[Dict[str,Any]]:
        return list(reversed(self.db.query('SELECT * FROM orderflow_snapshots WHERE symbol=? ORDER BY id DESC LIMIT ?', (symbol, limit))))
    def macro_window(self, limit: int=500) -> List[Dict[str,Any]]:
        return list(reversed(self.db.query('SELECT * FROM macro_snapshots ORDER BY id DESC LIMIT ?', (limit,))))
    def news_window(self, limit: int=200) -> List[Dict[str,Any]]:
        return list(reversed(self.db.query('SELECT * FROM news_events ORDER BY id DESC LIMIT ?', (limit,))))
    def zscore(self, vals: List[float], current: Optional[float]=None) -> float:
        vals=[fnum(v) for v in vals if v is not None]
        if len(vals)<20: return 0.0
        current=fnum(current, vals[-1])
        mean=statistics.mean(vals); sd=statistics.pstdev(vals) or 1e-9
        return (current-mean)/sd
    def derivative_factors(self, symbol: str, limit: int=240) -> Dict[str,Any]:
        rows=self.derivatives_window(symbol,limit)
        if not rows: return {'ok':False}
        oi=[fnum(r.get('open_interest')) for r in rows]; funding=[fnum(r.get('funding')) for r in rows]; ls=[fnum(r.get('long_short')) for r in rows]
        cur=rows[-1]
        return {'ok':True,'oi_z':self.zscore(oi), 'funding_z':self.zscore(funding), 'long_short_z':self.zscore(ls), 'oi_change_window':(oi[-1]/oi[0]-1)*100 if oi and oi[0] else 0, 'funding_current':funding[-1] if funding else 0, 'long_short_current':ls[-1] if ls else 1}
    def orderflow_factors(self, symbol: str, limit: int=240) -> Dict[str,Any]:
        rows=self.orderflow_window(symbol,limit)
        if not rows: return {'ok':False}
        cvd=[fnum(r.get('cvd_proxy')) for r in rows]; im=[fnum(r.get('imbalance_25bps')) for r in rows]; spread=[fnum(r.get('spread_bps')) for r in rows if fnum(r.get('spread_bps'))>0]
        return {'ok':True,'cvd_sum':sum(cvd), 'cvd_z':self.zscore(cvd), 'imbalance_avg':sum(im)/max(1,len(im)), 'spread_avg':sum(spread)/max(1,len(spread)) if spread else 0, 'spread_z':self.zscore(spread) if spread else 0}
    def data_health(self, symbol: str) -> Dict[str,Any]:
        checks={}
        for itv in ['1m','5m','15m','1h','4h','1d']:
            cs=self.candles(symbol,itv,10); checks[itv]={'n':len(cs),'last_close':cs[-1]['close_time'] if cs else None}
        return {'symbol':symbol,'checks':checks,'derivatives':len(self.derivatives_window(symbol,20)),'orderflow':len(self.orderflow_window(symbol,20)),'features':len(self.feature_window(symbol,20))}
