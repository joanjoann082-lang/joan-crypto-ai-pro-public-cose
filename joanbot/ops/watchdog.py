from __future__ import annotations
import subprocess, time, os
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
def alive():
    try: return bool(subprocess.check_output(['pgrep','-af','joanbot.runner'], text=True).strip())
    except Exception: return False
while True:
    if not alive():
        subprocess.Popen(['nohup','python','-u','-m','joanbot.runner'], cwd=str(ROOT), stdout=open(ROOT/'logs/runner.log','ab'), stderr=open(ROOT/'logs/runner_errors.log','ab'))
    time.sleep(30)
