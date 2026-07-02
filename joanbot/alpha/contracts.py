from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


VERSION = "ALPHA_CONTRACTS_V1_INSTITUTIONAL"


@dataclass(frozen=True)
class AlphaIdentity:
    symbol: str
    side: str
    setup: str
    profile: str
    horizon_min: int
    context_bucket: str

    def key(self) -> str:
        return "|".join([
            self.symbol.upper(),
            self.side.upper(),
            self.setup.upper(),
            self.profile.upper(),
            str(int(self.horizon_min)),
            self.context_bucket.upper(),
        ])

    def cluster_key(self) -> str:
        return "|".join([
            self.symbol.upper(),
            self.side.upper(),
            str(int(self.horizon_min)),
            self.context_bucket.upper(),
        ])

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class AlphaEvidence:
    n: int = 0

    mean_r: float = 0.0
    median_r: float = 0.0
    shrunk_expectancy_r: float = 0.0

    winrate: float = 0.0
    profit_factor: Optional[float] = None
    calibrated_pf: float = 0.0

    avg_mfe_r: float = 0.0
    avg_mae_r: float = 0.0
    p10_r: float = 0.0
    worst_r: float = 0.0
    std_r: float = 0.0

    train_exp_r: float = 0.0
    validation_exp_r: float = 0.0
    recent_exp_r: float = 0.0
    older_exp_r: float = 0.0

    fold_positive_n: int = 0
    fold_pass: bool = False

    decay_state: str = "UNKNOWN"
    tail_risk_state: str = "UNKNOWN"
    path_quality_score: float = 0.0
    stability_score: float = 0.0

    source: str = "UNKNOWN"
    payload: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class AlphaGovernanceVerdict:
    identity: AlphaIdentity
    evidence: AlphaEvidence

    current_context_bucket: str = "UNKNOWN"
    current_context_fit: float = 0.0

    cluster_rank: int = 999
    is_cluster_leader: bool = False

    governance_score: float = 0.0
    promotion_score: float = 0.0

    lifecycle_state: str = "DISCOVERY"
    allowed_paper_micro_canary: bool = False
    allowed_direct_open: bool = False

    size_cap_usd: float = 0.0
    max_daily_per_alpha: int = 0
    max_daily_global: int = 0

    thesis: str = ""
    counter_thesis: str = ""
    invalidation: str = ""
    interpretation: str = ""
    next_requirement: str = ""

    reasons: List[str] = field(default_factory=list)
    payload: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["identity"] = self.identity.to_dict()
        d["evidence"] = self.evidence.to_dict()
        return d


@dataclass
class AlphaPromotionContract:
    alpha_key: str
    cluster_key: str
    symbol: str
    side: str
    setup: str
    profile: str
    horizon_min: int
    context_bucket: str

    allowed_paper_micro_canary: bool
    allowed_direct_open: bool

    size_cap_usd: float
    max_daily_per_alpha: int
    max_daily_global: int

    governance_score: float
    promotion_score: float
    required_execution_mode: str

    reasons: List[str] = field(default_factory=list)
    payload: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
