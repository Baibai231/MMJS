"""Run additional contextual study without changing published snapshots."""
import argparse
import json
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from experiments.contextual_pipeline import run_contextual


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--config',type=Path,default=ROOT/'configs/contextual_smoke.json')
    parser.add_argument('--output-dir',type=Path,required=True)
    parser.add_argument('--seed',type=int)
    parser.add_argument('--users',type=int)
    parser.add_argument('--development',type=int)
    args=parser.parse_args()
    cfg=json.loads(args.config.read_text(encoding='utf-8'))
    if args.seed is not None:cfg['intervention']['seed']=args.seed
    for key in ('users','development'):
        if getattr(args,key) is not None:cfg['intervention']['data'][key]=getattr(args,key)
    report=run_contextual(cfg,args.output_dir,progress=lambda msg:print(msg,flush=True))
    for name,arm in report['arms'].items():
        a1=arm['A1']['minauto'][-1]['rate'] if arm['A1'] else None
        print(name,'D=',arm['final_distance'],'notified=',arm['ledger']['adaptive_affected'],'A1=',a1,flush=True)


if __name__=='__main__':main()
