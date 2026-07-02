from __future__ import annotations
import json, math, os, tempfile, time, traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, List


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def utc_ms() -> int:
    return int(time.time() * 1000)


def fnum(x: Any, default: float = 0.0) -> float:
    try:
        if x is None:
            return default
        v = float(x)
        if math.isnan(v) or math.isinf(v):
            return default
        return v
    except Exception:
        return default


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def pct(a: float, b: float) -> float:
    return (a / b - 1.0) * 100.0 if b else 0.0


def bps(a: float, b: float) -> float:
    return (a / b - 1.0) * 10000.0 if b else 0.0


def atomic_write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name, suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2, sort_keys=True)
        os.replace(tmp, path)
    finally:
        try:
            if os.path.exists(tmp):
                os.unlink(tmp)
        except Exception:
            pass


def read_json(path: Path, default: Any) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(errors="ignore"))
    except Exception:
        pass
    return default


def append_jsonl(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False, sort_keys=True) + "\n")


def tail_lines(path: Path, n: int = 1000) -> List[str]:
    try:
        if not path.exists():
            return []
        with path.open("rb") as f:
            f.seek(0, os.SEEK_END)
            end = f.tell(); data = b""; pos = end
            while pos > 0 and data.count(b"\n") <= n:
                step = min(8192, pos); pos -= step; f.seek(pos); data = f.read(step) + data
        return data.decode("utf-8", errors="ignore").splitlines()[-n:]
    except Exception:
        return []


def safe_call(label: str, fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except Exception as e:
        return {"ok": False, "label": label, "error": repr(e), "trace": traceback.format_exc(limit=3)}


def ema(values: Iterable[float], n: int) -> float:
    vals=[fnum(v) for v in values]
    if not vals: return 0.0
    if len(vals)<n: return sum(vals)/len(vals)
    k=2.0/(n+1.0); e=vals[0]
    for v in vals[1:]: e=v*k+e*(1-k)
    return e


def sma(values: Iterable[float], n: int) -> float:
    vals=[fnum(v) for v in values]
    if not vals: return 0.0
    vals=vals[-n:]
    return sum(vals)/len(vals)


def rsi(values: Iterable[float], n: int = 14) -> float:
    vals=[fnum(v) for v in values]
    if len(vals)<n+1: return 50.0
    gains=[]; losses=[]
    for i in range(-n,0):
        diff=vals[i]-vals[i-1]
        gains.append(max(diff,0)); losses.append(max(-diff,0))
    ag=sum(gains)/n; al=sum(losses)/n
    if al<=0: return 100.0
    return 100.0 - 100.0/(1.0+ag/al)


def atr(candles: list[dict], n: int = 14) -> float:
    if len(candles)<2: return 0.0
    trs=[]
    for i in range(1,len(candles)):
        h=fnum(candles[i].get('high')); l=fnum(candles[i].get('low')); pc=fnum(candles[i-1].get('close'))
        trs.append(max(h-l, abs(h-pc), abs(l-pc)))
    return sma(trs, n)


def wilson_lcb(wins: float, total: float, z: float = 1.64) -> float:
    if total <= 0: return 0.0
    phat=wins/total
    denom=1+z*z/total
    center=phat+z*z/(2*total)
    margin=z*math.sqrt((phat*(1-phat)+z*z/(4*total))/total)
    return max(0.0,(center-margin)/denom)
