"""Real three-party checks of the LOCAL synthetic histogram path."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from services.common import bootstrap
from mpyc.runtime import mpc
from privacy.service.compute import Computer,WIDTH,SHAPES
import json,time

def chars(c,word):
    return [c.sec(x) for x in list(word.encode('ascii'))+[0]*(WIDTH-len(word))]

async def main():
    await mpc.start()
    c=Computer(mpc,json.loads((ROOT/'contracts/public_reference.json').read_text('utf-8')))
    words=['amberfox','cloudreef','12345678','Orchid817!','SableHarbor!!28']
    weights=[3,2,2,1,1]
    groups=[(chars(c,w),c.sec(n)) for w,n in zip(words,weights)]
    expanded=[chars(c,w) for w,n in zip(words,weights) for _ in range(n)]
    start=time.perf_counter()
    vector,banks,metas=await c.compact_snapshot(groups)
    compact_time=time.perf_counter()-start
    expected,reference=await c.snapshot(expanded)
    checks=[a==b for a,b in zip(vector,expected)]
    # Ties need not have the same bank ordering as expanded account order.
    # Frequency vectors must agree; every active bank entry has its stated count.
    for s,group in enumerate(banks):
        for r,(row,active) in enumerate(group):
            count=mpc.sum(c.same_password(row,item) for item in expanded)
            checks.append((1-active)+active*(count==vector[len(SHAPES)+1+3*s+r]))
    groups=await c.compact_add(groups,chars(c,'amberfox'),-1)
    groups=await c.compact_add(groups,chars(c,'SableHarbor!!28'),1)
    groups=await c.compact_add(groups,chars(c,'Violet-Cascade-39'),1)
    groups=await c.compact_add(groups,chars(c,'Violet-Cascade-39'),-1)
    vector,_,_=await c.compact_snapshot(groups)
    expected,_=await c.snapshot([chars(c,w) for w in ['amberfox']*2+['cloudreef']*2+['12345678']*2+['Orchid817!']+['SableHarbor!!28']*2])
    checks.extend(a==b for a,b in zip(vector,expected))
    checks.append(c.same_password(chars(c,'amberfox'),chars(c,'amberfox')))
    checks.append(1-c.same_password(chars(c,'amberfox'),chars(c,'amberfow')))
    passed=bool(await mpc.output(mpc.all(checks)))
    if mpc.pid==0:
        target=ROOT/'reports/tool_population_1000_20261012';target.mkdir(exist_ok=True)
        (target/'compact-protocol-check.json').write_text(json.dumps({'passed':passed,'checks':len(checks),'compact_seconds':compact_time,'scope':'real three-party synthetic protocol; no production population tested'},indent=2),encoding='utf-8')
        print('COMPACT_CHECK',passed,len(checks),'seconds',time.perf_counter()-start,flush=True)
    await mpc.shutdown()
    if not passed:raise AssertionError('compact secret histogram mismatch')

mpc.run(main())
