from __future__ import annotations
import json, os, subprocess, time
from pathlib import Path
from ..config import STATE_PATH, CFG
from ..storage import get_db
from ..utils import read_json, utc_now_iso

def pgrep(pattern: str) -> bool:
    try: return bool(subprocess.check_output(['pgrep','-af',pattern], text=True, timeout=2).strip())
    except Exception: return False

def health() -> dict:
    st=read_json(STATE_PATH,{})
    runner=pgrep('joanbot.runner')
    issues=[]
    if not runner: issues.append('RUNNER_DOWN')
    try:
        ts=st.get('ts'); # ISO parse rough
    except Exception: ts=None
    db=get_db().state()
    wallet=st.get('wallet',{}) if isinstance(st,dict) else {}
    return {'ts':utc_now_iso(),'status':'OK' if not issues else 'FAIL','issues':issues,'runner_alive':runner,'state_ts':st.get('ts'), 'equity':wallet.get('equity'), 'open_positions':len(wallet.get('open',[])) if isinstance(wallet.get('open'),list) else 0,'db':db,'errors':st.get('errors',[]) if isinstance(st,dict) else []}

def main(): print(json.dumps(health(), indent=2, sort_keys=True))
if __name__=='__main__': main()
