
from __future__ import annotations
from pathlib import Path
import json, os, datetime

ROOT = Path(__file__).resolve().parents[1]
CONTROL_PATH = ROOT / "data" / "runtime_controls_v25.json"
AUDIT_PATH = ROOT / "data" / "runtime_control_audit_v25.jsonl"

VERSION = "V25_1B_RUNTIME_CONTROL"

DEFAULTS = {
    "version": VERSION,
    "enabled": True,
    "trading_paused": False,
    "mode": "normal",
    "base_min_score": 70.0,
    "score_floor_delta": 0.0,
    "min_score_override": None,
    "risk_multiplier": 1.0,
    "max_global_open": 2,
    "allow_longs": True,
    "allow_shorts": True,
    "updated_utc": None,
    "updated_by": "system",
}

PRESETS = {
    "conservative": {
        "mode": "conservative",
        "risk_multiplier": 0.50,
        "score_floor_delta": 8.0,
        "max_global_open": 1,
    },
    "normal": {
        "mode": "normal",
        "risk_multiplier": 1.0,
        "score_floor_delta": 0.0,
        "max_global_open": 2,
    },
    "aggressive": {
        "mode": "aggressive",
        "risk_multiplier": 1.25,
        "score_floor_delta": -3.0,
        "max_global_open": 3,
    },
}

class RuntimeControlReject(Exception):
    pass

def utc():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()

def atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)

def audit(event):
    try:
        event["ts"] = utc()
        event["version"] = VERSION
        AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
        with AUDIT_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, sort_keys=True) + "\n")
    except Exception:
        pass

def validate(c):
    c["version"] = VERSION
    c["enabled"] = bool(c.get("enabled", True))
    c["trading_paused"] = bool(c.get("trading_paused", False))
    c["mode"] = str(c.get("mode", "normal")).lower()
    if c["mode"] not in PRESETS:
        c["mode"] = "normal"
    c["base_min_score"] = max(40.0, min(95.0, float(c.get("base_min_score", 70.0))))
    c["score_floor_delta"] = max(-10.0, min(20.0, float(c.get("score_floor_delta", 0.0))))
    mso = c.get("min_score_override", None)
    if mso in ("", "none", "None", None):
        c["min_score_override"] = None
    else:
        c["min_score_override"] = max(50.0, min(95.0, float(mso)))
    c["risk_multiplier"] = max(0.10, min(1.50, float(c.get("risk_multiplier", 1.0))))
    c["max_global_open"] = max(0, min(5, int(c.get("max_global_open", 2))))
    c["allow_longs"] = bool(c.get("allow_longs", True))
    c["allow_shorts"] = bool(c.get("allow_shorts", True))
    return c

def load_controls():
    if not CONTROL_PATH.exists():
        c = dict(DEFAULTS)
        c["updated_utc"] = utc()
        atomic_write(CONTROL_PATH, c)
        return c
    try:
        raw = json.loads(CONTROL_PATH.read_text(encoding="utf-8"))
    except Exception:
        raw = {}
    c = dict(DEFAULTS)
    c.update(raw)
    return validate(c)

def save_controls(c, updated_by="cli"):
    c = validate(c)
    c["updated_utc"] = utc()
    c["updated_by"] = updated_by
    atomic_write(CONTROL_PATH, c)
    audit({"event": "CONTROL_UPDATE", "controls": c})
    return c

def set_preset(name, updated_by="cli"):
    name = str(name).lower().strip()
    if name not in PRESETS:
        raise ValueError("preset invalid: conservative | normal | aggressive")
    c = load_controls()
    c.update(PRESETS[name])
    return save_controls(c, updated_by)

def update_controls(updated_by="cli", **kwargs):
    c = load_controls()
    c.update(kwargs)
    return save_controls(c, updated_by)

def reset_controls(updated_by="cli"):
    c = dict(DEFAULTS)
    c["updated_utc"] = utc()
    return save_controls(c, updated_by)

def getv(obj, key, default=None):
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)

def setv(obj, key, value):
    if isinstance(obj, dict):
        obj[key] = value
    else:
        try:
            setattr(obj, key, value)
        except Exception:
            pass

def extract_score(obj):
    for k in ["score", "final_score", "entry_score", "quality_score", "decision_score", "edge_score", "setup_score"]:
        v = getv(obj, k, None)
        if v is not None:
            try:
                return float(v)
            except Exception:
                pass
    if isinstance(obj, dict):
        for k, v in obj.items():
            if "score" in str(k).lower():
                try:
                    return float(v)
                except Exception:
                    pass
    return None

def extract_side(obj):
    for k in ["side", "direction", "position_side", "trade_side"]:
        v = getv(obj, k, None)
        if v:
            return str(v).upper()
    return ""

def read_open_positions():
    for p in [ROOT / "live_export" / "status.json", ROOT / "data" / "status.json"]:
        try:
            if p.exists():
                d = json.loads(p.read_text(encoding="utf-8"))
                return int(d.get("open_positions", 0))
        except Exception:
            pass
    return 0

def apply_size_multiplier(obj, mult):
    for k in ["size_usd", "notional_usd", "amount_usd", "risk_usd", "position_size_usd", "exposure_usd"]:
        v = getv(obj, k, None)
        if v is not None:
            try:
                setv(obj, k, round(float(v) * mult, 8))
            except Exception:
                pass

def enforce_runtime_control(intent, base_min_score=None, open_positions=None, source="runtime"):
    c = load_controls()

    if not c.get("enabled", True):
        return intent

    if c.get("trading_paused", False):
        audit({"event": "REJECT", "reason": "RUNTIME_CONTROL_PAUSED", "source": source})
        raise RuntimeControlReject("RUNTIME_CONTROL_PAUSED")

    side = extract_side(intent)
    score = extract_score(intent)

    if side == "LONG" and not c.get("allow_longs", True):
        raise RuntimeControlReject("RUNTIME_CONTROL_LONGS_DISABLED")

    if side == "SHORT" and not c.get("allow_shorts", True):
        raise RuntimeControlReject("RUNTIME_CONTROL_SHORTS_DISABLED")

    if open_positions is None:
        open_positions = read_open_positions()

    if int(open_positions) >= int(c.get("max_global_open", 2)):
        raise RuntimeControlReject(f"RUNTIME_CONTROL_MAX_OPEN_{open_positions}_GE_{c.get('max_global_open')}")

    if c.get("min_score_override") is not None:
        floor = float(c["min_score_override"])
    else:
        base = float(base_min_score if base_min_score is not None else c.get("base_min_score", 70.0))
        floor = base + float(c.get("score_floor_delta", 0.0))

    if score is not None and float(score) < floor:
        raise RuntimeControlReject(f"RUNTIME_CONTROL_SCORE_{score:.2f}_LT_{floor:.2f}")

    apply_size_multiplier(intent, float(c.get("risk_multiplier", 1.0)))
    audit({"event": "ALLOW", "side": side, "score": score, "risk_multiplier": c.get("risk_multiplier"), "source": source})
    return intent

def summary():
    c = load_controls()
    if c.get("min_score_override") is not None:
        score = f"min_score_override={c['min_score_override']}"
    else:
        score = f"base={c['base_min_score']} delta={c['score_floor_delta']} effective={c['base_min_score'] + c['score_floor_delta']}"
    return (
        "V25.1B Runtime Control\n"
        f"- mode: {c['mode']}\n"
        f"- paused: {c['trading_paused']}\n"
        f"- risk_multiplier: {c['risk_multiplier']}\n"
        f"- score: {score}\n"
        f"- max_global_open: {c['max_global_open']}\n"
        f"- longs: {c['allow_longs']}\n"
        f"- shorts: {c['allow_shorts']}\n"
        f"- updated: {c.get('updated_utc')}\n"
    )
