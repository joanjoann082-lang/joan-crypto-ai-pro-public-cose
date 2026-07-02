from __future__ import annotations
"""Optional Binance Futures WebSocket collector.

It is intentionally optional because Termux installs differ. If websocket-client exists,
this module streams:
  - aggTrade: CVD/orderflow
  - forceOrder: real liquidations
  - depth20@100ms: depth snapshots/diff proxy
  - kline_1m: live candle heartbeat
Fallback: REST MarketDataHub continues working if this daemon is not running.
"""
import json, time, threading
from typing import Any, Dict
from ..config import CFG
from ..storage import get_db
from ..utils import utc_now_iso, fnum

STREAMS=['aggTrade','forceOrder','depth20@100ms','kline_1m']

class WebSocketCollector:
    def __init__(self): self.db=get_db(); self.running=False; self.state={s:{} for s in CFG.symbols}
    def url(self) -> str:
        streams=[]
        for sym in CFG.symbols:
            low=sym.lower(); streams += [f'{low}@aggTrade', f'{low}@forceOrder', f'{low}@depth20@100ms', f'{low}@kline_1m']
        return 'wss://fstream.binance.com/stream?streams=' + '/'.join(streams)
    def on_message(self, ws, message: str) -> None:
        try: msg=json.loads(message); data=msg.get('data',{}); event=data.get('e')
        except Exception: return
        ts=utc_now_iso(); sym=data.get('s')
        if not sym: return
        if event=='aggTrade':
            price=fnum(data.get('p')); qty=fnum(data.get('q')); notional=price*qty; sell_aggr=bool(data.get('m'))
            cvd=-notional if sell_aggr else notional
            self.db.insert_json('orderflow_snapshots', {'ts':ts,'symbol':sym,'event':'aggTrade','price':price,'qty':qty,'notional':notional,'cvd_delta':cvd,'sell_aggressive':sell_aggr}, {'ts':ts,'symbol':sym,'spread_bps':0,'depth_10bps':0,'depth_25bps':0,'imbalance_25bps':0,'wall_pressure':0,'cvd_proxy':cvd})
        elif event=='forceOrder':
            o=data.get('o',{}); side=o.get('S'); price=fnum(o.get('p')); qty=fnum(o.get('q')); n=price*qty
            payload={'ts':ts,'symbol':sym,'event':'forceOrder','side':side,'price':price,'qty':qty,'notional':n,'raw':o}
            self.db.insert_json('orderflow_snapshots', payload, {'ts':ts,'symbol':sym,'spread_bps':0,'depth_10bps':0,'depth_25bps':0,'imbalance_25bps':0,'wall_pressure':0,'cvd_proxy':0})
        elif event=='kline':
            k=data.get('k',{}); candle={'symbol':sym,'interval':k.get('i','1m'),'open_time':int(k.get('t',0)),'close_time':int(k.get('T',0)),'open':fnum(k.get('o')),'high':fnum(k.get('h')),'low':fnum(k.get('l')),'close':fnum(k.get('c')),'volume':fnum(k.get('v')),'quote_volume':fnum(k.get('q')),'trades':int(k.get('n',0)),'taker_buy_base':fnum(k.get('V')),'taker_buy_quote':fnum(k.get('Q'))}
            self.db.upsert_candles(sym,'1m',[candle])
    def run(self) -> None:
        try:
            import websocket  # type: ignore
        except Exception as e:
            raise SystemExit('Install optional dependency: pip install websocket-client') from e
        self.running=True
        while True:
            ws=websocket.WebSocketApp(self.url(), on_message=self.on_message)
            ws.run_forever(ping_interval=20, ping_timeout=10)
            time.sleep(5)

def main(): WebSocketCollector().run()
if __name__=='__main__': main()
