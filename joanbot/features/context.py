from __future__ import annotations
from typing import Any, Dict
from datetime import datetime, timezone
from ..utils import fnum, clamp, utc_now_iso
from ..storage import get_db
from .data_quality import DataQualityEngine
from .technical import TechnicalEngine
from .levels import LevelsEngine

class ContextEngine:
    def __init__(self): self.quality=DataQualityEngine(); self.tech=TechnicalEngine(); self.levels=LevelsEngine(); self.db=get_db()
    def session(self) -> str:
        h=datetime.now(timezone.utc).hour
        if 0<=h<7: return 'ASIA'
        if 7<=h<13: return 'EUROPE'
        if 13<=h<21: return 'US'
        return 'LATE_US'
    def build(self, symbol_snap: Dict[str, Any], global_snap: Dict[str, Any]) -> Dict[str, Any]:
        symbol=symbol_snap['symbol']; price=fnum(symbol_snap.get('price'))
        q=self.quality.evaluate(symbol_snap)
        tech=self.tech.analyze(symbol_snap.get('candles',{}))
        lvl=self.levels.analyze(symbol_snap.get('candles',{}), price)
        der=symbol_snap.get('derivatives',{}); ob=symbol_snap.get('orderbook',{}); trades=symbol_snap.get('trades',{}); liq=symbol_snap.get('liquidations',{})
        macro=global_snap.get('macro',{}); news=global_snap.get('news',{}); cal=global_snap.get('calendar',{})
        # scoring modules
        liq_score=50
        spread=fnum(ob.get('spread_bps')); depth=fnum(ob.get('depth_25bps')); imb=fnum(ob.get('imbalance_25bps'))
        if spread<2: liq_score+=20
        elif spread>8: liq_score-=20
        if depth>50_000_000: liq_score+=20
        elif depth<2_000_000: liq_score-=15
        if abs(imb)>0.35: liq_score-=7
        liq_score=clamp(liq_score,0,100)
        fund=fnum(der.get('funding_rate')); oi=fnum(der.get('open_interest')); lsr=fnum(der.get('long_short_ratio'),1); taker=fnum(der.get('taker_buy_sell_ratio'),1); cvd_ratio=fnum(trades.get('cvd_ratio'))
        deriv_score=50 + clamp((taker-1)*20,-12,12) - clamp(abs(fund)*250,0,12) + clamp(cvd_ratio*20,-10,10)
        if lsr>1.8: deriv_score-=8
        elif lsr<0.65: deriv_score+=5
        deriv_score=clamp(deriv_score,0,100)
        # late move and squeeze risk
        tf=tech.get('timeframes',{}); rsi15=fnum(tf.get('15m',{}).get('rsi'),50); rsi1h=fnum(tf.get('1h',{}).get('rsi'),50)
        ret1h=fnum(tf.get('1h',{}).get('ret'))
        late_long = rsi15>73 or (rsi1h>70 and ret1h>2.0)
        late_short = rsi15<27 or (rsi1h<30 and ret1h<-2.0)
        squeeze_risk=clamp(abs(fnum(liq.get('liq_imbalance')))*70 + abs(imb)*20 + max(0, abs(cvd_ratio)*10),0,100)
        regime=tech.get('regime','RANGE_CHOP')
        vol_pct=fnum(tf.get('1h',{}).get('atr_pct'))
        vol_bucket='HIGH' if vol_pct>2.0 else 'LOW' if vol_pct<0.55 else 'NORMAL'
        news_sev=fnum(news.get('severity')); news_bucket='HIGH' if news_sev>=70 else 'MEDIUM' if news_sev>=35 else 'LOW'
        ctx={'symbol':symbol,'ts':utc_now_iso(),'price':price,'session':self.session(),'regime':regime,'volatility_bucket':vol_bucket,'news_bucket':news_bucket,'data_quality':q,'technical':tech,'levels':lvl,'micro':{**ob, **trades, 'liquidity_score':liq_score},'derivatives':{**der, **liq, 'derivatives_score':deriv_score},'macro':macro,'news':news,'calendar':cal,'flags':{'late_long':late_long,'late_short':late_short,'squeeze_risk':squeeze_risk}}
        self.db.insert_json('features', ctx, {'ts':ctx['ts'],'symbol':symbol,'regime':regime,'session':ctx['session'],'volatility_bucket':vol_bucket,'news_bucket':news_bucket,'data_quality':q.get('score',0)})
        return ctx
