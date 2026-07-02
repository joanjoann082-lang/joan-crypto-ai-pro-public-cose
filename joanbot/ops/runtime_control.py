from __future__ import annotations
import json, os, sqlite3, time, uuid
from pathlib import Path
from typing import Any, Dict, Optional
from datetime import datetime, timezone

CONTROL_PATH = Path("data/runtime_control.json")
DB_PATH = Path("data/joanbot_v14.sqlite")

DEFAULT: Dict[str, Any] = {
    "paused": False,
    "allow_new_trades": True,
    "allow_long": True,
    "allow_short": True,
    "mode": "normal",
    "risk_mult": 1.0,
    "open_threshold": None,
    "probe_threshold": None,
    "pending_change": None,
    "last_applied": None,
    "updated_at": None,
    "updated_by": "system",
    "version": "V24_INSTITUTIONAL_COMMAND_LAYER"
}

SAFE_LIMITS = {
    "open_threshold": (70.0, 92.0),
    "probe_threshold": (55.0, 82.0),
    "risk_mult": (0.05, 1.25),
}
VALID_MODES = {"ultra_conservative", "conservative", "normal", "aggressive"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(x)))


class RuntimeControl:
    def __init__(self, path: Path = CONTROL_PATH, db_path: Path = DB_PATH):
        self.path = Path(path)
        self.db_path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.ensure_schema()

    def ensure_schema(self) -> None:
        try:
            con = sqlite3.connect(self.db_path)
            con.execute("""
            CREATE TABLE IF NOT EXISTS runtime_control_audit(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT,
                source TEXT,
                action TEXT,
                before_json TEXT,
                after_json TEXT,
                pending_json TEXT,
                note TEXT
            )
            """)
            con.commit(); con.close()
        except Exception:
            pass

    def _audit(self, source: str, action: str, before: Dict[str, Any], after: Dict[str, Any], pending: Optional[Dict[str, Any]]=None, note: str="") -> None:
        try:
            con = sqlite3.connect(self.db_path)
            con.execute(
                "INSERT INTO runtime_control_audit(ts,source,action,before_json,after_json,pending_json,note) VALUES(?,?,?,?,?,?,?)",
                (now_iso(), source, action, json.dumps(before, sort_keys=True), json.dumps(after, sort_keys=True), json.dumps(pending or {}, sort_keys=True), note)
            )
            con.commit(); con.close()
        except Exception:
            pass

    def load(self) -> Dict[str, Any]:
        if not self.path.exists():
            self.save(dict(DEFAULT), source="system", action="init", audit=False)
            return dict(DEFAULT)
        try:
            raw = json.loads(self.path.read_text(errors="ignore"))
            data = dict(DEFAULT)
            if isinstance(raw, dict):
                data.update(raw)
            return data
        except Exception:
            return dict(DEFAULT)

    def save(self, data: Dict[str, Any], source: str="system", action: str="save", audit: bool=True, before: Optional[Dict[str, Any]]=None, pending: Optional[Dict[str, Any]]=None, note: str="") -> Dict[str, Any]:
        out = dict(DEFAULT)
        out.update(data or {})
        out["updated_at"] = now_iso()
        out["version"] = "V24_INSTITUTIONAL_COMMAND_LAYER"
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(out, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self.path)
        if audit:
            self._audit(source, action, before or {}, out, pending, note)
        return out

    def policy(self, default_open: float=74.0, default_probe: float=58.0) -> Dict[str, Any]:
        c = self.load()
        mode = str(c.get("mode") or "normal").lower()
        if mode not in VALID_MODES:
            mode = "normal"
        risk = float(c.get("risk_mult") if c.get("risk_mult") is not None else 1.0)
        open_th = float(c.get("open_threshold") if c.get("open_threshold") is not None else default_open)
        probe_th = float(c.get("probe_threshold") if c.get("probe_threshold") is not None else default_probe)

        if mode == "ultra_conservative":
            open_th += 8; probe_th += 6; risk *= 0.35
        elif mode == "conservative":
            open_th += 4; probe_th += 3; risk *= 0.65
        elif mode == "aggressive":
            open_th -= 2; probe_th -= 2; risk *= 1.10

        open_th = _clamp(open_th, *SAFE_LIMITS["open_threshold"])
        probe_th = _clamp(probe_th, *SAFE_LIMITS["probe_threshold"])
        risk = _clamp(risk, *SAFE_LIMITS["risk_mult"])

        return {
            "paused": bool(c.get("paused")),
            "allow_new_trades": bool(c.get("allow_new_trades")),
            "allow_long": bool(c.get("allow_long")),
            "allow_short": bool(c.get("allow_short")),
            "mode": mode,
            "risk_mult": risk,
            "open_threshold_effective": open_th,
            "probe_threshold_effective": probe_th,
            "raw": c,
        }

    def _validate(self, apply: Dict[str, Any]) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for k, v in apply.items():
            if k in ("paused", "allow_new_trades", "allow_long", "allow_short"):
                out[k] = bool(v)
            elif k == "mode":
                v = str(v).lower()
                if v not in VALID_MODES:
                    raise ValueError("Mode vàlid: ultra_conservative | conservative | normal | aggressive")
                out[k] = v
            elif k in SAFE_LIMITS:
                lo, hi = SAFE_LIMITS[k]
                fv = float(v)
                if fv < lo or fv > hi:
                    raise ValueError(f"{k} fora de límit segur: {lo}–{hi}")
                out[k] = fv
            elif k in ("open_threshold", "probe_threshold") and v is None:
                out[k] = None
            else:
                raise ValueError(f"Camp no permès: {k}")
        return out

    def set_pending(self, apply: Dict[str, Any], source: str="telegram", note: str="") -> Dict[str, Any]:
        before = self.load()
        clean = self._validate(apply)
        pending = {
            "id": str(uuid.uuid4())[:8],
            "apply": clean,
            "before": {k: before.get(k) for k in clean.keys()},
            "created_at": time.time(),
            "expires_at": time.time()+300,
            "note": note,
        }
        after = dict(before)
        after["pending_change"] = pending
        after["updated_by"] = source
        return self.save(after, source=source, action="pending", before=before, pending=pending, note=note)

    def apply_direct(self, apply: Dict[str, Any], source: str="telegram", note: str="") -> Dict[str, Any]:
        before = self.load()
        clean = self._validate(apply)
        after = dict(before)
        prev = {k: before.get(k) for k in clean.keys()}
        after.update(clean)
        after["pending_change"] = None
        after["last_applied"] = {"apply": clean, "before": prev, "source": source, "ts": now_iso(), "note": note}
        after["updated_by"] = source
        return self.save(after, source=source, action="apply_direct", before=before, note=note)

    def confirm(self, source: str="telegram") -> Dict[str, Any]:
        before = self.load()
        p = before.get("pending_change")
        if not p:
            raise ValueError("No hi ha cap canvi pendent.")
        if float(p.get("expires_at", 0)) < time.time():
            after = dict(before); after["pending_change"] = None
            self.save(after, source=source, action="pending_expired", before=before)
            raise ValueError("El canvi pendent ha caducat. Torna’l a demanar.")
        clean = self._validate(p.get("apply") or {})
        after = dict(before)
        after.update(clean)
        after["pending_change"] = None
        after["last_applied"] = {"apply": clean, "before": p.get("before", {}), "source": source, "ts": now_iso(), "note": p.get("note", "")}
        after["updated_by"] = source
        return self.save(after, source=source, action="confirm", before=before, pending=p, note=p.get("note", ""))

    def cancel(self, source: str="telegram") -> Dict[str, Any]:
        before = self.load(); after = dict(before); p = after.get("pending_change")
        after["pending_change"] = None; after["updated_by"] = source
        return self.save(after, source=source, action="cancel", before=before, pending=p)

    def undo(self, source: str="telegram") -> Dict[str, Any]:
        before = self.load()
        last = before.get("last_applied")
        if not last or not isinstance(last.get("before"), dict):
            raise ValueError("No hi ha cap canvi recent per desfer.")
        restore = self._validate(last["before"])
        after = dict(before)
        after.update(restore)
        after["last_applied"] = None
        after["pending_change"] = None
        after["updated_by"] = source
        return self.save(after, source=source, action="undo", before=before, note="undo last_applied")
