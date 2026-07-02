from __future__ import annotations
import sys, os
sys.path.insert(0, os.getcwd())
from joanbot.features.context import ContextEngine
from joanbot.intelligence.decision import DecisionKernel
from joanbot.execution import PaperBroker, ProfitGuard
from joanbot.testing import ForwardTester

def fake_candles(n=260, start=100.0, step=0.08):
    out=[]; p=start
    for i in range(n):
        o=p; c=p+step; h=max(o,c)+0.2; l=min(o,c)-0.2; p=c
        out.append({'open_time':i*60000,'close_time':(i+1)*60000,'open':o,'high':h,'low':l,'close':c,'volume':100+i%10,'quote_volume':(100+i%10)*c,'trades':100,'taker_buy_base':50,'taker_buy_quote':50*c})
    return out
snap={'symbol':'BTCUSDT','price':121.0,'candles':{tf:fake_candles() for tf in ['1m','5m','15m','1h','4h','1d']},'orderbook':{'spread_bps':1.2,'depth_25bps':25_000_000,'imbalance_25bps':0.1,'wall_pressure':0.05},'trades':{'cvd_ratio':0.05,'taker_buy_ratio':0.55},'derivatives':{'funding_rate':0.005,'open_interest':1000,'long_short_ratio':1.1,'taker_buy_sell_ratio':1.05},'liquidations':{'liq_imbalance':0.1}}
global_snap={'macro':{'risk_score':58,'mode':'NEUTRAL'},'news':{'severity':10,'bucket':'LOW'},'calendar':{}}
ctx=ContextEngine().build(snap,global_snap)
ds=DecisionKernel().decide_for_context(ctx, {'equity':100000,'open':[]}, mode='TEST')
assert ds, 'no decisions'
for d in ds:
    assert d.symbol=='BTCUSDT'
    assert d.action in ('OPEN','PROBE','WAIT')
print('SMOKE_OK', ds[0].action, round(ds[0].final_score,2), ds[0].side)
