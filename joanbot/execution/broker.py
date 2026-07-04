from __future__ import annotations
import uuid
from dataclasses import asdict
from typing import Any, Dict, List
from ..config import CFG
from ..models import Position
from ..storage import get_db
from ..utils import fnum, utc_now_iso, clamp
from .contract import evaluate_execution, record_execution_rejection
from ..institutional.nucli_quantitatiu_net import get_core as NucliQuantitatiuNet
from ..institutional.gestio_posicio_institucional_neta import get_core as GestioPosicioInstitucionalNeta
try:
    from core.runtime_control_v25 import enforce_runtime_control, RuntimeControlReject
except Exception:
    enforce_runtime_control = None
    class RuntimeControlReject(Exception):
        pass


class PaperBroker:
    def __init__(self):
        self.db = get_db()
        self.nucli_quant = NucliQuantitatiuNet(self.db)
        self.gestio_posicio = GestioPosicioInstitucionalNeta(self.db)
        self.wallet = self.load_wallet()

    def load_wallet(self) -> Dict[str, Any]:
        rows = self.db.query("SELECT payload FROM positions WHERE status='OPEN'")
        opens = []
        for r in rows:
            try:
                import json
                opens.append(json.loads(r['payload']))
            except Exception:
                pass
        pnl = sum(fnum(r['pnl_usd']) for r in self.db.query('SELECT pnl_usd FROM trades'))
        return {'equity': CFG.initial_equity + pnl, 'initial': CFG.initial_equity, 'open': opens, 'closed_pnl': pnl}

    def refresh(self):
        self.wallet = self.load_wallet()
        return self.wallet

    def open_from_decision(self, d) -> Dict[str, Any] | None:
        wallet = self.refresh()
        verdict = evaluate_execution(d, wallet.get('open', []) or [])
        if not verdict.allowed:
            record_execution_rejection(self.db, d, verdict)
            return None

        if enforce_runtime_control is not None:
            try:
                enforce_runtime_control(d, base_min_score=40.0, open_positions=len(wallet.get('open', []) or []), source='broker_control_natiu')
            except RuntimeControlReject as e:
                record_execution_rejection(self.db, d, type('Verdict', (), {
                    'allowed': False, 'reason': str(e), 'severity': 'WARN',
                    'contract_version': 'CONTROL_OPERATIU_NATIU',
                    'details': {'source': 'broker_control_natiu'}
                })())
                return None

        pid = str(uuid.uuid4())[:12]
        entry = self._slipped_entry(d.entry, d.side, d.size_usd)
        pos = Position(pid, d.symbol, d.side, d.setup, d.size_usd, entry, d.stop_loss, d.take_profit_1, d.take_profit_2, utc_now_iso(), meta={'decision': d.to_dict()})
        payload = asdict(pos)
        payload['remaining_pct'] = 1.0
        payload['gestio_accions'] = {}
        try:
            pla = self.gestio_posicio.crea_pla_inicial(payload)
            payload['pla_gestio_posicio_institucional_neta'] = pla
        except Exception as e:
            try:
                self.db.runtime_event('gestio_posicio_institucional_neta', 'ERROR', 'pla_inicial_fallit', {'error': repr(e), 'position_id': pid})
            except Exception:
                pass
        import json
        self.db.execute('INSERT OR REPLACE INTO positions(id,opened_at,closed_at,symbol,side,setup,status,entry,exit,size_usd,pnl_usd,payload) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)', (pos.id, pos.opened_at, None, pos.symbol, pos.side, pos.setup, pos.status, pos.entry_price, None, pos.size_usd, None, json.dumps(payload, sort_keys=True)))
        self.db.insert_json('position_events', {'event': 'OPEN', 'position': payload}, {'ts': utc_now_iso(), 'position_id': pid, 'event': 'OPEN', 'symbol': pos.symbol})
        return payload

    def _slipped_entry(self, price: float, side: str, size_usd: float) -> float:
        slip = CFG.slippage_base_bps / 10000.0
        return price * (1 + slip) if side == 'LONG' else price * (1 - slip)

    def mark_positions(self, prices: Dict[str, float]) -> List[Dict[str, Any]]:
        # GESTIO_POSICIO_V4_QUANT_UNICA: el broker delega en una única autoritat.
        return self._manage_positions_v4_quant(prices)


    def _manage_positions_v4_quant(self, prices: Dict[str, float]) -> List[Dict[str, Any]]:
        """Execució de sortida V4. No decideix edge; només aplica l'autoritat de gestió."""
        import json

        actions: List[Dict[str, Any]] = []
        positions = self.refresh().get('open', []) or []

        def update_payload(p: Dict[str, Any]) -> None:
            try:
                self.db.execute(
                    'UPDATE positions SET payload=? WHERE id=?',
                    (json.dumps(p, sort_keys=True), p.get('id'))
                )
            except Exception:
                pass

        for p in list(positions):
            try:
                sym = str(p.get('symbol') or '').upper()
                price = fnum(prices.get(sym) if isinstance(prices, dict) else None)
                if price <= 0:
                    continue

                side = str(p.get('side') or '').upper()
                entry = fnum(p.get('entry_price') or p.get('entry'))
                sl = fnum(p.get('stop_loss'))
                risk_abs = abs(entry - sl) if entry > 0 and sl > 0 else 0.0
                if risk_abs <= 0:
                    continue

                r = ((price - entry) / risk_abs) if side == 'LONG' else ((entry - price) / risk_abs)

                p['mfe_r'] = max(fnum(p.get('mfe_r')), r)
                p['mae_r'] = min(fnum(p.get('mae_r')), r)
                p['last_price'] = price
                p['gestio_mostres_n'] = int(fnum(p.get('gestio_mostres_n'), 0)) + 1

                stop_hit = (price <= fnum(p.get('stop_loss')) if side == 'LONG' else price >= fnum(p.get('stop_loss')))
                if stop_hit:
                    if self.gestio_posicio.reserva_accio(p, 'TANCAR_TOTAL', 'STOP_LOSS', 'hard_stop', 1.0, price, r):
                        actions.append(self.close_position(p, price, 'STOP_LOSS', 1.0))
                    continue

                decisio = self.gestio_posicio.decideix_accio(p, price, r, risk_abs)
                p['gestio_posicio_institucional_neta'] = decisio

                action = decisio.get('action') if isinstance(decisio, dict) else None
                reason = decisio.get('reason', 'GESTIO_POSICIO_V4') if isinstance(decisio, dict) else 'GESTIO_POSICIO_V4'
                stage = decisio.get('stage') or decisio.get('marca_accio') or 'stage'

                def update_stop_from_r(lock_r: float) -> None:
                    if side == 'LONG':
                        p['stop_loss'] = max(fnum(p.get('stop_loss')), entry + risk_abs * lock_r)
                    else:
                        old = fnum(p.get('stop_loss'))
                        candidate = entry - risk_abs * lock_r
                        p['stop_loss'] = min(old if old > 0 else candidate, candidate)

                    p.setdefault('gestio_accions', {})['lock_r'] = max(
                        fnum(p.get('gestio_accions', {}).get('lock_r'), -999.0),
                        lock_r
                    )

                if action == 'ACTUALITZAR_STOP':
                    update_stop_from_r(fnum(decisio.get('nou_stop_r'), 0.02))
                    p.setdefault('gestio_accions', {})[stage] = True
                    update_payload(p)
                    continue

                if action in {'TANCAR_TOTAL', 'TANCAR_PARCIAL'}:
                    close_pct = 1.0 if action == 'TANCAR_TOTAL' else clamp(
                        fnum(decisio.get('close_pct'), 0.25),
                        0.01,
                        0.90
                    )

                    if not self.gestio_posicio.reserva_accio(p, action, reason, stage, close_pct, price, r):
                        update_payload(p)
                        continue

                    p.setdefault('gestio_accions', {})[stage] = True

                    if decisio.get('nou_stop_r') is not None:
                        update_stop_from_r(fnum(decisio.get('nou_stop_r')))

                    actions.append(self.close_position(p, price, reason, close_pct))
                    continue

                update_payload(p)

            except Exception as e:
                try:
                    self.db.runtime_event(
                        'gestio_posicio_institucional_neta',
                        'ERROR',
                        'manage_v4_quant_fallit',
                        {'error': repr(e), 'position_id': p.get('id')}
                    )
                except Exception:
                    pass

        self.refresh()
        return actions

    def close_position(self, pos: Dict[str, Any], exit_price: float, reason: str, close_pct: float = 1.0) -> Dict[str, Any]:
        """Tanca una fracció de la posició actual.

        Important: close_pct és fracció de la mida actual restant, no de la mida original.
        Això evita errors acumulats després de parcials múltiples.
        """
        import json
        side = pos.get('side')
        entry = fnum(pos.get('entry_price'))
        current_size = fnum(pos.get('size_usd'))
        close_pct = clamp(fnum(close_pct, 1.0), 0.0, 1.0)
        size = current_size * close_pct
        slip = CFG.slippage_base_bps / 10000.0
        px = exit_price * (1 - slip) if side == 'LONG' else exit_price * (1 + slip)
        gross = (px - entry) / entry * size if side == 'LONG' else (entry - px) / entry * size
        fees = size * CFG.fee_rate * 2
        pnl = gross - fees
        pos_id = pos.get('id')
        old_remaining = fnum(pos.get('remaining_pct'), 1.0)
        remaining = 0.0 if close_pct >= 0.999 else max(0.0, old_remaining * (1.0 - close_pct))
        status = 'CLOSED' if remaining <= 1e-9 else 'OPEN'
        trade = {
            'ts': utc_now_iso(), 'position_id': pos_id, 'symbol': pos.get('symbol'), 'side': side, 'setup': pos.get('setup'),
            'entry': entry, 'exit': px, 'size_usd': size, 'pnl_usd': pnl, 'fees': fees, 'reason': reason,
            'close_pct': close_pct, 'close_pct_base': 'CURRENT_REMAINING_POSITION', 'remaining_pct_before': old_remaining,
            'remaining_pct_after': remaining,
        }
        r_info = self.nucli_quant.calcula_r_live(pos, trade)
        trade['pnl_r'] = r_info.get('resultat_r', 0.0)
        self.db.execute('INSERT INTO trades(ts,position_id,symbol,side,setup,pnl_usd,pnl_r,fees,reason,payload) VALUES(?,?,?,?,?,?,?,?,?,?)', (trade['ts'], pos_id, pos.get('symbol'), side, pos.get('setup'), pnl, trade['pnl_r'], fees, reason, json.dumps(trade, sort_keys=True)))
        try:
            rows = self.db.query("SELECT * FROM trades WHERE position_id=? ORDER BY id DESC LIMIT 1", (pos_id,))
            if rows:
                res = self.nucli_quant.registra_operacio_live(pos, dict(rows[0]))
                if isinstance(res, dict):
                    trade['pnl_r'] = res.get('resultat_r', trade['pnl_r'])
                    trade['nucli_quantitatiu_net'] = res
        except Exception as e:
            try:
                self.db.runtime_event('nucli_quantitatiu_net', 'ERROR', 'registre_live_fallit', {'error': repr(e), 'position_id': pos_id})
            except Exception:
                pass
        try:
            gestio = self.gestio_posicio.registra_tancament(pos, trade)
            trade['gestio_posicio_institucional_neta'] = gestio
        except Exception as e:
            try:
                self.db.runtime_event('gestio_posicio_institucional_neta', 'ERROR', 'registre_tancament_fallit', {'error': repr(e), 'position_id': pos_id})
            except Exception:
                pass
        self.db.insert_json('position_events', {'event': 'CLOSE' if status == 'CLOSED' else 'PARTIAL_CLOSE', 'trade': trade}, {'ts': utc_now_iso(), 'position_id': pos_id, 'event': 'CLOSE' if status == 'CLOSED' else 'PARTIAL_CLOSE', 'symbol': pos.get('symbol')})
        cumulative_pnl = fnum(pos.get('pnl_usd')) + pnl
        if status == 'CLOSED':
            payload = {**pos, 'status': status, 'exit': px, 'pnl_usd': cumulative_pnl, 'remaining_pct': 0.0, 'size_usd': 0.0}
            self.db.execute('UPDATE positions SET status=?, closed_at=?, exit=?, pnl_usd=?, size_usd=?, payload=? WHERE id=?', (status, utc_now_iso(), px, cumulative_pnl, 0.0, json.dumps(payload, sort_keys=True), pos_id))
        else:
            pos['size_usd'] = current_size * (1.0 - close_pct)
            pos['remaining_pct'] = remaining
            pos['pnl_usd'] = cumulative_pnl
            self.db.execute('UPDATE positions SET status=?, size_usd=?, pnl_usd=?, payload=? WHERE id=?', (status, pos['size_usd'], cumulative_pnl, json.dumps(pos, sort_keys=True), pos_id))
        return trade


class ProfitGuard:
    def __init__(self, broker: PaperBroker):
        self.broker = broker

    def manage(self, prices: Dict[str, float]) -> List[Dict[str, Any]]:
        # Adaptador de compatibilitat: la decisió real és a PaperBroker._manage_positions_v4_quant.
        return self.broker._manage_positions_v4_quant(prices)

    def _update_payload(self, p: Dict[str, Any]) -> None:
        import json
        self.broker.db.execute('UPDATE positions SET payload=? WHERE id=?', (json.dumps(p, sort_keys=True), p.get('id')))
