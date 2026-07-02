from __future__ import annotations
import argparse, json
from .ops.health import health
from .storage import get_db
from .testing.replay_backtester import ReplayBacktester
from .analytics.edge_report import EdgeReport
from .analytics.missed_opportunities import MissedOpportunityAnalyzer
from .testing.walk_forward import WalkForwardRunner
from .utils import read_json
from .config import STATE_PATH

def main():
    ap=argparse.ArgumentParser(); sub=ap.add_subparsers(dest='cmd')
    sub.add_parser('status'); sub.add_parser('edge'); sub.add_parser('why'); sub.add_parser('forward'); sub.add_parser('state'); sub.add_parser('audit'); sub.add_parser('missed'); wf=sub.add_parser('walkforward'); wf.add_argument('--symbol',default='BTCUSDT'); wf.add_argument('--interval',default='1h')
    b=sub.add_parser('backtest'); b.add_argument('--symbol',default='BTCUSDT'); b.add_argument('--interval',default='1h'); b.add_argument('--limit',type=int,default=800)
    args=ap.parse_args(); db=get_db()
    if args.cmd=='status': print(json.dumps(health(),indent=2)); return
    if args.cmd=='state': print(json.dumps(read_json(STATE_PATH,{}),indent=2)[:8000]); return
    if args.cmd=='edge': print(json.dumps(db.query('SELECT * FROM edge_memory ORDER BY n DESC LIMIT 50'), indent=2)); return
    if args.cmd=='why': print(json.dumps(db.query('SELECT ts,symbol,action,side,setup,final_score,confidence,size_usd,payload FROM decisions ORDER BY id DESC LIMIT 20'), indent=2)[:12000]); return
    if args.cmd=='forward': print(json.dumps(db.query('SELECT * FROM forward_results ORDER BY id DESC LIMIT 50'), indent=2)); return
    if args.cmd=='audit': print(json.dumps(EdgeReport().full(), indent=2)); return
    if args.cmd=='missed': print(json.dumps(MissedOpportunityAnalyzer().analyze(), indent=2)); return
    if args.cmd=='walkforward': print(json.dumps(WalkForwardRunner().run(args.symbol,args.interval), indent=2)); return
    if args.cmd=='backtest': print(json.dumps(ReplayBacktester().run(args.symbol,args.interval,args.limit), indent=2)); return
    ap.print_help()
if __name__=='__main__': main()
