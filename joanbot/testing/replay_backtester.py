from __future__ import annotations
from typing import Any, Dict, List
from pathlib import Path
from ..market.binance import BinanceClient
from ..features.context import ContextEngine
from ..intelligence.decision import DecisionKernel
from ..execution.broker import PaperBroker, ProfitGuard
from ..storage.db import DB
from ..config import CFG
from ..utils import fnum

class ReplayBacktester:
    def __init__(self, db_path: str=':memory:'):
        # Uses live components but with isolated DB where possible; in this compact build, report only simulates decisions from candles.
        self.binance=BinanceClient(); self.ctx=ContextEngine(); self.kernel=DecisionKernel()
    def run(self, symbol: str='BTCUSDT', interval: str='1h', limit: int=1000) -> Dict[str, Any]:
        candles=self.binance.klines(symbol,interval,limit)
        if len(candles)<250: return {'ok':False,'reason':'not_enough_candles','n':len(candles)}
        equity=CFG.initial_equity; trades=[]; decisions=0; opens=0; waits=0; probes=0
        # Simplified OHLC path evaluation. Professional use requires historical derivatives/orderbook in FeatureStore.
        for i in range(220,len(candles)-5):
            window=candles[:i]
            price=fnum(window[-1]['close'])
            snap={'symbol':symbol,'price':price,'candles':{'1m':window[-300:],'5m':window[-300:],'15m':window[-300:],'1h':window[-300:],'4h':window[-300:],'1d':window[-300:]},'orderbook':{'spread_bps':2,'depth_25bps':20_000_000,'imbalance_25bps':0,'wall_pressure':0},'trades':{'cvd_ratio':0,'taker_buy_ratio':0.5},'derivatives':{'funding_rate':0,'open_interest':1,'long_short_ratio':1,'taker_buy_sell_ratio':1},'liquidations':{'liq_imbalance':0}}
            global_snap={'macro':{'risk_score':50,'mode':'NEUTRAL'},'news':{'severity':0,'bucket':'LOW'},'calendar':{}}
            ctx=self.ctx.build(snap,global_snap); ds=self.kernel.decide_for_context(ctx, {'equity':equity,'open':[]}, mode='BACKTEST')
            if not ds: continue
            d=ds[0]; decisions+=1
            if d.action=='WAIT': waits+=1; continue
            if d.action=='PROBE': probes+=1
            if d.action=='OPEN': opens+=1
            risk=abs(d.entry-d.stop_loss) or d.entry*0.01; result=0
            future=candles[i+1:i+6]
            for c in future:
                h=fnum(c['high']); l=fnum(c['low'])
                if d.side=='LONG':
                    if l<=d.stop_loss: result=-1; break
                    if h>=d.take_profit_2: result=2; break
                    if h>=d.take_profit_1: result=1
                else:
                    if h>=d.stop_loss: result=-1; break
                    if l<=d.take_profit_2: result=2; break
                    if l<=d.take_profit_1: result=1
            pnl=d.size_usd*(risk/d.entry)*result if d.entry else 0; equity+=pnl; trades.append(pnl)
        wins=[x for x in trades if x>0]; losses=[x for x in trades if x<0]
        pf=sum(wins)/abs(sum(losses)) if losses else (999 if wins else 0)
        return {'ok':True,'symbol':symbol,'interval':interval,'decisions':decisions,'opens':opens,'probes':probes,'waits':waits,'trades':len(trades),'pnl':sum(trades),'equity':equity,'winrate':len(wins)/max(1,len(wins)+len(losses))*100,'profit_factor':pf,'expectancy':sum(trades)/max(1,len(trades))}
