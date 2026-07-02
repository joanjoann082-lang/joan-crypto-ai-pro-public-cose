from __future__ import annotations
import json, sqlite3, threading, time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from ..config import CFG, DATA_DIR
from ..utils import utc_now_iso, atomic_write_json

_SCHEMA = r'''
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
CREATE TABLE IF NOT EXISTS candles(
  symbol TEXT, interval TEXT, open_time INTEGER, close_time INTEGER,
  open REAL, high REAL, low REAL, close REAL, volume REAL, quote_volume REAL,
  trades INTEGER, taker_buy_base REAL, taker_buy_quote REAL,
  PRIMARY KEY(symbol, interval, open_time)
);
CREATE TABLE IF NOT EXISTS market_snapshots(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, symbol TEXT, price REAL, payload TEXT
);
CREATE TABLE IF NOT EXISTS derivatives_snapshots(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, symbol TEXT, funding REAL, open_interest REAL,
  oi_chg_5m REAL, oi_chg_1h REAL, long_short REAL, top_long_short REAL, taker_buy_ratio REAL, basis_bps REAL, payload TEXT
);
CREATE TABLE IF NOT EXISTS orderflow_snapshots(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, symbol TEXT, spread_bps REAL, depth_10bps REAL,
  depth_25bps REAL, imbalance_25bps REAL, wall_pressure REAL, cvd_proxy REAL, payload TEXT
);
CREATE TABLE IF NOT EXISTS macro_snapshots(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, risk_score REAL, mode TEXT, payload TEXT
);
CREATE TABLE IF NOT EXISTS news_events(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, source TEXT, category TEXT, severity REAL, direction TEXT, title TEXT, url TEXT, payload TEXT
);
CREATE TABLE IF NOT EXISTS features(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, symbol TEXT, regime TEXT, session TEXT, volatility_bucket TEXT,
  news_bucket TEXT, data_quality REAL, payload TEXT
);
CREATE TABLE IF NOT EXISTS decisions(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, mode TEXT, symbol TEXT, action TEXT, side TEXT, setup TEXT,
  final_score REAL, confidence REAL, size_usd REAL, payload TEXT
);
CREATE TABLE IF NOT EXISTS positions(
  id TEXT PRIMARY KEY, opened_at TEXT, closed_at TEXT, symbol TEXT, side TEXT, setup TEXT,
  status TEXT, entry REAL, exit REAL, size_usd REAL, pnl_usd REAL, payload TEXT
);
CREATE TABLE IF NOT EXISTS position_events(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, position_id TEXT, event TEXT, symbol TEXT, payload TEXT
);
CREATE TABLE IF NOT EXISTS trades(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, position_id TEXT, symbol TEXT, side TEXT, setup TEXT,
  pnl_usd REAL, pnl_r REAL, fees REAL, reason TEXT, payload TEXT
);
CREATE TABLE IF NOT EXISTS edge_memory(
  key TEXT, source TEXT, updated_at TEXT, n REAL, wins REAL, losses REAL,
  sum_r REAL, sum_pos_r REAL, sum_neg_r REAL, max_dd_r REAL, payload TEXT,
  PRIMARY KEY(key, source)
);
CREATE TABLE IF NOT EXISTS forward_cases(
  id TEXT PRIMARY KEY, created_at TEXT, due_at TEXT, horizon_min INTEGER, symbol TEXT,
  side TEXT, action TEXT, setup TEXT, entry REAL, sl REAL, tp1 REAL, tp2 REAL, status TEXT, payload TEXT
);
CREATE TABLE IF NOT EXISTS forward_results(
  id INTEGER PRIMARY KEY AUTOINCREMENT, case_id TEXT, resolved_at TEXT, symbol TEXT, outcome TEXT,
  result_r REAL, mfe_r REAL, mae_r REAL, payload TEXT
);
CREATE TABLE IF NOT EXISTS alerts(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, severity TEXT, kind TEXT, symbol TEXT, dedup_key TEXT, payload TEXT
);
CREATE TABLE IF NOT EXISTS runtime_events(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, component TEXT, level TEXT, message TEXT, payload TEXT
);
CREATE INDEX IF NOT EXISTS idx_decisions_ts ON decisions(ts);
CREATE INDEX IF NOT EXISTS idx_features_symbol_ts ON features(symbol, ts);
CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol, setup);
CREATE INDEX IF NOT EXISTS idx_forward_status ON forward_cases(status, due_at);
'''

class DB:
    def __init__(self, path: Path = CFG.db_path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False, timeout=30)
        self._conn.row_factory = sqlite3.Row
        self.init()

    def init(self) -> None:
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def execute(self, sql: str, params: Iterable[Any] = ()): 
        with self._lock:
            cur = self._conn.execute(sql, tuple(params))
            self._conn.commit()
            return cur

    def query(self, sql: str, params: Iterable[Any] = ()) -> List[Dict[str, Any]]:
        with self._lock:
            cur = self._conn.execute(sql, tuple(params))
            return [dict(r) for r in cur.fetchall()]

    def upsert_candles(self, symbol: str, interval: str, candles: List[Dict[str, Any]]) -> None:
        rows=[]
        for c in candles:
            rows.append((symbol, interval, int(c.get('open_time',0)), int(c.get('close_time',0)), float(c.get('open',0)), float(c.get('high',0)), float(c.get('low',0)), float(c.get('close',0)), float(c.get('volume',0)), float(c.get('quote_volume',0)), int(c.get('trades',0)), float(c.get('taker_buy_base',0)), float(c.get('taker_buy_quote',0))))
        if not rows: return
        with self._lock:
            self._conn.executemany('''INSERT OR REPLACE INTO candles(symbol,interval,open_time,close_time,open,high,low,close,volume,quote_volume,trades,taker_buy_base,taker_buy_quote)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''', rows)
            self._conn.commit()

    def latest_candles(self, symbol: str, interval: str, limit: int = 500) -> List[Dict[str, Any]]:
        return list(reversed(self.query('SELECT * FROM candles WHERE symbol=? AND interval=? ORDER BY open_time DESC LIMIT ?', (symbol, interval, limit))))

    def insert_json(self, table: str, obj: Dict[str, Any], columns: Optional[Dict[str, Any]]=None) -> None:
        columns = columns or {}
        cols = list(columns.keys()) + ['payload']
        vals = list(columns.values()) + [json.dumps(obj, ensure_ascii=False, sort_keys=True)]
        q = ','.join('?' for _ in cols)
        self.execute(f"INSERT INTO {table}({','.join(cols)}) VALUES({q})", vals)

    def record_decision(self, mode: str, decision: Dict[str, Any]) -> None:
        self.insert_json('decisions', decision, {'ts': decision.get('ts', utc_now_iso()), 'mode': mode, 'symbol': decision.get('symbol'), 'action': decision.get('action'), 'side': decision.get('side'), 'setup': decision.get('setup'), 'final_score': decision.get('final_score',0), 'confidence': decision.get('confidence',0), 'size_usd': decision.get('size_usd',0)})

    def runtime_event(self, component: str, level: str, message: str, payload: Optional[Dict[str, Any]]=None) -> None:
        self.insert_json('runtime_events', {'ts': utc_now_iso(), 'component': component, 'level': level, 'message': message, 'payload': payload or {}}, {'ts': utc_now_iso(), 'component': component, 'level': level, 'message': message})

    def state(self) -> Dict[str, Any]:
        tables=['candles','market_snapshots','features','decisions','positions','trades','forward_cases','runtime_events']
        out={'db_path': str(self.path)}
        for t in tables:
            try: out[t]=self.query(f'SELECT COUNT(*) c FROM {t}')[0]['c']
            except Exception: out[t]=None
        return out

_DB: Optional[DB] = None

def get_db() -> DB:
    global _DB
    if _DB is None:
        _DB=DB()
    return _DB
