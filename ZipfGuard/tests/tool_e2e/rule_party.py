"""Synthetic shares-only harness for the identical live rule circuit."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from services.common import bootstrap
import json,argparse,time
from mpyc.runtime import mpc
from privacy.service.compute import Computer
async def main():
 parser=argparse.ArgumentParser();parser.add_argument('--fixture',required=True);parser.add_argument('--result-path',required=True);args=parser.parse_args()
 fixture=json.loads(Path(args.fixture).read_text('utf-8'));c=Computer(mpc,json.loads((ROOT/'contracts/public_reference.json').read_text('utf-8')))
 await mpc.start();result=[];started=time.perf_counter()
 hot=await c.input(fixture['hot'])
 for case in fixture['cases']:
  chars=await c.input(case['shares']);meta=c.characterize(chars)
  flag=c.fragment(case['fragment'],chars,meta,[(hot,c.sec(1))])
  accepted=bool(await mpc.output(flag));result.append({'id':case['id'],'fragment':case['fragment'],'accepted':accepted,'expected':case['expected'],'passed':accepted==case['expected']})
  print(case['id'],accepted,flush=True)
 Path(args.result_path).write_text(json.dumps({'party':mpc.pid,'seconds':time.perf_counter()-started,'cases':result},indent=2),encoding='utf-8')
 await mpc.shutdown()
if __name__=='__main__':mpc.run(main())
