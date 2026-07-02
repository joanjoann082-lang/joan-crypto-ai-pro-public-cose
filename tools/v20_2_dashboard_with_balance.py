#!/usr/bin/env python3
from __future__ import annotations

import os
import runpy
import subprocess
import sys
from pathlib import Path

ROOT = Path("/storage/emulated/0/Download/joan_crypto_ai_pro_v14")
sys.path.insert(0, str(ROOT))

from tools.v23_equity_panel import print_equity_panel

TARGET = ROOT / "tools/v20_2_canonical_dashboard.py"

if not TARGET.exists():
    raise SystemExit("FAIL: tools/v20_2_canonical_dashboard.py not found")

_orig_system = os.system
_orig_run = subprocess.run
_orig_call = subprocess.call

def _is_clear_cmd(cmd) -> bool:
    s = " ".join(cmd) if isinstance(cmd, (list, tuple)) else str(cmd)
    return "clear" in s or "\\033c" in s or "\\x1bc" in s

def _after_clear(cmd):
    if _is_clear_cmd(cmd):
        try:
            print_equity_panel()
        except Exception as e:
            print(f"EQUITY_PANEL_ERROR: {e}")

def patched_system(cmd):
    rc = _orig_system(cmd)
    _after_clear(cmd)
    return rc

def patched_run(*args, **kwargs):
    res = _orig_run(*args, **kwargs)
    if args:
        _after_clear(args[0])
    return res

def patched_call(*args, **kwargs):
    res = _orig_call(*args, **kwargs)
    if args:
        _after_clear(args[0])
    return res

os.system = patched_system
subprocess.run = patched_run
subprocess.call = patched_call

print_equity_panel()
runpy.run_path(str(TARGET), run_name="__main__")
