
from pathlib import Path
import sys, json
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from joanbot.storage import get_db
from joanbot.institutional.gestio_posicio_institucional_neta import get_core

db = get_db(); core = get_core(db)
pos = {
  "id": "PROVA_GESTIO_POSICIO_V2", "symbol": "BTCUSDT", "side": "LONG", "setup": "PROVA_GESTIO",
  "entry_price": 100.0, "stop_loss": 95.0, "take_profit_1": 107.0, "take_profit_2": 112.0, "size_usd": 10000,
  "remaining_pct": 1.0, "gestio_accions": {},
  "meta": {"decision": {"feature_summary": {"regime":"TEST", "mapa_causal": {"zona":"RESISTENCIA", "cvd":"POSITIU", "fractal":"HH_HL", "liquidacions":"LIQ_NEUTRA"}}}}
}
pla = core.crea_pla_inicial(pos)
assert pla.get("created"), pla
risk = 5.0
seq = [101, 103, 106, 104, 102]
last = None
for px in seq:
    r = (px - 100.0) / risk
    last = core.decideix_accio(pos, px, r, risk)
    pos['mfe_r'] = max(pos.get('mfe_r', 0), r)
    pos['mae_r'] = min(pos.get('mae_r', 0), r)
trade = {"id": "PROVA_GESTIO_POSICIO_TRADE_V2", "position_id": pos['id'], "symbol": "BTCUSDT", "side": "LONG", "setup": "PROVA_GESTIO", "pnl_r": 0.4, "reason": "PROVA", "close_pct": 1.0}
res = core.registra_tancament(pos, trade)
pol = core.politica_per_posicio(pos)
counts = {t: db.query(f"SELECT COUNT(*) c FROM {t}")[0]['c'] for t in ['plans_gestio_posicio_neta','mostres_posicio_neta','tancaments_posicio_neta','politica_gestio_posicio_neta','simulacions_sortida_neta']}
assert counts['plans_gestio_posicio_neta'] >= 1, counts
assert counts['simulacions_sortida_neta'] >= 1, counts
print("PROVA_GESTIO_POSICIO_INSTITUCIONAL_OK", json.dumps({"pla": pla, "ultima_decisio": last, "res": res, "politica": pol, "counts": counts}, ensure_ascii=False, sort_keys=True, default=str))



# === HIGIENE_PROVES_GESTIO_POSICIO_V1 ===
# Les proves funcionals poden crear mostres sintètiques, però no poden quedar dins l'aprenentatge net.
try:
    import sqlite3 as _sqlite3
    from pathlib import Path as _Path

    _DB = _Path("data/joanbot_v14.sqlite")
    if _DB.exists():
        _con = _sqlite3.connect(str(_DB))
        _con.row_factory = _sqlite3.Row
        _taules = [
            "plans_gestio_posicio_neta",
            "mostres_posicio_neta",
            "tancaments_posicio_neta",
            "politica_gestio_posicio_neta",
            "decisions_gestio_posicio_neta",
            "simulacions_sortida_neta",
            "auditoria_gestio_posicio_neta",
        ]
        _marcadors = ["PROVA_GESTIO", "PROVA_GESTIO_POSICIO"]
        _total = 0

        def _qcol(c):
            return '"' + c.replace('"', '""') + '"'

        for _t in _taules:
            try:
                _cols_info = _con.execute(f"PRAGMA table_info({_t})").fetchall()
                if not _cols_info:
                    continue
                _text_cols = []
                for _r in _cols_info:
                    _name = _r[1]
                    _typ = str(_r[2] or "").upper()
                    if "TEXT" in _typ or _name.lower() in {"key", "setup", "payload", "reason", "accio", "etiqueta", "symbol", "side", "posicio_id"}:
                        _text_cols.append(_name)
                if not _text_cols:
                    continue
                _parts = []
                _params = []
                for _c in _text_cols:
                    for _m in _marcadors:
                        _parts.append(f"UPPER(CAST({_qcol(_c)} AS TEXT)) LIKE ?")
                        _params.append(f"%{_m.upper()}%")
                _where = " OR ".join(_parts)
                _n = _con.execute(f"SELECT COUNT(*) c FROM {_t} WHERE {_where}", _params).fetchone()["c"]
                if _n:
                    _con.execute(f"DELETE FROM {_t} WHERE {_where}", _params)
                    _total += int(_n)
            except Exception:
                pass
        _con.commit()
        print("HIGIENE_PROVES_GESTIO_POSICIO_OK", _total)
except Exception as _e:
    print("HIGIENE_PROVES_GESTIO_POSICIO_WARN", repr(_e))

