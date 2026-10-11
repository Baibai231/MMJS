"""Verify both website and computation-party per-round caps through normal auth."""
from pathlib import Path
import sys,json,time
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from ops.run_population_demo import DemoClient,admin_session,PRIVATE,OUT
from services.common.store import Store
config=json.loads((ROOT/'tool_runtime/admin/config.json').read_text('utf-8'))
store=Store(config['store']);state=store.get('site','state')
while state['simulation']['status']!='completed':
    time.sleep(3);state=store.get('site','state')
assert not (PRIVATE/'pending-operation.json').exists()
assert all(json.loads((ROOT/f'tool_runtime/party_{i}/config.json').read_text('utf-8'))['round_task_cap']==20 for i in range(3))
c=DemoClient();session=admin_session(c)
params={'quota':21,'total_cap':200,'fragments':[3],'snapshot':state['simulation']['final_snapshot'],'positions':[],'shapes':[]}
response=c.http.post(c.cfg['admin_api']+'/jobs',json={'op':'assign_plan','params':params,'account':None,'policy_version':state['policy_version'],'account_version':0},headers={'Authorization':'Bearer '+session})
assert response.status_code==400,response.text
result=c.operation('assign_plan',params=params,session=session)
after=store.get('site','state')
checks={'business_rejects_over_round_cap':response.status_code==400,'mpc_rejects_over_round_cap':result['outcome']=='task_budget_exhausted','allocation_budget_unchanged':after['task_used']==state['task_used']==102,'privacy_budget_unchanged':after['epsilon_spent']==state['epsilon_spent']==5}
summary={'passed':all(checks.values()),'checks':checks,'outcome':result['outcome'],'round_cap':20,'trial_quota':21}
(OUT/'quota-check.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(summary,ensure_ascii=False))
assert summary['passed']
