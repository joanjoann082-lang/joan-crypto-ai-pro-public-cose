from __future__ import annotations

import math
from typing import Any, Dict, List, Tuple


def num(x: Any, default: float = 0.0) -> float:
    try:
        if x is None:
            return default
        y = float(x)
        if math.isnan(y) or math.isinf(y):
            return default
        return y
    except Exception:
        return default


def upper(x: Any, default: str = "UNKNOWN") -> str:
    s = str(x if x is not None else default).strip().upper()
    return s if s else default


class MotorAprenentatgeCausalNet:
    """Mapa causal compacte per aprenentatge quantitatiu.

    Converteix el context brut en atributs estables perquè el ledger aprengui causes:
    estructura, nivells, liquiditat, flux, derivats i fase d'ona aproximada.
    """

    def bucket(self, x: float, limits: Tuple[float, ...], labels: Tuple[str, ...]) -> str:
        ax = abs(num(x))
        for lim, lab in zip(limits, labels):
            if ax <= lim:
                return lab
        return labels[-1]

    def dist_bucket(self, pct: float) -> str:
        pct = num(pct)
        side = "SOBRE" if pct > 0 else "SOTA" if pct < 0 else "A"
        mag = self.bucket(pct, (0.10, 0.35, 0.80, 1.60), ("TOCANT", "PROP", "MITJA", "LLUNY", "EXTREM"))
        return f"{side}_{mag}"

    def zone_bucket(self, pos: float) -> str:
        pos = num(pos, 0.5)
        if pos >= 0.86: return "EXTREM_ALT"
        if pos >= 0.67: return "PART_ALTA"
        if pos <= 0.14: return "EXTREM_BAIX"
        if pos <= 0.33: return "PART_BAIXA"
        return "MIG_RANG"

    def rsi_bucket(self, rsi: float) -> str:
        r = num(rsi, 50)
        if r >= 78: return "SOBRECOMPRA_EXTREMA"
        if r >= 68: return "SOBRECOMPRA"
        if r <= 22: return "SOBREVENDA_EXTREMA"
        if r <= 32: return "SOBREVENDA"
        return "NEUTRAL"

    def _pivots(self, candles: List[Dict[str, Any]], left: int = 2, right: int = 2) -> List[Tuple[int, str, float]]:
        piv: List[Tuple[int, str, float]] = []
        if len(candles) < left + right + 5:
            return piv
        highs = [num(c.get("high")) for c in candles]
        lows = [num(c.get("low")) for c in candles]
        for i in range(left, len(candles) - right):
            h = highs[i]; l = lows[i]
            if h and h >= max(highs[i-left:i+right+1]):
                piv.append((i, "H", h))
            if l and l <= min(lows[i-left:i+right+1]):
                piv.append((i, "L", l))
        return piv[-12:]

    def fractal_state(self, candles: List[Dict[str, Any]]) -> str:
        piv = self._pivots(candles)
        highs = [p for p in piv if p[1] == "H"][-2:]
        lows = [p for p in piv if p[1] == "L"][-2:]
        if len(highs) < 2 or len(lows) < 2:
            return "FRACTAL_INSUFICIENT"
        hh = highs[-1][2] > highs[-2][2]
        hl = lows[-1][2] > lows[-2][2]
        lh = highs[-1][2] < highs[-2][2]
        ll = lows[-1][2] < lows[-2][2]
        if hh and hl: return "HH_HL"
        if lh and ll: return "LH_LL"
        if hh and ll: return "EXPANSIO_VOLATIL"
        if lh and hl: return "COMPRESSIO_RANG"
        return "FRACTAL_MIXT"

    def fase_ona(self, candles: List[Dict[str, Any]], price: float) -> str:
        piv = self._pivots(candles)
        if len(piv) < 5:
            return "ONA_INSUFICIENT"
        recent = piv[-5:]
        highs = [p[2] for p in recent if p[1] == "H"]
        lows = [p[2] for p in recent if p[1] == "L"]
        if len(highs) < 2 or len(lows) < 2:
            return "ONA_INSUFICIENT"
        up = highs[-1] > highs[0] and lows[-1] > lows[0]
        down = highs[-1] < highs[0] and lows[-1] < lows[0]
        last_high = max(highs); last_low = min(lows)
        pos = (num(price) - last_low) / max(1e-9, last_high - last_low)
        if up and pos > 0.78: return "IMPULS_BULL_TARD"
        if up and pos < 0.45: return "CORRECCIO_BULL"
        if up: return "IMPULS_BULL"
        if down and pos < 0.22: return "IMPULS_BEAR_TARD"
        if down and pos > 0.55: return "CORRECCIO_BEAR"
        if down: return "IMPULS_BEAR"
        return "ONA_RANG"

    def sweep_state(self, candles: List[Dict[str, Any]]) -> str:
        if len(candles) < 25:
            return "SWEEP_INSUFICIENT"
        last = candles[-1]
        prev = candles[-25:-1]
        high_prev = max(num(c.get("high")) for c in prev)
        low_prev = min(num(c.get("low")) for c in prev)
        high = num(last.get("high")); low = num(last.get("low")); close = num(last.get("close"))
        if high > high_prev and close < high_prev: return "SWEEP_HIGH_REBUIG"
        if low < low_prev and close > low_prev: return "SWEEP_LOW_REBUIG"
        if high > high_prev: return "BREAK_HIGH"
        if low < low_prev: return "BREAK_LOW"
        return "SENSE_SWEEP"

    def zona_preu(self, levels: Dict[str, Any]) -> Dict[str, str]:
        distances = levels.get("distances_pct") or {}
        cycles = levels.get("cycles") or {}
        c24 = cycles.get("24h") or {}
        c7 = cycles.get("7d") or {}
        pos24 = num(c24.get("close_pos"), 0.5)
        pos7 = num(c7.get("close_pos"), 0.5)
        d_vah = num(distances.get("vah")); d_val = num(distances.get("val")); d_poc = num(distances.get("poc")); d_vwap = num(distances.get("vwap_d"))
        zona = self.zone_bucket(pos24)
        nivell = "MIG_RANG"
        if abs(d_vah) <= 0.35 or zona in {"PART_ALTA", "EXTREM_ALT"}: nivell = "RESISTENCIA"
        if abs(d_val) <= 0.35 or zona in {"PART_BAIXA", "EXTREM_BAIX"}: nivell = "SUPORT"
        if abs(d_poc) <= 0.25: nivell = "POC"
        return {
            "zona_rang_24h": zona,
            "zona_rang_7d": self.zone_bucket(pos7),
            "nivell_operatiu": nivell,
            "dist_vwap_d": self.dist_bucket(d_vwap),
            "dist_poc": self.dist_bucket(d_poc),
            "dist_vah": self.dist_bucket(d_vah),
            "dist_val": self.dist_bucket(d_val),
        }

    def flux_orderflow(self, micro: Dict[str, Any], derivatives: Dict[str, Any]) -> Dict[str, str]:
        cvd = num(micro.get("cvd_ratio") or micro.get("cvd_proxy"))
        imb = num(micro.get("imbalance_25bps"))
        wall = num(micro.get("wall_pressure"))
        taker = num(derivatives.get("taker_buy_sell_ratio") or derivatives.get("taker_buy_ratio"), 1.0)
        funding = num(derivatives.get("funding_rate"))
        lsr = num(derivatives.get("long_short_ratio") or derivatives.get("long_short"), 1.0)
        oi5 = num(derivatives.get("oi_chg_5m")); oi1h = num(derivatives.get("oi_chg_1h"))
        def sign(v, pos, neg, flat="NEUTRAL"):
            if v > pos: return "POSITIU"
            if v < neg: return "NEGATIU"
            return flat
        return {
            "cvd": sign(cvd, 0.08, -0.08),
            "imbalance": sign(imb, 0.18, -0.18),
            "wall": sign(wall, 0.12, -0.12),
            "taker": "BUY_AGRESSIU" if taker > 1.08 else "SELL_AGRESSIU" if taker < 0.92 else "NEUTRAL",
            "funding": "FUNDING_POSITIU_EXTREM" if funding > 0.00025 else "FUNDING_NEGATIU_EXTREM" if funding < -0.00025 else "FUNDING_NORMAL",
            "long_short": "CROWD_LONG" if lsr > 1.55 else "CROWD_SHORT" if lsr < 0.70 else "CROWD_NEUTRAL",
            "oi": "OI_PUJANT" if oi5 > 0.015 or oi1h > 0.035 else "OI_CAIGUENT" if oi5 < -0.015 or oi1h < -0.035 else "OI_NEUTRAL",
        }

    def liquiditat(self, derivatives: Dict[str, Any]) -> Dict[str, str]:
        imb = num(derivatives.get("liq_imbalance"))
        long_liq = num(derivatives.get("long_liq_usd")); short_liq = num(derivatives.get("short_liq_usd"))
        if long_liq > short_liq * 1.5 and long_liq > 250000: pressio = "LONG_LIQ_DOMINA"
        elif short_liq > long_liq * 1.5 and short_liq > 250000: pressio = "SHORT_LIQ_DOMINA"
        elif imb > 0.25: pressio = "SHORT_LIQ_PRESSIO"
        elif imb < -0.25: pressio = "LONG_LIQ_PRESSIO"
        else: pressio = "LIQ_NEUTRA"
        return {"pressio_liquidacions": pressio}

    def calcula(self, ctx: Dict[str, Any], candles_by_tf: Dict[str, List[Dict[str, Any]]] | None = None) -> Dict[str, Any]:
        candles_by_tf = candles_by_tf or {}
        price = num(ctx.get("price"))
        technical = ctx.get("technical") or {}; levels = ctx.get("levels") or {}; micro = ctx.get("micro") or {}; derivatives = ctx.get("derivatives") or {}
        tf = technical.get("timeframes") or {}
        c15 = candles_by_tf.get("15m") or []; c1h = candles_by_tf.get("1h") or []; c4h = candles_by_tf.get("4h") or []
        tags: Dict[str, str] = {}
        tags["estructura_4h"] = upper((tf.get("4h") or {}).get("state"))
        tags["estructura_1h"] = upper((tf.get("1h") or {}).get("state"))
        tags["estructura_15m"] = upper((tf.get("15m") or {}).get("state"))
        tags["rsi_15m"] = self.rsi_bucket(num((tf.get("15m") or {}).get("rsi"), 50))
        tags["rsi_1h"] = self.rsi_bucket(num((tf.get("1h") or {}).get("rsi"), 50))
        tags["fractal_15m"] = self.fractal_state(c15)
        tags["fractal_1h"] = self.fractal_state(c1h)
        tags["fractal_4h"] = self.fractal_state(c4h)
        tags["ona_1h"] = self.fase_ona(c1h, price)
        tags["sweep_15m"] = self.sweep_state(c15)
        tags["sweep_1h"] = self.sweep_state(c1h)
        tags.update(self.zona_preu(levels))
        tags.update(self.flux_orderflow(micro, derivatives))
        tags.update(self.liquiditat(derivatives))
        score_long = 0.0; score_short = 0.0
        if tags["estructura_4h"] == "BULL": score_long += 8; score_short -= 5
        if tags["estructura_4h"] == "BEAR": score_short += 8; score_long -= 5
        if tags["fractal_1h"] == "HH_HL": score_long += 6
        if tags["fractal_1h"] == "LH_LL": score_short += 6
        if tags["nivell_operatiu"] == "SUPORT": score_long += 5; score_short -= 2
        if tags["nivell_operatiu"] == "RESISTENCIA": score_short += 5; score_long -= 2
        if tags["sweep_15m"] == "SWEEP_LOW_REBUIG": score_long += 6
        if tags["sweep_15m"] == "SWEEP_HIGH_REBUIG": score_short += 6
        if tags["cvd"] == "POSITIU": score_long += 4; score_short -= 2
        if tags["cvd"] == "NEGATIU": score_short += 4; score_long -= 2
        if tags["pressio_liquidacions"] in {"LONG_LIQ_DOMINA", "LONG_LIQ_PRESSIO"}: score_short += 3
        if tags["pressio_liquidacions"] in {"SHORT_LIQ_DOMINA", "SHORT_LIQ_PRESSIO"}: score_long += 3
        if tags["funding"] == "FUNDING_POSITIU_EXTREM": score_short += 2
        if tags["funding"] == "FUNDING_NEGATIU_EXTREM": score_long += 2
        return {
            "versio": "MAPA_CAUSAL_NET_1",
            "tags": tags,
            "score_long": round(score_long, 4),
            "score_short": round(score_short, 4),
            "resum": [f"{k}={v}" for k, v in tags.items() if v not in {"UNKNOWN", "NEUTRAL", "FRACTAL_INSUFICIENT", "ONA_INSUFICIENT", "SENSE_SWEEP"}][:18],
        }

    def score_per_costat(self, mapa: Dict[str, Any], side: str) -> float:
        if upper(side) == "LONG": return num(mapa.get("score_long"))
        if upper(side) == "SHORT": return num(mapa.get("score_short"))
        return 0.0

    def claus_causals(self, symbol: str, side: str, setup: str, regime: str, mapa: Dict[str, Any]) -> List[str]:
        tags = mapa.get("tags") or {}
        def t(k: str) -> str: return upper(tags.get(k))
        symbol = upper(symbol); side = upper(side); setup = upper(setup); regime = upper(regime)
        keys = [
            f"CAUSA|{symbol}|{side}|{setup}|{t('estructura_4h')}|{t('fractal_1h')}|{t('nivell_operatiu')}|{t('cvd')}|{t('pressio_liquidacions')}",
            f"CAUSA_SETUP|{symbol}|{side}|{setup}|{t('nivell_operatiu')}|{t('cvd')}|{t('pressio_liquidacions')}",
            f"CAUSA_REGIM|{side}|{setup}|{regime}|{t('fractal_1h')}|{t('ona_1h')}|{t('sweep_15m')}",
            f"CAUSA_NIVELL|{side}|{setup}|{t('nivell_operatiu')}|{t('dist_vwap_d')}|{t('dist_poc')}",
            f"CAUSA_FLUX|{side}|{setup}|{t('cvd')}|{t('taker')}|{t('oi')}|{t('funding')}",
            f"CAUSA_LIQ|{side}|{setup}|{t('pressio_liquidacions')}|{t('sweep_15m')}",
            f"ATRIBUT|{side}|NIVELL|{t('nivell_operatiu')}",
            f"ATRIBUT|{side}|FRACTAL|{t('fractal_1h')}",
            f"ATRIBUT|{side}|ONA|{t('ona_1h')}",
            f"ATRIBUT|{side}|CVD|{t('cvd')}",
            f"ATRIBUT|{side}|LIQ|{t('pressio_liquidacions')}",
        ]
        return [k for k in keys if "UNKNOWN" not in k]
