from __future__ import annotations

import json
from typing import Any, Dict

from joanbot.storage.db import get_db
from joanbot.utils import fnum

VERSION = "ALPHA_FEATURE_STORE_V1_INSTITUTIONAL"


def safe_json(x: Any) -> Dict[str, Any]:
    if isinstance(x, dict):
        return x
    if not x:
        return {}
    try:
        y = json.loads(str(x))
        return y if isinstance(y, dict) else {}
    except Exception:
        return {}


class AlphaFeatureStoreV1:
    """
    Current context feature store for alpha governance.
    Read-only. No trading mutation.
    """

    def __init__(self, db=None):
        self.db = db or get_db()
        self._context_engine = None

    def context_engine(self):
        if self._context_engine is not None:
            return self._context_engine
        try:
            from joanbot.features.context import ContextEngine
            self._context_engine = ContextEngine()
        except Exception:
            self._context_engine = False
        return self._context_engine

    def bucket_from_ctx(self, ctx: Dict[str, Any]) -> str:
        tech = ctx.get("technical", {}) or {}
        tf = tech.get("timeframes", {}) or {}
        tf15 = tf.get("15m", {}) or {}
        tf1h = tf.get("1h", {}) or {}

        regime = str(ctx.get("regime") or "UNKNOWN").upper()
        session = str(ctx.get("session") or "UNKNOWN").upper()
        vol = str(ctx.get("volatility_bucket") or "UNKNOWN").upper()

        rsi15 = fnum(tf15.get("rsi"), 50.0)
        score1h = fnum(tf1h.get("score"), 0.0)

        rsi_bucket = "RSI_LOW" if rsi15 < 38 else "RSI_HIGH" if rsi15 > 65 else "RSI_MID"
        trend_bucket = "S1H_BEAR" if score1h < -8 else "S1H_BULL" if score1h > 8 else "S1H_FLAT"

        return f"{regime}|{session}|{vol}|{rsi_bucket}|{trend_bucket}"

    def current_buckets(self) -> Dict[str, str]:
        rows = self.db.query("""
            SELECT id, ts, symbol, price, payload
            FROM market_snapshots
            WHERE symbol IN ('BTCUSDT','ETHUSDT')
            ORDER BY id DESC
            LIMIT 40;
        """)

        ce = self.context_engine()
        out: Dict[str, str] = {}

        for r in rows:
            symbol = str(r.get("symbol") or "").upper()
            if not symbol or symbol in out:
                continue

            raw = safe_json(r.get("payload"))
            raw["symbol"] = symbol
            raw["price"] = fnum(r.get("price"), 0.0)

            ctx = raw
            if ce:
                try:
                    ctx = ce.build(
                        raw,
                        {
                            "macro": raw.get("macro", {"risk_score": 50}),
                            "news": raw.get("news", {"severity": 0}),
                            "calendar": raw.get("calendar", {}),
                        },
                    )
                except Exception:
                    ctx = raw

            out[symbol] = self.bucket_from_ctx(ctx)

        return out

    def context_fit(self, historical_bucket: str, current_bucket: str) -> float:
        if not historical_bucket or not current_bucket or current_bucket == "UNKNOWN":
            return 0.35

        if historical_bucket == current_bucket:
            return 1.0

        h = historical_bucket.split("|")
        c = current_bucket.split("|")
        weights = [0.35, 0.15, 0.15, 0.15, 0.20]

        score = 0.0
        for i, w in enumerate(weights):
            if len(h) > i and len(c) > i and h[i] == c[i]:
                score += w

        return max(0.0, min(1.0, score))
