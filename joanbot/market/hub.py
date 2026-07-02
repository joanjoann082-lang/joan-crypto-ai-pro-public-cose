from __future__ import annotations
from typing import Any, Dict, List
from ..config import CFG
from ..storage import get_db
from ..utils import utc_now_iso, fnum
from .binance import BinanceClient
from .macro_news import MacroClient, NewsFilter, EconomicCalendar

class MarketDataHub:
    def __init__(self):
        self.binance=BinanceClient(); self.macro=MacroClient(); self.news=NewsFilter(); self.calendar=EconomicCalendar(); self.db=get_db()
        self._macro_cache={}; self._news_cache={}
    def global_snapshot(self, force: bool=False) -> Dict[str, Any]:
        macro=self.macro.snapshot()
        news_events=self.news.fetch()
        news_agg=self.news.aggregate(news_events)
        cal=self.calendar.scheduled_risk(news_agg)
        snap={'ts':utc_now_iso(),'macro':macro,'news':news_agg,'calendar':cal}
        self.db.insert_json('macro_snapshots', snap, {'ts':snap['ts'],'risk_score':macro.get('risk_score',50),'mode':macro.get('mode','NEUTRAL')})
        for e in news_events:
            self.db.insert_json('news_events', e, {'ts':e.get('ts'), 'source':e.get('source'), 'category':e.get('category'), 'severity':e.get('severity'), 'direction':e.get('direction'), 'title':e.get('title'), 'url':e.get('url')})
        return snap
    def symbol_snapshot(self, symbol: str) -> Dict[str, Any]:
        intervals=['1m','5m','15m','1h','4h','1d']
        candles={}
        for itv in intervals:
            try:
                cs=self.binance.klines(symbol,itv,500 if itv in ('1m','5m','15m') else 300)
                candles[itv]=cs; self.db.upsert_candles(symbol,itv,cs)
            except Exception as e:
                candles[itv]=[]
        ticker=self.binance.ticker(symbol)
        orderbook=self.binance.orderbook(symbol)
        trades=self.binance.agg_trades(symbol)
        derivatives=self.binance.derivatives(symbol)
        liq=self.binance.force_orders_snapshot(symbol)
        price=fnum(ticker.get('price')) or (candles.get('1m') or [{}])[-1].get('close',0)
        snap={'ts':utc_now_iso(),'symbol':symbol,'price':price,'ticker':ticker,'candles':candles,'orderbook':orderbook,'trades':trades,'derivatives':derivatives,'liquidations':liq}
        self.db.insert_json('market_snapshots', snap, {'ts':snap['ts'],'symbol':symbol,'price':price})
        self.db.insert_json('derivatives_snapshots', derivatives, {'ts':snap['ts'],'symbol':symbol,'funding':derivatives.get('funding_rate',0),'open_interest':derivatives.get('open_interest',0),'oi_chg_5m':derivatives.get('oi_chg_5m',0),'oi_chg_1h':derivatives.get('oi_chg_1h',0),'long_short':derivatives.get('long_short_ratio',0),'top_long_short':derivatives.get('top_long_short_ratio',0),'taker_buy_ratio':derivatives.get('taker_buy_sell_ratio',0),'basis_bps':derivatives.get('basis_bps',0)})
        self.db.insert_json('orderflow_snapshots', {**orderbook, **trades, **liq}, {'ts':snap['ts'],'symbol':symbol,'spread_bps':orderbook.get('spread_bps',0),'depth_10bps':orderbook.get('depth_10bps',0),'depth_25bps':orderbook.get('depth_25bps',0),'imbalance_25bps':orderbook.get('imbalance_25bps',0),'wall_pressure':orderbook.get('wall_pressure',0),'cvd_proxy':trades.get('cvd_proxy',0)})
        return snap
