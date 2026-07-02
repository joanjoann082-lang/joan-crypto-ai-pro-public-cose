from __future__ import annotations
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Tuple

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.getenv("JOANBOT_DATA_DIR", ROOT / "data"))
LOG_DIR = Path(os.getenv("JOANBOT_LOG_DIR", ROOT / "logs"))
BACKUP_DIR = Path(os.getenv("JOANBOT_BACKUP_DIR", ROOT / "backups"))
DB_PATH = DATA_DIR / "joanbot_v14.sqlite"
STATE_PATH = DATA_DIR / "runtime_state.json"
ENV_PATH = ROOT / ".env"


def load_env(path: Path = ENV_PATH) -> None:
    if not path.exists():
        return
    for raw in path.read_text(errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

load_env()


def _float(key: str, default: float) -> float:
    try:
        return float(os.getenv(key, default))
    except Exception:
        return default


def _int(key: str, default: int) -> int:
    try:
        return int(float(os.getenv(key, default)))
    except Exception:
        return default


def _bool(key: str, default: bool) -> bool:
    v = os.getenv(key)
    if v is None:
        return default
    return str(v).lower() in {"1", "true", "yes", "on"}

@dataclass(frozen=True)
class Config:
    symbols: Tuple[str, ...] = tuple(x.strip().upper() for x in os.getenv("SYMBOLS", "BTCUSDT,ETHUSDT").split(",") if x.strip())
    quote: str = "USDT"
    initial_equity: float = _float("INITIAL_EQUITY", 100000.0)
    loop_market_sec: int = _int("LOOP_MARKET_SEC", 8)
    loop_position_sec: int = _int("LOOP_POSITION_SEC", 5)
    loop_decision_sec: int = _int("LOOP_DECISION_SEC", 25)
    loop_context_sec: int = _int("LOOP_CONTEXT_SEC", 45)
    loop_forward_sec: int = _int("LOOP_FORWARD_SEC", 60)
    loop_health_sec: int = _int("LOOP_HEALTH_SEC", 60)
    loop_macro_sec: int = _int("LOOP_MACRO_SEC", 300)
    stale_market_sec: int = _int("STALE_MARKET_SEC", 90)
    fee_rate: float = _float("FEE_RATE", 0.00045)
    slippage_base_bps: float = _float("SLIPPAGE_BASE_BPS", 1.5)
    max_total_exposure_pct: float = _float("MAX_TOTAL_EXPOSURE_PCT", 0.12)
    max_symbol_exposure_pct: float = _float("MAX_SYMBOL_EXPOSURE_PCT", 0.08)
    max_side_exposure_pct: float = _float("MAX_SIDE_EXPOSURE_PCT", 0.10)
    base_risk_pct: float = _float("BASE_RISK_PCT", 0.0035)
    max_risk_pct: float = _float("MAX_RISK_PCT", 0.0125)
    probe_risk_pct: float = _float("PROBE_RISK_PCT", 0.0010)
    min_notional: float = _float("MIN_NOTIONAL", 150.0)
    max_positions: int = _int("MAX_POSITIONS", 6)
    max_per_symbol: int = _int("MAX_PER_SYMBOL", 3)
    open_threshold: float = _float("OPEN_THRESHOLD", 74.0)
    probe_threshold: float = _float("PROBE_THRESHOLD", 58.0)
    high_quality_probe_threshold: float = _float("HIGH_QUALITY_PROBE_THRESHOLD", 68.0)
    allow_short: bool = _bool("ALLOW_SHORT", True)
    allow_long: bool = _bool("ALLOW_LONG", True)
    telegram_enabled: bool = _bool("TELEGRAM_ENABLED", False)
    dashboard_host: str = os.getenv("DASHBOARD_HOST", "0.0.0.0")
    dashboard_port: int = _int("DASHBOARD_PORT", 8164)
    forward_horizons: Tuple[int, ...] = (15, 60, 240)
    news_high_block_threshold: float = _float("NEWS_HIGH_BLOCK_THRESHOLD", 78.0)
    event_risk_size_floor: float = _float("EVENT_RISK_SIZE_FLOOR", 0.35)
    websocket_enabled: bool = _bool("WEBSOCKET_ENABLED", False)
    db_path: Path = field(default=DB_PATH)

CFG = Config()
for _d in (DATA_DIR, LOG_DIR, BACKUP_DIR):
    _d.mkdir(parents=True, exist_ok=True)
