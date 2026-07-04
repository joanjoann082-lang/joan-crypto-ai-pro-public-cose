from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from joanbot.storage import get_db


def main():
    db=get_db()
    print("===== INFORME APRENENTATGE CAUSAL NET =====")
    for t in ["estat_causal_quant", "estat_promocio_quant", "memoria_edge_neta", "resultats_quant_nets"]:
        try:
            print(t, db.query(f"SELECT COUNT(*) c FROM {t}")[0]["c"])
        except Exception as e:
            print(t, "ERR", e)
    print("\nTOP CAUSAL EXECUTABLE")
    rows=db.query("""
    SELECT key,estat,live_n,ROUND(live_exp_r,4) live_exp,ROUND(live_pf,3) live_pf,
           forward_n,ROUND(forward_exp_r,4) fwd_exp,ROUND(forward_pf,3) fwd_pf
    FROM estat_causal_quant
    WHERE estat!='QUARANTENA'
    ORDER BY estat='VALIDAT' DESC, estat='CANARI' DESC, estat='EXPLORAR' DESC, live_n DESC, forward_n DESC
    LIMIT 40
    """)
    for r in rows:
        print(f"{r['estat']} liveN={r['live_n']} liveExp={r['live_exp']} livePF={r['live_pf']} fwdN={r['forward_n']} fwdExp={r['fwd_exp']} fwdPF={r['fwd_pf']} key={r['key']}")
    print("\nCAUSAL QUARANTENA")
    rows=db.query("""
    SELECT key,estat,live_n,ROUND(live_exp_r,4) live_exp,ROUND(live_pf,3) live_pf,
           forward_n,ROUND(forward_exp_r,4) fwd_exp,ROUND(forward_pf,3) fwd_pf
    FROM estat_causal_quant
    WHERE estat='QUARANTENA'
    ORDER BY live_exp_r ASC, forward_exp_r ASC
    LIMIT 30
    """)
    for r in rows:
        print(f"Q liveN={r['live_n']} liveExp={r['live_exp']} livePF={r['live_pf']} fwdN={r['forward_n']} fwdExp={r['fwd_exp']} fwdPF={r['fwd_pf']} key={r['key']}")

if __name__ == '__main__':
    main()
