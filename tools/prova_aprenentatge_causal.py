from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from joanbot.institutional.aprenentatge_causal_net import MotorAprenentatgeCausalNet

ctx={
 'price':100,
 'technical':{'timeframes':{'15m':{'state':'BULL','rsi':72},'1h':{'state':'BULL'},'4h':{'state':'BULL'}}},
 'levels':{'distances_pct':{'vah':0.12,'val':1.1,'poc':0.25,'vwap_d':0.18},'cycles':{'24h':{'close_pos':0.92},'7d':{'close_pos':0.80}}},
 'micro':{'cvd_ratio':-0.12,'imbalance_25bps':-0.22},
 'derivatives':{'funding_rate':0.0003,'long_short_ratio':1.8,'long_liq_usd':500000,'short_liq_usd':100000,'liq_imbalance':-0.3},
}
candles={'15m':[{'high':i+1,'low':i-1,'close':i,'volume':1} for i in range(1,40)], '1h':[{'high':i+1,'low':i-1,'close':i,'volume':1} for i in range(1,80)], '4h':[{'high':i+1,'low':i-1,'close':i,'volume':1} for i in range(1,80)]}
m=MotorAprenentatgeCausalNet()
mp=m.calcula(ctx,candles)
keys=m.claus_causals('BTCUSDT','SHORT','PROVA','TRENDING_BULL',mp)
assert mp.get('tags'), 'sense tags'
assert keys, 'sense claus causals'
print('PROVA_APRENENTATGE_CAUSAL_OK', mp['tags'], keys[:3])
