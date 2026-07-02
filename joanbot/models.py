from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional
from .utils import utc_now_iso

@dataclass
class Candle:
    symbol: str
    interval: str
    open_time: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    close_time: int = 0
    quote_volume: float = 0.0
    trades: int = 0
    taker_buy_base: float = 0.0
    taker_buy_quote: float = 0.0

    def to_dict(self): return asdict(self)

@dataclass
class MarketSnapshot:
    symbol: str
    ts: str = field(default_factory=utc_now_iso)
    price: float = 0.0
    mark_price: float = 0.0
    candles: Dict[str, List[Dict[str, Any]]] = field(default_factory=dict)
    orderbook: Dict[str, Any] = field(default_factory=dict)
    trades: List[Dict[str, Any]] = field(default_factory=list)
    derivatives: Dict[str, Any] = field(default_factory=dict)
    liquidations: Dict[str, Any] = field(default_factory=dict)
    quality: Dict[str, Any] = field(default_factory=dict)

@dataclass
class MacroSnapshot:
    ts: str = field(default_factory=utc_now_iso)
    vix: float = 0.0
    qqq_chg: float = 0.0
    spy_chg: float = 0.0
    dia_chg: float = 0.0
    dxy_chg: float = 0.0
    us10y_chg: float = 0.0
    gold_chg: float = 0.0
    oil_chg: float = 0.0
    fear_greed: float = 50.0
    risk_score: float = 50.0
    mode: str = "NEUTRAL"
    notes: List[str] = field(default_factory=list)

@dataclass
class NewsEvent:
    ts: str
    source: str
    title: str
    url: str = ""
    category: str = "general"
    severity: float = 0.0
    direction: str = "UNKNOWN"
    affected: List[str] = field(default_factory=list)

@dataclass
class FeatureSnapshot:
    symbol: str
    ts: str
    price: float
    regime: str
    session: str
    volatility_bucket: str
    news_bucket: str
    data_quality: float
    alpha_context: Dict[str, Any]
    technical: Dict[str, Any]
    levels: Dict[str, Any]
    micro: Dict[str, Any]
    derivatives: Dict[str, Any]
    macro: Dict[str, Any]
    news: Dict[str, Any]

@dataclass
class Candidate:
    symbol: str
    side: str
    setup: str
    trade_type: str
    raw_alpha: float
    timing_score: float
    invalidation: float
    tp1: float
    tp2: float
    reason: List[str] = field(default_factory=list)
    meta: Dict[str, Any] = field(default_factory=dict)

@dataclass
class EdgeView:
    key: str
    status: str
    effective_n: float
    winrate: float
    expectancy_r: float
    profit_factor: float
    lcb: float
    score_adjustment: float
    size_multiplier: float
    reasons: List[str] = field(default_factory=list)

@dataclass
class RiskPlan:
    allowed: bool
    size_usd: float
    risk_usd: float
    risk_pct: float
    leverage: float
    stop_pct: float
    size_multiplier: float
    reasons: List[str] = field(default_factory=list)

@dataclass
class Decision:
    symbol: str
    action: str
    side: str
    setup: str
    trade_type: str
    final_score: float
    confidence: float
    size_usd: float
    entry: float
    stop_loss: float
    take_profit_1: float
    take_profit_2: float
    reasons: List[str]
    score_layers: Dict[str, float]
    risk: Dict[str, Any]
    edge: Dict[str, Any]
    feature_summary: Dict[str, Any]
    ts: str = field(default_factory=utc_now_iso)

    def to_dict(self): return asdict(self)

@dataclass
class Position:
    id: str
    symbol: str
    side: str
    setup: str
    size_usd: float
    entry_price: float
    stop_loss: float
    take_profit_1: float
    take_profit_2: float
    opened_at: str
    status: str = "OPEN"
    tp1_done: bool = False
    remaining_pct: float = 1.0
    trail_active: bool = False
    mfe_r: float = 0.0
    mae_r: float = 0.0
    last_price: float = 0.0
    meta: Dict[str, Any] = field(default_factory=dict)
