
# V25.2B HARD RUNTIME GUARD
# Loaded explicitly from joanbot.runner.

from __future__ import annotations

import sys, os, json, datetime, functools, inspect, importlib.abc, importlib.machinery
from pathlib import Path

INSTALLED = False

TARGET_PREFIXES = (
    "joanbot.execution",
    "joanbot.runtime",
    "joanbot.institutional",
)

SKIP_WORDS = (
    "close", "closed", "exit", "settle", "outcome", "feedback",
    "update", "mark", "sync", "status", "export", "label", "result",
    "manage", "manager", "maintenance", "health",
)

ACTION_WORDS = (
    "open", "execute", "submit", "place", "entry",
    "paper", "broker", "bridge", "canary",
)

PAYLOAD_KEYS = (
    "side", "direction", "symbol", "score", "final_score", "entry_score",
    "size_usd", "notional_usd", "amount_usd", "risk_usd",
    "entry_price", "stop_loss", "take_profit", "take_profit_1", "take_profit_2",
)

SIZE_KEYS = (
    "size_usd", "notional_usd", "amount_usd", "risk_usd",
    "position_size_usd", "exposure_usd",
)

def _root():
    return Path.cwd()

def _audit(event):
    try:
        p = _root() / "data" / "runtime_guard_v25_2b.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        event["ts"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        event["guard"] = "V25_2B_RUNNER_GUARD"
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, sort_keys=True, default=str) + "\n")
    except Exception:
        pass

def _interesting_module(name):
    return any(name == p or name.startswith(p + ".") for p in TARGET_PREFIXES)

def _is_opening_function(q):
    low = q.lower()
    if any(w in low for w in SKIP_WORDS):
        return False
    return any(w in low for w in ACTION_WORDS)

def _get_payload_from_obj(obj):
    if isinstance(obj, dict):
        if any(k in obj for k in PAYLOAD_KEYS):
            return obj, obj
        return None, None

    try:
        d = vars(obj)
        if any(k in d for k in PAYLOAD_KEYS):
            return d, obj
    except Exception:
        pass

    return None, None

def _extract_payload(sig, args, kwargs):
    if isinstance(kwargs, dict) and any(k in kwargs for k in PAYLOAD_KEYS):
        return kwargs, None

    for a in args:
        payload, ref = _get_payload_from_obj(a)
        if payload is not None:
            return payload, ref

    try:
        bound = sig.bind_partial(*args, **kwargs)
        d = dict(bound.arguments)

        flat = {}
        for k, v in d.items():
            if k in PAYLOAD_KEYS:
                flat[k] = v
            payload, ref = _get_payload_from_obj(v)
            if payload is not None:
                return payload, ref

        if any(k in flat for k in PAYLOAD_KEYS):
            return flat, None
    except Exception:
        pass

    return None, None

def _looks_like_new_trade(payload):
    if not isinstance(payload, dict):
        return False

    has_direction = any(k in payload for k in ("side", "direction", "trade_side", "position_side"))
    has_size = any(k in payload for k in SIZE_KEYS)
    has_entry = any(k in payload for k in ("entry_price", "stop_loss", "take_profit", "take_profit_1", "take_profit_2"))

    status = str(payload.get("status", "")).upper()
    if status and status not in ("OPEN", "PENDING", "NEW", "SUBMIT", "SUBMITTED"):
        return False

    return has_direction and (has_size or has_entry)

def _write_back(payload, ref, kwargs):
    if isinstance(ref, dict):
        ref.update(payload)
        return

    if ref is not None:
        for k in SIZE_KEYS:
            if k in payload:
                try:
                    setattr(ref, k, payload[k])
                except Exception:
                    pass
        return

    for k in SIZE_KEYS:
        if k in kwargs and k in payload:
            kwargs[k] = payload[k]

def _wrap(fn, q):
    if getattr(fn, "_v25_2b_guarded", False):
        return fn

    try:
        sig = inspect.signature(fn)
    except Exception:
        return fn

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            payload, ref = _extract_payload(sig, args, kwargs)

            if payload is None or not _looks_like_new_trade(payload):
                return fn(*args, **kwargs)

            from core.runtime_control_v25 import enforce_runtime_control, RuntimeControlReject

            try:
                controlled = enforce_runtime_control(payload, source=q)
                if isinstance(controlled, dict):
                    payload.update(controlled)
                _write_back(payload, ref, kwargs)
                _audit({"event": "ALLOW_CALL", "source": q, "payload": {k: payload.get(k) for k in PAYLOAD_KEYS if k in payload}})
            except RuntimeControlReject as e:
                _audit({"event": "REJECT_CALL", "source": q, "reason": str(e), "payload": {k: payload.get(k) for k in PAYLOAD_KEYS if k in payload}})
                return {
                    "ok": False,
                    "status": "REJECTED",
                    "reason": str(e),
                    "source": "V25_2B_RUNNER_GUARD",
                    "guarded_function": q,
                }

        except Exception as e:
            _audit({"event": "GUARD_ERROR_FAIL_OPEN", "source": q, "error": repr(e)})
            return fn(*args, **kwargs)

        return fn(*args, **kwargs)

    wrapper._v25_2b_guarded = True
    return wrapper

def _patch_module(module):
    name = getattr(module, "__name__", "")
    if not _interesting_module(name):
        return

    patched = []

    for attr, obj in list(vars(module).items()):
        if inspect.isfunction(obj):
            q = f"{name}.{attr}"
            if _is_opening_function(q):
                setattr(module, attr, _wrap(obj, q))
                patched.append(q)

        elif inspect.isclass(obj) and getattr(obj, "__module__", "") == name:
            for mname, raw in list(vars(obj).items()):
                if mname.startswith("__"):
                    continue

                descriptor = None
                fn = raw

                if isinstance(raw, staticmethod):
                    descriptor = staticmethod
                    fn = raw.__func__
                elif isinstance(raw, classmethod):
                    descriptor = classmethod
                    fn = raw.__func__

                if not inspect.isfunction(fn):
                    continue

                q = f"{name}.{obj.__name__}.{mname}"
                if not _is_opening_function(q):
                    continue

                wrapped = _wrap(fn, q)

                if descriptor is staticmethod:
                    wrapped = staticmethod(wrapped)
                elif descriptor is classmethod:
                    wrapped = classmethod(wrapped)

                setattr(obj, mname, wrapped)
                patched.append(q)

    if patched:
        _audit({"event": "MODULE_PATCHED", "module": name, "patched_count": len(patched), "patched": patched[:40]})

class _GuardLoader(importlib.abc.Loader):
    def __init__(self, original_loader, fullname):
        self.original_loader = original_loader
        self.fullname = fullname

    def create_module(self, spec):
        if hasattr(self.original_loader, "create_module"):
            return self.original_loader.create_module(spec)
        return None

    def exec_module(self, module):
        self.original_loader.exec_module(module)
        _patch_module(module)

class _GuardFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if not _interesting_module(fullname):
            return None

        spec = importlib.machinery.PathFinder.find_spec(fullname, path)
        if spec and spec.loader and not isinstance(spec.loader, _GuardLoader):
            spec.loader = _GuardLoader(spec.loader, fullname)
            return spec

        return None

def install():
    global INSTALLED

    if INSTALLED:
        return True

    sys._V25_2B_RUNTIME_GUARD_ACTIVE = True

    if not any(type(f).__name__ == "_GuardFinder" for f in sys.meta_path):
        sys.meta_path.insert(0, _GuardFinder())

    # patch modules already imported, if any
    for m in list(sys.modules.values()):
        try:
            if m is not None:
                _patch_module(m)
        except Exception:
            pass

    INSTALLED = True
    _audit({"event": "GUARD_INSTALLED_FROM_RUNNER"})
    return True
