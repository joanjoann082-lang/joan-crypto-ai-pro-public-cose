from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional

from .evidence_engine_v1 import EvidenceEngineV1, fnum
from .canary_promotion_gate_v1 import CanaryPromotionGateV1

VERSION = "STATISTICAL_EDGE_AUTHORITY_V1_4_R_NORMALIZED_CANARY_GATE"

MIN_PROBE_EFFECTIVE_N = 3.0
MIN_PROMISING_R_N = 12
MIN_VALIDATED_R_N = 20

PROMISING_MIN_EXP_R = 0.05
PROMISING_MIN_PF = 1.15

VALIDATED_MIN_EXP_R = 0.08
VALIDATED_MIN_PF = 1.25
VALIDATED_MIN_LCB = 0.42

PRIMARY_FORWARD_MIN_N = 20
PRIMARY_FORWARD_MIN_EXP_R = 0.05
PRIMARY_FORWARD_MIN_PF = 1.10

SHADOW_FORWARD_MIN_N = 50
SHADOW_FORWARD_MIN_EXP_R = 0.08
SHADOW_FORWARD_MIN_PF = 1.20
SHADOW_FORWARD_MIN_LCB = 0.42


def pfnum(x: Any) -> Optional[float]:
    try:
        if x is None:
            return None
        return float(x)
    except Exception:
        return None


@dataclass
class AuthorityVerdict:
    version: str
    symbol: str
    side: str
    setup: str

    status: str
    authority_status: str
    edge_status: str
    allow_open: bool
    allow_probe: bool

    score_adjustment: float
    size_multiplier: float
    size_usd_cap: float
    canary_gate: Dict[str, Any]

    n: float
    effective_n: float
    expectancy_r: float
    profit_factor: Optional[float]
    lcb: float

    position_scope: str
    position_metric: str
    position_r_n: int
    position_expectancy_r: float
    position_profit_factor_r: Optional[float]
    position_winrate_lcb_r: float
    position_max_drawdown_r: float

    position_usd_n: int
    position_pnl_usd: float
    position_expectancy_usd: float
    position_profit_factor_usd: Optional[float]

    forward_n: int
    forward_expectancy_r: float
    forward_profit_factor: Optional[float]

    forward_shadow_n: int
    forward_shadow_expectancy_r: float
    forward_shadow_profit_factor: Optional[float]
    forward_shadow_lcb: float

    edge_memory_n: float
    edge_memory_status: str
    edge_memory_expectancy_r: float
    edge_memory_lcb: float

    source_health: Dict[str, Any]
    reasons: List[str]
    evidence: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class StatisticalEdgeAuthorityV1:
    """
    Statistical authority only.

    EvidenceEngineV1 owns evidence collection and normalization.
    This class owns only statistical permission:
    - allow_open
    - allow_probe
    - score_adjustment
    - size_multiplier

    No direct DB reads are allowed here.
    """

    def __init__(self):
        self.evidence_engine = EvidenceEngineV1()
        self.canary_gate = CanaryPromotionGateV1()

    def evaluate(self, candidate: Any, ctx: Dict[str, Any], edge_memory: Dict[str, Any]) -> Dict[str, Any]:
        symbol = str(getattr(candidate, "symbol", "") or "").upper()
        side = str(getattr(candidate, "side", "") or "").upper()
        setup = str(getattr(candidate, "setup", "") or "").upper()

        evidence = self.evidence_engine.build(symbol, side, setup, edge_memory)

        pos = evidence.get("position", {}) or {}
        pos_r = pos.get("r", {}) or {}
        pos_usd = pos.get("usd", {}) or {}
        fw = evidence.get("forward_primary", {}) or {}
        shadow = evidence.get("forward_shadow", {}) or {}
        mem = evidence.get("edge_memory", {}) or {}
        health = evidence.get("source_health", {}) or {}

        r_n = int(fnum(pos_r.get("n"), 0))
        r_exp = fnum(pos_r.get("expectancy"), 0)
        r_pf = pfnum(pos_r.get("profit_factor"))
        r_lcb = fnum(pos_r.get("winrate_lcb"), 0)
        r_dd = fnum(pos_r.get("max_drawdown"), 0)

        usd_n = int(fnum(pos_usd.get("n"), 0))
        usd_sum = fnum(pos_usd.get("sum"), 0)
        usd_exp = fnum(pos_usd.get("expectancy"), 0)
        usd_pf = pfnum(pos_usd.get("profit_factor"))

        fw_n = int(fnum(fw.get("n"), 0))
        fw_exp = fnum(fw.get("expectancy"), 0)
        fw_pf = pfnum(fw.get("profit_factor"))

        shadow_n = int(fnum(shadow.get("n"), 0))
        shadow_exp = fnum(shadow.get("expectancy"), 0)
        shadow_pf = pfnum(shadow.get("profit_factor"))
        shadow_lcb = fnum(shadow.get("winrate_lcb"), 0)

        mem_n = fnum(mem.get("effective_n", mem.get("n")), 0)
        mem_status = str(mem.get("status", "INSUFFICIENT") or "INSUFFICIENT")
        mem_exp = fnum(mem.get("expectancy_r"), 0)
        mem_lcb = fnum(mem.get("lcb"), 0)

        effective_n = fnum(evidence.get("effective_n"), 0)

        reasons: List[str] = []

        authority_status = "INSUFFICIENT_SAMPLE"
        edge_status = "INSUFFICIENT"
        allow_open = False
        allow_probe = False
        score_adj = -30.0
        size_mult = 0.0

        position_metric = str(pos.get("metric_primary", "UNKNOWN"))

        strong_primary_forward = (
            fw_n >= PRIMARY_FORWARD_MIN_N
            and fw_exp > PRIMARY_FORWARD_MIN_EXP_R
            and fw_pf is not None
            and fw_pf >= PRIMARY_FORWARD_MIN_PF
        )

        strong_shadow_forward = (
            shadow_n >= SHADOW_FORWARD_MIN_N
            and shadow_exp > SHADOW_FORWARD_MIN_EXP_R
            and shadow_pf is not None
            and shadow_pf >= SHADOW_FORWARD_MIN_PF
            and shadow_lcb >= SHADOW_FORWARD_MIN_LCB
        )

        if not health.get("positions_table") or not health.get("trades_table"):
            authority_status = "BLOCKED"
            edge_status = "INSUFFICIENT"
            score_adj = -100.0
            reasons.append("FAIL_CLOSED_MISSING_POSITION_OR_TRADE_TABLE")

        elif not health.get("forward_join_schema_usable"):
            authority_status = "BLOCKED"
            edge_status = "INSUFFICIENT"
            score_adj = -100.0
            reasons.append("FAIL_FORWARD_JOIN_SCHEMA_UNUSABLE")

        elif usd_n >= 2 and usd_exp < 0:
            authority_status = "QUARANTINED"
            edge_status = "INSUFFICIENT"
            score_adj = -100.0
            reasons.append("QUARANTINE_NEGATIVE_POSITION_USD_EXPECTANCY")

        elif r_n >= 2 and r_exp < 0:
            authority_status = "QUARANTINED"
            edge_status = "INSUFFICIENT"
            score_adj = -100.0
            reasons.append("QUARANTINE_NEGATIVE_POSITION_R_EXPECTANCY")

        elif setup == "CAPITULATION_REBOUND_LONG" and usd_n >= 2 and usd_exp <= 0:
            authority_status = "QUARANTINED"
            edge_status = "INSUFFICIENT"
            score_adj = -100.0
            reasons.append("QUARANTINE_CAPITULATION_REBOUND_LONG_NEGATIVE_POSITION_EVIDENCE")

        elif r_n >= 5 and r_pf is not None and r_pf < 0.90:
            authority_status = "QUARANTINED"
            edge_status = "INSUFFICIENT"
            score_adj = -100.0
            reasons.append("QUARANTINE_LOW_R_PROFIT_FACTOR")

        elif effective_n < MIN_PROBE_EFFECTIVE_N:
            authority_status = "INSUFFICIENT_SAMPLE"
            edge_status = "INSUFFICIENT"
            score_adj = -30.0
            reasons.append("INSUFFICIENT_TOTAL_EVIDENCE")

        elif r_n == 0 and usd_n > 0:
            authority_status = "R_NORMALIZATION_INCOMPLETE"
            edge_status = "INSUFFICIENT"
            allow_open = False
            allow_probe = False
            score_adj = -40.0
            size_mult = 0.0
            reasons.append("NO_POSITION_R_AVAILABLE_OPEN_FORBIDDEN")

        elif r_n < MIN_PROMISING_R_N:
            if r_exp > 0 or strong_primary_forward:
                authority_status = "PROBE_ONLY"
                edge_status = "INSUFFICIENT"
                allow_probe = True
                score_adj = -15.0
                size_mult = 0.12
                reasons.append("PROBE_ONLY_LOW_R_SAMPLE_PRIMARY_SUPPORT")

            elif strong_shadow_forward:
                authority_status = "PROBE_ONLY"
                edge_status = "INSUFFICIENT"
                allow_probe = True
                score_adj = -22.0
                size_mult = 0.06
                reasons.append("PROBE_ONLY_FROM_STRONG_WAIT_SHADOW_EVIDENCE")

            else:
                authority_status = "INSUFFICIENT_SAMPLE"
                edge_status = "INSUFFICIENT"
                allow_probe = False
                score_adj = -30.0
                size_mult = 0.0
                reasons.append("LOW_R_SAMPLE_NO_PROMOTION_EVIDENCE")

        elif r_n < MIN_VALIDATED_R_N:
            if r_exp >= PROMISING_MIN_EXP_R and r_pf is not None and r_pf >= PROMISING_MIN_PF:
                authority_status = "PROMISING"
                edge_status = "PROMISING"
                allow_open = True
                allow_probe = True
                score_adj = -7.0
                size_mult = 0.30
                reasons.append("PROMISING_R_EDGE_REDUCED_SIZE")

            elif r_exp > 0:
                authority_status = "PROBE_ONLY"
                edge_status = "INSUFFICIENT"
                allow_probe = True
                score_adj = -15.0
                size_mult = 0.12
                reasons.append("PROBE_ONLY_POSITIVE_R_EDGE_WEAK_PF")

            else:
                authority_status = "QUARANTINED"
                edge_status = "INSUFFICIENT"
                score_adj = -100.0
                size_mult = 0.0
                reasons.append("QUARANTINE_R_NOT_PROMOTABLE")

        else:
            if (
                r_exp >= VALIDATED_MIN_EXP_R
                and r_pf is not None
                and r_pf >= VALIDATED_MIN_PF
                and r_lcb >= VALIDATED_MIN_LCB
            ):
                authority_status = "VALIDATED"
                edge_status = "VALIDATED"
                allow_open = True
                allow_probe = True
                score_adj = 0.0
                size_mult = 1.0
                reasons.append("VALIDATED_R_EDGE")

            elif r_exp >= PROMISING_MIN_EXP_R and r_pf is not None and r_pf >= PROMISING_MIN_PF:
                authority_status = "PROMISING"
                edge_status = "PROMISING"
                allow_open = True
                allow_probe = True
                score_adj = -7.0
                size_mult = 0.30
                reasons.append("PROMISING_R_EDGE_NOT_FULLY_VALIDATED")

            else:
                authority_status = "QUARANTINED"
                edge_status = "INSUFFICIENT"
                score_adj = -100.0
                size_mult = 0.0
                reasons.append("QUARANTINE_FAILED_VALIDATED_R_SAMPLE")

        if allow_open and r_n < MIN_PROMISING_R_N:
            allow_open = False
            allow_probe = True
            authority_status = "PROBE_ONLY"
            edge_status = "INSUFFICIENT"
            score_adj = min(score_adj, -15.0)
            size_mult = min(size_mult, 0.12)
            reasons.append("SAFETY_DOWNGRADE_OPEN_TO_PROBE_LOW_R_SAMPLE")

        if setup == "CAPITULATION_REBOUND_LONG" and usd_n >= 2 and usd_exp <= 0:
            allow_open = False
            allow_probe = False
            authority_status = "QUARANTINED"
            edge_status = "INSUFFICIENT"
            score_adj = -100.0
            size_mult = 0.0
            reasons.append("SAFETY_FORCE_BLOCK_NEGATIVE_CAPITULATION_SETUP")

        if mem_status in ("VALIDATED", "PROMISING") and edge_status == "INSUFFICIENT":
            reasons.append("EDGE_MEMORY_DEGRADED_BY_R_NORMALIZED_EVIDENCE")

        # CANARY_PROMOTION_GATE_V1
        size_usd_cap = 0.0
        canary_gate = {}
        try:
            canary_gate = self.canary_gate.evaluate(symbol, side, setup)
        except Exception as exc:
            canary_gate = {
                "allow_canary_probe": False,
                "allow_direct_open": False,
                "gate_status": "GATE_EXCEPTION_FAIL_CLOSED",
                "reasons": ["CANARY_GATE_EXCEPTION_FAIL_CLOSED"],
                "error": str(exc)[:240],
            }

        health["canary_promotion_gate_v1"] = canary_gate

        if (
            bool(canary_gate.get("allow_canary_probe"))
            and authority_status not in ("BLOCKED", "QUARANTINED")
            and not allow_open
        ):
            allow_open = False
            allow_probe = True
            authority_status = "CANARY_MICRO_PROBE_ONLY"
            edge_status = "INSUFFICIENT"
            score_adj = max(score_adj, -5.0)
            cap_mult = fnum(canary_gate.get("size_multiplier_cap"), 0.025)
            size_mult = min(size_mult if size_mult > 0 else cap_mult, cap_mult)
            size_usd_cap = fnum(canary_gate.get("absolute_size_usd_cap"), 250.0)
            reasons.append("CANARY_PROMOTION_GATE_V1")
            reasons.append("CANARY_MICRO_PROBE_ONLY")
            reasons.append("DIRECT_OPEN_FORBIDDEN_CANARY")
            reasons.append("AUTHORITY_SIZE_USD_CAP_APPLIED")

        elif canary_gate.get("gate_status"):
            reasons.append("CANARY_GATE_" + str(canary_gate.get("gate_status")))

        compat_exp_r = r_exp if r_n > 0 else fw_exp if fw_n > 0 else mem_exp
        compat_pf = r_pf if r_n > 0 else fw_pf
        compat_lcb = r_lcb if r_n > 0 else mem_lcb

        return AuthorityVerdict(
            version=VERSION,
            symbol=symbol,
            side=side,
            setup=setup,
            status=edge_status,
            authority_status=authority_status,
            edge_status=edge_status,
            allow_open=allow_open,
            allow_probe=allow_probe,
            score_adjustment=round(score_adj, 4),
            size_multiplier=round(size_mult, 4),
            size_usd_cap=round(size_usd_cap, 4),
            canary_gate=canary_gate,
            n=round(float(r_n), 4),
            effective_n=round(effective_n, 4),
            expectancy_r=round(compat_exp_r, 4),
            profit_factor=round(compat_pf, 4) if isinstance(compat_pf, float) else compat_pf,
            lcb=round(compat_lcb, 4),
            position_scope=str(pos.get("scope", "")),
            position_metric=position_metric,
            position_r_n=r_n,
            position_expectancy_r=round(r_exp, 4),
            position_profit_factor_r=round(r_pf, 4) if isinstance(r_pf, float) else r_pf,
            position_winrate_lcb_r=round(r_lcb, 4),
            position_max_drawdown_r=round(r_dd, 4),
            position_usd_n=usd_n,
            position_pnl_usd=round(usd_sum, 4),
            position_expectancy_usd=round(usd_exp, 4),
            position_profit_factor_usd=round(usd_pf, 4) if isinstance(usd_pf, float) else usd_pf,
            forward_n=fw_n,
            forward_expectancy_r=round(fw_exp, 4),
            forward_profit_factor=round(fw_pf, 4) if isinstance(fw_pf, float) else fw_pf,
            forward_shadow_n=shadow_n,
            forward_shadow_expectancy_r=round(shadow_exp, 4),
            forward_shadow_profit_factor=round(shadow_pf, 4) if isinstance(shadow_pf, float) else shadow_pf,
            forward_shadow_lcb=round(shadow_lcb, 4),
            edge_memory_n=round(mem_n, 4),
            edge_memory_status=mem_status,
            edge_memory_expectancy_r=round(mem_exp, 4),
            edge_memory_lcb=round(mem_lcb, 4),
            source_health=health,
            reasons=reasons,
            evidence=evidence,
        ).to_dict()


def main() -> None:
    from types import SimpleNamespace

    auth = StatisticalEdgeAuthorityV1()

    tests = [
        ("BTCUSDT", "LONG", "CAPITULATION_REBOUND_LONG"),
        ("ETHUSDT", "LONG", "CAPITULATION_REBOUND_LONG"),
        ("BTCUSDT", "SHORT", "TREND_BOUNCE_SHORT"),
        ("ETHUSDT", "SHORT", "TREND_BOUNCE_SHORT"),
    ]

    for symbol, side, setup in tests:
        c = SimpleNamespace(symbol=symbol, side=side, setup=setup)
        v = auth.evaluate(c, {}, {})
        print(
            symbol,
            side,
            setup,
            "status=", v["authority_status"],
            "edge=", v["edge_status"],
            "open=", v["allow_open"],
            "probe=", v["allow_probe"],
            "r_n=", v["position_r_n"],
            "r_exp=", v["position_expectancy_r"],
            "r_pf=", v["position_profit_factor_r"],
            "usd_n=", v["position_usd_n"],
            "usd_exp=", v["position_expectancy_usd"],
            "fw_n=", v["forward_n"],
            "shadow_n=", v["forward_shadow_n"],
            "shadow_exp=", v["forward_shadow_expectancy_r"],
            "eff_n=", v["effective_n"],
            "size_mult=", v["size_multiplier"],
            "reasons=", ",".join(v["reasons"][:4]),
        )


if __name__ == "__main__":
    main()
