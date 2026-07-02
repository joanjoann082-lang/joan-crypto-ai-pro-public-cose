from __future__ import annotations
from typing import Any, Dict, List
from .http import HttpClient
from ..utils import fnum, utc_now_iso, pct, bps

class BinanceClient:
    SPOT='https://api.binance.com'
    FUT='https://fapi.binance.com'
    def __init__(self): self.http=HttpClient('joanbot-binance-v16', timeout=10)

    def _safe_json(self, url: str, params: Dict[str, Any], ttl: int, default: Any):
        try:
            return self.http.get_json(url, params, ttl=ttl)
        except Exception:
            return default

    def klines(self, symbol: str, interval: str, limit: int = 500) -> List[Dict[str, Any]]:
        j=self.http.get_json(self.SPOT+'/api/v3/klines', {'symbol':symbol,'interval':interval,'limit':limit}, ttl=10 if interval in ('1m','5m') else 60)
        out=[]
        for x in j if isinstance(j,list) else []:
            if len(x)<11: continue
            out.append({'symbol':symbol,'interval':interval,'open_time':int(x[0]),'open':fnum(x[1]),'high':fnum(x[2]),'low':fnum(x[3]),'close':fnum(x[4]),'volume':fnum(x[5]),'close_time':int(x[6]),'quote_volume':fnum(x[7]),'trades':int(x[8]),'taker_buy_base':fnum(x[9]),'taker_buy_quote':fnum(x[10])})
        return out

    def ticker(self, symbol: str) -> Dict[str, Any]:
        j=self.http.get_json(self.SPOT+'/api/v3/ticker/24hr', {'symbol':symbol}, ttl=5)
        return {'symbol':symbol,'price':fnum(j.get('lastPrice')),'change_pct_24h':fnum(j.get('priceChangePercent')),'quote_volume_24h':fnum(j.get('quoteVolume')),'high_24h':fnum(j.get('highPrice')),'low_24h':fnum(j.get('lowPrice'))}

    def orderbook(self, symbol: str, limit: int = 100) -> Dict[str, Any]:
        j=self.http.get_json(self.SPOT+'/api/v3/depth', {'symbol':symbol,'limit':limit}, ttl=3)
        bids=[(fnum(p), fnum(q)) for p,q in j.get('bids',[])]; asks=[(fnum(p), fnum(q)) for p,q in j.get('asks',[])]
        if not bids or not asks: return {'spread_bps':999,'depth_10bps':0,'depth_25bps':0,'imbalance_25bps':0,'wall_pressure':0,'best_bid':0,'best_ask':0,'data_ok':False}
        bid=bids[0][0]; ask=asks[0][0]; mid=(bid+ask)/2
        def depth(bp: float):
            bid_d=sum(p*q for p,q in bids if p >= mid*(1-bp/10000)); ask_d=sum(p*q for p,q in asks if p <= mid*(1+bp/10000)); return bid_d, ask_d
        b10,a10=depth(10); b25,a25=depth(25)
        im=(b25-a25)/(b25+a25) if (b25+a25)>0 else 0
        tb=sum(p*q for p,q in bids[:5]); ta=sum(p*q for p,q in asks[:5]); wp=(tb-ta)/(tb+ta) if tb+ta else 0
        return {'best_bid':bid,'best_ask':ask,'spread_bps':bps(ask,bid),'depth_10bps':b10+a10,'depth_25bps':b25+a25,'imbalance_25bps':im,'wall_pressure':wp,'bid_depth_25bps':b25,'ask_depth_25bps':a25,'data_ok':True}

    def agg_trades(self, symbol: str, limit: int = 500) -> Dict[str, Any]:
        j=self.http.get_json(self.SPOT+'/api/v3/aggTrades', {'symbol':symbol,'limit':limit}, ttl=5)
        buy=0.0; sell=0.0; cvd=0.0; notional=0.0
        for t in j if isinstance(j,list) else []:
            price=fnum(t.get('p')); qty=fnum(t.get('q')); n=price*qty; notional += n
            if t.get('m'):
                sell += n; cvd -= n
            else:
                buy += n; cvd += n
        total=buy+sell
        return {'aggr_buy_notional':buy,'aggr_sell_notional':sell,'aggr_total_notional':total,'taker_buy_ratio':buy/total if total else 0.5,'cvd_proxy':cvd,'cvd_ratio':cvd/total if total else 0.0,'data_ok': bool(total)}

    def derivatives(self, symbol: str) -> Dict[str, Any]:
        errors=[]
        def safe(name, url, params, ttl, default):
            try:
                return self.http.get_json(url, params, ttl=ttl)
            except Exception as e:
                errors.append(f'{name}:{type(e).__name__}')
                return default
        premium=safe('premium', self.FUT+'/fapi/v1/premiumIndex', {'symbol':symbol}, 20, {})
        oi=safe('openInterest', self.FUT+'/fapi/v1/openInterest', {'symbol':symbol}, 20, {})
        ls=safe('globalLongShort', self.FUT+'/futures/data/globalLongShortAccountRatio', {'symbol':symbol,'period':'5m','limit':30}, 45, [])
        top=safe('topLongShort', self.FUT+'/futures/data/topLongShortAccountRatio', {'symbol':symbol,'period':'5m','limit':30}, 45, [])
        taker=safe('takerLongShort', self.FUT+'/futures/data/takerlongshortRatio', {'symbol':symbol,'period':'5m','limit':30}, 45, [])
        def last(arr, key, default=0.0):
            try: return fnum(arr[-1].get(key), default) if isinstance(arr,list) and arr else default
            except Exception: return default
        oi_val=fnum(oi.get('openInterest')) if isinstance(oi,dict) else 0.0
        mark=fnum(premium.get('markPrice')) if isinstance(premium,dict) else 0.0
        index=fnum(premium.get('indexPrice')) if isinstance(premium,dict) else 0.0
        return {
            'ts': utc_now_iso(), 'symbol': symbol,
            'funding_rate': fnum(premium.get('lastFundingRate'))*100.0 if isinstance(premium,dict) else 0.0,
            'mark_price': mark, 'index_price': index, 'basis_bps': bps(mark,index) if index else 0.0,
            'open_interest': oi_val,
            'long_short_ratio': last(ls,'longShortRatio',1.0),
            'top_long_short_ratio': last(top,'longShortRatio',1.0),
            'taker_buy_sell_ratio': last(taker,'buySellRatio',1.0),
            'raw_ls': ls[-3:] if isinstance(ls,list) else [], 'raw_top': top[-3:] if isinstance(top,list) else [],
            'data_ok': len(errors) < 3, 'endpoint_errors': errors
        }

    def force_orders_snapshot(self, symbol: str) -> Dict[str, Any]:
        try:
            j=self.http.get_json(self.FUT+'/fapi/v1/allForceOrders', {'symbol':symbol,'limit':50}, ttl=30)
        except Exception:
            j=[]
        long_liq=0.0; short_liq=0.0; count=0
        for o in j if isinstance(j,list) else []:
            side=str(o.get('side','')).upper(); p=fnum(o.get('price')); q=fnum(o.get('origQty') or o.get('executedQty')); n=p*q; count+=1
            if side=='SELL': long_liq += n
            elif side=='BUY': short_liq += n
        total=long_liq+short_liq
        return {'liquidations_count':count,'long_liq_usd':long_liq,'short_liq_usd':short_liq,'liq_imbalance':(short_liq-long_liq)/total if total else 0.0}

