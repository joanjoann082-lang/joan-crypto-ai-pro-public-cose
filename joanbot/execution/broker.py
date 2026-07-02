from __future__ import annotations
import uuid
from dataclasses import asdict
from typing import Any, Dict, List
from ..config import CFG
from ..models import Position
from ..storage import get_db
from ..utils import fnum, utc_now_iso, pct, clamp
from .contract import evaluate_execution, record_execution_rejection

class PaperBroker:
    def __init__(self): self.db=get_db(); self.wallet=self.load_wallet()
    def load_wallet(self) -> Dict[str, Any]:
        # derive from DB positions/trades when possible; simple JSON-like state maintained in runtime through DB payloads
        rows=self.db.query("SELECT payload FROM positions WHERE status='OPEN'")
        opens=[]
        for r in rows:
            try:
                import json; opens.append(json.loads(r['payload']))
            except Exception: pass
        pnl=sum(fnum(r['pnl_usd']) for r in self.db.query('SELECT pnl_usd FROM trades'))
        return {'equity': CFG.initial_equity + pnl, 'initial': CFG.initial_equity, 'open': opens, 'closed_pnl': pnl}
    def refresh(self): self.wallet=self.load_wallet(); return self.wallet
    def open_from_decision(self, d) -> Dict[str, Any] | None:
        wallet = self.refresh()
        verdict = evaluate_execution(d, wallet.get('open', []) or [])
        if not verdict.allowed:
            record_execution_rejection(self.db, d, verdict)
            return None

        pid=str(uuid.uuid4())[:12]
        entry=self._slipped_entry(d.entry,d.side,d.size_usd)
        pos=Position(pid,d.symbol,d.side,d.setup,d.size_usd,entry,d.stop_loss,d.take_profit_1,d.take_profit_2,utc_now_iso(),meta={'decision':d.to_dict()})
        payload=asdict(pos)
        self.db.execute('INSERT OR REPLACE INTO positions(id,opened_at,closed_at,symbol,side,setup,status,entry,exit,size_usd,pnl_usd,payload) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)', (pos.id,pos.opened_at,None,pos.symbol,pos.side,pos.setup,pos.status,pos.entry_price,None,pos.size_usd,None,__import__('json').dumps(payload,sort_keys=True)))
        self.db.insert_json('position_events', {'event':'OPEN','position':payload}, {'ts':utc_now_iso(),'position_id':pid,'event':'OPEN','symbol':pos.symbol})
        return payload
    def _slipped_entry(self, price: float, side: str, size_usd: float) -> float:
        slip=CFG.slippage_base_bps/10000.0
        return price*(1+slip) if side=='LONG' else price*(1-slip)
    def mark_positions(self, prices: Dict[str,float]) -> List[Dict[str,Any]]:
        self.refresh(); return self.wallet.get('open',[])
    def close_position(self, pos: Dict[str,Any], exit_price: float, reason: str, close_pct: float=1.0) -> Dict[str,Any]:
        side=pos.get('side'); entry=fnum(pos.get('entry_price')); size=fnum(pos.get('size_usd'))*close_pct
        slip=CFG.slippage_base_bps/10000.0; px=exit_price*(1-slip) if side=='LONG' else exit_price*(1+slip)
        gross=(px-entry)/entry*size if side=='LONG' else (entry-px)/entry*size
        fees=(entry*0+size)*CFG.fee_rate + size*CFG.fee_rate
        pnl=gross-fees
        pos_id=pos.get('id')
        if close_pct>=0.999 or fnum(pos.get('remaining_pct'),1)<=close_pct+1e-9:
            status='CLOSED'; remaining=0
        else:
            status='OPEN'; remaining=max(0,fnum(pos.get('remaining_pct'),1)-close_pct); pos['remaining_pct']=remaining; pos['size_usd']=fnum(pos.get('size_usd'))*(remaining/fnum(pos.get('remaining_pct'),1) if fnum(pos.get('remaining_pct'),1) else 0)
        trade={'ts':utc_now_iso(),'position_id':pos_id,'symbol':pos.get('symbol'),'side':side,'setup':pos.get('setup'),'entry':entry,'exit':px,'size_usd':size,'pnl_usd':pnl,'fees':fees,'reason':reason,'close_pct':close_pct}
        self.db.execute('INSERT INTO trades(ts,position_id,symbol,side,setup,pnl_usd,pnl_r,fees,reason,payload) VALUES(?,?,?,?,?,?,?,?,?,?)', (trade['ts'],pos_id,pos.get('symbol'),side,pos.get('setup'),pnl,0,fees,reason,__import__('json').dumps(trade,sort_keys=True)))
        self.db.insert_json('position_events', {'event':'CLOSE' if status=='CLOSED' else 'PARTIAL_CLOSE','trade':trade}, {'ts':utc_now_iso(),'position_id':pos_id,'event':'CLOSE' if status=='CLOSED' else 'PARTIAL_CLOSE','symbol':pos.get('symbol')})
        if status=='CLOSED':
            self.db.execute('UPDATE positions SET status=?, closed_at=?, exit=?, pnl_usd=?, payload=? WHERE id=?', (status,utc_now_iso(),px,pnl,__import__('json').dumps({**pos,'status':status,'exit':px,'pnl_usd':pnl},sort_keys=True),pos_id))
        return trade

class ProfitGuard:
    def __init__(self, broker: PaperBroker): self.broker=broker
    def manage(self, prices: Dict[str,float]) -> List[Dict[str,Any]]:
        actions=[]; positions=self.broker.refresh().get('open',[])
        for p in positions:
            sym=p.get('symbol'); price=fnum(prices.get(sym));
            if not price: continue
            side=p.get('side'); entry=fnum(p.get('entry_price')); sl=fnum(p.get('stop_loss')); tp1=fnum(p.get('take_profit_1')); tp2=fnum(p.get('take_profit_2'))
            risk_abs=abs(entry-sl) if entry and sl else 0
            if risk_abs<=0: continue
            r=((price-entry)/risk_abs) if side=='LONG' else ((entry-price)/risk_abs)
            p['mfe_r']=max(fnum(p.get('mfe_r')), r); p['mae_r']=min(fnum(p.get('mae_r')), r); p['last_price']=price
            stop_hit=(price<=sl if side=='LONG' else price>=sl)
            tp1_hit=(price>=tp1 if side=='LONG' else price<=tp1)
            tp2_hit=(price>=tp2 if side=='LONG' else price<=tp2)
            if stop_hit:
                actions.append(self.broker.close_position(p,price,'STOP_LOSS',1.0)); continue
            if tp2_hit:
                actions.append(self.broker.close_position(p,price,'TAKE_PROFIT_2',1.0)); continue
            if tp1_hit and not p.get('tp1_done'):
                p['tp1_done']=True; p['trail_active']=True; p['stop_loss']=entry
                actions.append(self.broker.close_position(p,price,'TAKE_PROFIT_1_PARTIAL',0.35))
                self._update_payload(p); continue
            # profit lock: if MFE > 0.7R lock some profit; giveback close partial
            if fnum(p.get('mfe_r'))>=0.70:
                if side=='LONG': p['stop_loss']=max(fnum(p.get('stop_loss')), entry + risk_abs*0.15)
                else: p['stop_loss']=min(fnum(p.get('stop_loss')), entry - risk_abs*0.15)
            if fnum(p.get('mfe_r'))>=1.20 and r < fnum(p.get('mfe_r'))*0.45:
                actions.append(self.broker.close_position(p,price,'GIVEBACK_PROTECTION',0.5)); continue
            self._update_payload(p)
        return actions
    def _update_payload(self, p: Dict[str,Any]) -> None:
        self.broker.db.execute('UPDATE positions SET payload=? WHERE id=?', (__import__('json').dumps(p,sort_keys=True), p.get('id')))
