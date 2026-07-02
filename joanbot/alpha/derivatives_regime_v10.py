from __future__ import annotations

import argparse
import json
from typing import Any, Dict, List, Tuple

from joanbot.storage.db import get_db
from joanbot.utils import utc_now_iso

VERSION = "DERIVATIVES_REGIME_V10_2_INSTITUTIONAL_CONTEXTUAL"


def fnum(x: Any, default: float = 0.0) -> float:
    try:
        if x is None:
            return default
        return float(x)
    except Exception:
        return default


def inum(x: Any, default: int = 0) -> int:
    try:
        if x is None:
            return default
        return int(float(x))
    except Exception:
        return default


def js(x: Any) -> str:
    return json.dumps(x, separators=(",", ":"), ensure_ascii=False, sort_keys=True, default=str)


class DerivativesRegimeV10:
    """
    V10.2 institutional derivatives regime.

    Purpose:
    - Convert free Binance derivatives/orderflow data into a side-aware regime confirmation/veto.
    - Never create trades.
    - Never override statistical edge.
    - Only confirm, reduce, wait, or veto V11 paper micro-canaries.

    Main improvement vs V10:
    - separates directional flow, crowding, carry/basis, liquidation and book pressure.
    - uses realistic funding units: Binance funding is decimal; 0.0001 = 1 bp.
    - computes contradiction_index, confidence_score and regime_quality_score.
    - publishes through latest_derivatives_regime_v10, so V11 downstream remains compatible.
    """

    def __init__(self, db=None) -> None:
        self.db = db or get_db()

    def ensure_schema(self) -> None:
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS derivatives_regime_v10_2 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                version TEXT NOT NULL,
                symbol TEXT NOT NULL,
                selected_side TEXT NOT NULL,
                source_edge_id INTEGER NOT NULL,

                data_state TEXT NOT NULL,
                data_quality REAL NOT NULL,

                flow_long_score REAL NOT NULL,
                flow_short_score REAL NOT NULL,
                crowding_long_score REAL NOT NULL,
                crowding_short_score REAL NOT NULL,
                carry_long_score REAL NOT NULL,
                carry_short_score REAL NOT NULL,
                liquidation_long_score REAL NOT NULL,
                liquidation_short_score REAL NOT NULL,
                book_long_score REAL NOT NULL,
                book_short_score REAL NOT NULL,

                long_score REAL NOT NULL,
                short_score REAL NOT NULL,
                selected_score REAL NOT NULL,
                opposite_score REAL NOT NULL,
                directional_delta REAL NOT NULL,
                contradiction_index REAL NOT NULL,
                confidence_score REAL NOT NULL,
                regime_quality_score REAL NOT NULL,

                derivatives_state TEXT NOT NULL,
                allow_v10_canary INTEGER NOT NULL,
                reduce_size INTEGER NOT NULL,
                veto_canary INTEGER NOT NULL,

                hard_vetoes TEXT NOT NULL,
                reasons TEXT NOT NULL,
                payload TEXT NOT NULL
            );
        """)

        self.db.execute("DROP VIEW IF EXISTS latest_derivatives_regime_v10;")
        self.db.execute("""
            CREATE VIEW latest_derivatives_regime_v10 AS
            SELECT *
            FROM derivatives_regime_v10_2
            ORDER BY id DESC
            LIMIT 1;
        """)

    def qmany(self, sql: str, params=()) -> List[Dict[str, Any]]:
        try:
            return [dict(r) for r in self.db.query(sql, params)]
        except Exception:
            return []

    def q1(self, sql: str, params=()) -> Dict[str, Any]:
        rows = self.qmany(sql, params)
        return rows[0] if rows else {}

    def selected_edge(self) -> Dict[str, Any]:
        return self.q1("""
            SELECT *
            FROM latest_edge_robustness_validator_v9
            ORDER BY canary_permission DESC, robustness_score DESC, lcb_r DESC, avg_r DESC, n DESC
            LIMIT 1;
        """)

    def data_for(self, symbol: str) -> Dict[str, Any]:
        return self.q1("SELECT * FROM latest_derivatives_data_spine_v10 WHERE symbol=? LIMIT 1;", (symbol,))

    @staticmethod
    def cap(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
        return max(lo, min(hi, x))

    @staticmethod
    def add(score: Dict[str, float], side: str, key: str, value: float, reasons: Dict[str, List[str]], reason: str) -> None:
        value = max(0.0, float(value))
        if value <= 0:
            return
        score[f"{key}_{side.lower()}"] = score.get(f"{key}_{side.lower()}", 0.0) + value
        reasons[side].append(reason)

    def score(self, d: Dict[str, Any]) -> Dict[str, Any]:
        funding = fnum(d.get("funding_rate"))
        funding_bps = funding * 10000.0
        oi30 = fnum(d.get("oi_change_30m"))
        oi5 = fnum(d.get("oi_change_5m"))
        ls = fnum(d.get("long_short_ratio"), 1.0)
        top_ls = fnum(d.get("top_long_short_ratio"), 1.0)
        taker = fnum(d.get("taker_buy_sell_ratio"), 1.0)
        basis = fnum(d.get("basis_bps"))
        cvd = fnum(d.get("cvd_ratio"))
        imbalance = fnum(d.get("imbalance_25bps"))
        wall = fnum(d.get("wall_pressure"))
        liq = fnum(d.get("liq_imbalance"))
        spread = fnum(d.get("spread_bps"), 999.0)

        score: Dict[str, float] = {
            "flow_long": 0.0, "flow_short": 0.0,
            "crowding_long": 0.0, "crowding_short": 0.0,
            "carry_long": 0.0, "carry_short": 0.0,
            "liquidation_long": 0.0, "liquidation_short": 0.0,
            "book_long": 0.0, "book_short": 0.0,
        }
        reasons: Dict[str, List[str]] = {"LONG": [], "SHORT": [], "NEUTRAL": []}

        oi_expanding = oi30 > 0.06 or oi5 > 0.035
        oi_contracting = oi30 < -0.08 or oi5 < -0.05

        # 1) Directional flow. This receives the highest weight.
        if taker >= 1.02:
            self.add(score, "LONG", "flow", min(18.0, (taker - 1.0) * 220.0), reasons, "TAKER_BUY_PRESSURE")
        elif 0 < taker <= 0.98:
            self.add(score, "SHORT", "flow", min(18.0, (1.0 - taker) * 220.0), reasons, "TAKER_SELL_PRESSURE")

        if cvd >= 0.025:
            self.add(score, "LONG", "flow", min(14.0, cvd * 125.0), reasons, "POSITIVE_CVD_PROXY")
        elif cvd <= -0.025:
            self.add(score, "SHORT", "flow", min(14.0, abs(cvd) * 125.0), reasons, "NEGATIVE_CVD_PROXY")

        if oi_expanding and taker >= 1.015:
            self.add(score, "LONG", "flow", 12.0, reasons, "OI_EXPANDS_WITH_BUY_FLOW")
        if oi_expanding and 0 < taker <= 0.985:
            self.add(score, "SHORT", "flow", 12.0, reasons, "OI_EXPANDS_WITH_SELL_FLOW")
        if oi_contracting:
            reasons["NEUTRAL"].append("OI_CONTRACTING_REDUCES_DIRECTIONAL_CONFIDENCE")

        # 2) Crowding and squeeze pressure. Lower weight than actual flow.
        if funding_bps >= 2.0:
            self.add(score, "SHORT", "crowding", min(14.0, 5.0 + (funding_bps - 2.0) * 1.6), reasons, "POSITIVE_FUNDING_LONG_CROWDING")
        elif funding_bps <= -2.0:
            self.add(score, "LONG", "crowding", min(14.0, 5.0 + (abs(funding_bps) - 2.0) * 1.6), reasons, "NEGATIVE_FUNDING_SHORT_CROWDING")

        if ls >= 1.08:
            self.add(score, "SHORT", "crowding", min(12.0, (ls - 1.0) * 70.0), reasons, "GLOBAL_LONG_SHORT_LONG_HEAVY")
        elif 0 < ls <= 0.92:
            self.add(score, "LONG", "crowding", min(12.0, (1.0 - ls) * 70.0), reasons, "GLOBAL_LONG_SHORT_SHORT_HEAVY")

        if top_ls >= 1.08:
            self.add(score, "SHORT", "crowding", min(10.0, (top_ls - 1.0) * 60.0), reasons, "TOP_TRADER_LONG_HEAVY")
        elif 0 < top_ls <= 0.92:
            self.add(score, "LONG", "crowding", min(10.0, (1.0 - top_ls) * 60.0), reasons, "TOP_TRADER_SHORT_HEAVY")

        # 3) Carry/basis. Useful as a supporting veto/confirmation, never decisive alone.
        if basis >= 8.0:
            self.add(score, "SHORT", "carry", min(8.0, 3.0 + basis / 8.0), reasons, "POSITIVE_PREMIUM_BASIS")
        elif basis <= -8.0:
            self.add(score, "LONG", "carry", min(8.0, 3.0 + abs(basis) / 8.0), reasons, "NEGATIVE_DISCOUNT_BASIS")

        # 4) Liquidation imbalance proxy.
        # Positive liq_imbalance means short liquidations dominate -> upside pressure.
        # Negative means long liquidations dominate -> downside pressure.
        if liq >= 0.12:
            self.add(score, "LONG", "liquidation", min(8.0, liq * 18.0), reasons, "SHORT_LIQUIDATION_PRESSURE")
        elif liq <= -0.12:
            self.add(score, "SHORT", "liquidation", min(8.0, abs(liq) * 18.0), reasons, "LONG_LIQUIDATION_PRESSURE")

        # 5) Book/orderflow microstructure. Small weight because it is noisy on Termux snapshots.
        book_signal = 0.5 * imbalance + 0.5 * wall
        if book_signal >= 0.08:
            self.add(score, "LONG", "book", min(7.0, book_signal * 30.0), reasons, "BID_BOOK_PRESSURE")
        elif book_signal <= -0.08:
            self.add(score, "SHORT", "book", min(7.0, abs(book_signal) * 30.0), reasons, "ASK_BOOK_PRESSURE")

        if spread >= 8.0:
            reasons["NEUTRAL"].append("WIDE_SPREAD_REDUCES_REGIME_CONFIDENCE")

        long_score = self.cap(
            score["flow_long"] + score["crowding_long"] + score["carry_long"] + score["liquidation_long"] + score["book_long"]
        )
        short_score = self.cap(
            score["flow_short"] + score["crowding_short"] + score["carry_short"] + score["liquidation_short"] + score["book_short"]
        )

        flow_delta = abs(score["flow_long"] - score["flow_short"])
        total_abs = long_score + short_score
        contradiction_index = 0.0 if total_abs <= 0 else self.cap(100.0 * min(long_score, short_score) / max(long_score, short_score, 1.0))
        if flow_delta < 3.0 and total_abs > 30.0:
            contradiction_index = min(100.0, contradiction_index + 12.0)
        if oi_contracting and total_abs > 25.0:
            contradiction_index = min(100.0, contradiction_index + 8.0)
        if spread >= 8.0:
            contradiction_index = min(100.0, contradiction_index + 10.0)

        return {
            **score,
            "long_score": long_score,
            "short_score": short_score,
            "contradiction_index": contradiction_index,
            "long_reasons": reasons["LONG"],
            "short_reasons": reasons["SHORT"],
            "neutral_reasons": reasons["NEUTRAL"],
            "funding_bps": funding_bps,
            "oi_expanding": oi_expanding,
            "oi_contracting": oi_contracting,
        }

    def classify(self, selected_score: float, opposite_score: float, data_quality: float, contradiction_index: float, side: str) -> Tuple[str, int, int, int, List[str], List[str]]:
        hard_vetoes: List[str] = []
        reasons: List[str] = []
        delta = selected_score - opposite_score

        if data_quality < 45:
            hard_vetoes.append("DERIVATIVES_DATA_NOT_READY")
            return "DERIVATIVES_DATA_NOT_READY", 0, 1, 1, hard_vetoes, reasons

        if contradiction_index >= 72:
            hard_vetoes.append("DERIVATIVES_INTERNAL_CONTRADICTION_HIGH")
            return f"DERIVATIVES_CONFLICT_{side}", 0, 1, 1, hard_vetoes, reasons

        if opposite_score >= 54 and delta <= -16:
            hard_vetoes.append("DERIVATIVES_OPPOSITE_DIRECTION_STRONG")
            return f"DERIVATIVES_CONFLICT_{side}", 0, 1, 1, hard_vetoes, reasons

        if opposite_score >= 42 and delta <= -8:
            hard_vetoes.append("DERIVATIVES_OPPOSITE_DIRECTION_SOFT")
            return f"DERIVATIVES_SOFT_CONFLICT_{side}", 0, 1, 0, hard_vetoes, reasons

        if selected_score >= 54 and delta >= 14 and contradiction_index <= 38 and data_quality >= 70:
            reasons.append("DERIVATIVES_STRONG_MULTI_FACTOR_CONFIRMATION")
            return f"DERIVATIVES_CONFIRM_STRONG_{side}", 1, 0, 0, hard_vetoes, reasons

        if selected_score >= 40 and delta >= 6 and contradiction_index <= 55:
            reasons.append("DERIVATIVES_CONFIRM_EDGE_DIRECTION")
            return f"DERIVATIVES_CONFIRM_{side}", 1, 0, 0, hard_vetoes, reasons

        if selected_score >= 28 and delta >= -6 and contradiction_index <= 60 and data_quality >= 55:
            reasons.append("DERIVATIVES_NEUTRAL_BUT_NOT_CONFLICTING")
            return f"DERIVATIVES_NEUTRAL_SUPPORTIVE_{side}", 1, 1, 0, hard_vetoes, reasons

        reasons.append("DERIVATIVES_NEUTRAL_WAIT_FOR_CONFLUENCE")
        return "DERIVATIVES_NEUTRAL", 0, 1, 0, hard_vetoes, reasons

    def refresh(self) -> Dict[str, Any]:
        self.ensure_schema()

        edge = self.selected_edge()
        symbol = str(edge.get("symbol") or "UNKNOWN")
        side = str(edge.get("side") or "UNKNOWN").upper()
        d = self.data_for(symbol)

        if not edge:
            data_state = "NO_DATA"
            data_quality = 0.0
            sc = {k: 0.0 for k in [
                "flow_long", "flow_short", "crowding_long", "crowding_short", "carry_long", "carry_short",
                "liquidation_long", "liquidation_short", "book_long", "book_short", "long_score", "short_score",
                "contradiction_index"
            ]}
            selected_score = opposite_score = delta = confidence = regime_quality = 0.0
            state = "NO_EDGE_FOR_DERIVATIVES_REGIME"
            allow = 0; reduce = 1; veto = 1
            hard_vetoes = ["NO_EDGE"]
            reasons: List[str] = []
        elif not d:
            data_state = "NO_DATA"
            data_quality = 0.0
            sc = {k: 0.0 for k in [
                "flow_long", "flow_short", "crowding_long", "crowding_short", "carry_long", "carry_short",
                "liquidation_long", "liquidation_short", "book_long", "book_short", "long_score", "short_score",
                "contradiction_index"
            ]}
            selected_score = opposite_score = delta = confidence = regime_quality = 0.0
            state = "DERIVATIVES_DATA_NOT_READY"
            allow = 0; reduce = 1; veto = 1
            hard_vetoes = ["NO_DERIVATIVES_DATA_SPINE"]
            reasons = []
        else:
            data_state = str(d.get("data_state") or "DERIVATIVES_DATA_NOT_READY")
            data_quality = fnum(d.get("data_quality"))
            if data_state == "DERIVATIVES_DATA_NOT_READY":
                data_quality = min(data_quality, 44.0)
            sc = self.score(d)
            long_score = fnum(sc.get("long_score"))
            short_score = fnum(sc.get("short_score"))
            selected_score = long_score if side == "LONG" else short_score if side == "SHORT" else 0.0
            opposite_score = short_score if side == "LONG" else long_score if side == "SHORT" else 0.0
            delta = selected_score - opposite_score
            state, allow, reduce, veto, hard_vetoes, reasons = self.classify(
                selected_score, opposite_score, data_quality, fnum(sc.get("contradiction_index")), side
            )
            side_reasons = sc.get("long_reasons") if side == "LONG" else sc.get("short_reasons") if side == "SHORT" else []
            reasons.extend(side_reasons or [])
            reasons.extend(sc.get("neutral_reasons") or [])
            confidence = self.cap(0.45 * selected_score + 0.30 * max(0.0, delta) + 0.25 * data_quality - 0.35 * fnum(sc.get("contradiction_index")))
            regime_quality = self.cap(0.55 * data_quality + 0.30 * confidence + 0.15 * max(0.0, 100.0 - fnum(sc.get("contradiction_index"))))

        payload = {
            "edge": edge,
            "derivatives_data": d,
            "score_components": sc,
            "paper_only": True,
            "paid_api_required": False,
            "v10_2_changes": [
                "realistic_funding_bps",
                "flow_crowding_carry_liquidation_book_separation",
                "contradiction_index",
                "confidence_score",
                "single_latest_view_compatible_with_v11",
            ],
        }

        self.db.execute("""
            INSERT INTO derivatives_regime_v10_2 (
                ts, version, symbol, selected_side, source_edge_id,
                data_state, data_quality,
                flow_long_score, flow_short_score, crowding_long_score, crowding_short_score,
                carry_long_score, carry_short_score, liquidation_long_score, liquidation_short_score,
                book_long_score, book_short_score,
                long_score, short_score, selected_score, opposite_score, directional_delta,
                contradiction_index, confidence_score, regime_quality_score,
                derivatives_state, allow_v10_canary, reduce_size, veto_canary,
                hard_vetoes, reasons, payload
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """, (
            utc_now_iso(), VERSION, symbol, side, inum(edge.get("source_edge_id")),
            data_state, data_quality,
            fnum(sc.get("flow_long")), fnum(sc.get("flow_short")), fnum(sc.get("crowding_long")), fnum(sc.get("crowding_short")),
            fnum(sc.get("carry_long")), fnum(sc.get("carry_short")), fnum(sc.get("liquidation_long")), fnum(sc.get("liquidation_short")),
            fnum(sc.get("book_long")), fnum(sc.get("book_short")),
            fnum(sc.get("long_score")), fnum(sc.get("short_score")), selected_score, opposite_score, delta,
            fnum(sc.get("contradiction_index")), confidence, regime_quality,
            state, allow, reduce, veto,
            js(hard_vetoes), js(reasons), js(payload),
        ))

        return {
            "version": VERSION,
            "symbol": symbol,
            "side": side,
            "derivatives_state": state,
            "allow_v10_canary": allow,
            "reduce_size": reduce,
            "veto_canary": veto,
            "selected_score": round(selected_score, 2),
            "opposite_score": round(opposite_score, 2),
            "directional_delta": round(delta, 2),
            "contradiction_index": round(fnum(sc.get("contradiction_index")), 2),
            "confidence_score": round(confidence, 2),
            "regime_quality_score": round(regime_quality, 2),
            "hard_vetoes": hard_vetoes,
            "reasons": reasons[:10],
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    engine = DerivativesRegimeV10()
    if args.refresh:
        print(json.dumps(engine.refresh(), indent=2, sort_keys=True, default=str))
    else:
        engine.ensure_schema()
        print(json.dumps({"version": VERSION, "schema": "ok"}, indent=2))


if __name__ == "__main__":
    main()
