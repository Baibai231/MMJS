"""Isolated control-logic checks, clearly distinct from actual MPC tests."""
from client import ROOT
import asyncio,copy,json,secrets
from fastapi.testclient import TestClient
import httpx
from services.common.business import Business
from services.common.security import token
from services.common.contracts import Policy
from services.jobs.runner import Runner
def run():
 suffix=secrets.token_hex(3);cfg=json.loads((ROOT/'tool_runtime/admin/config.json').read_text('utf-8'));cfg['store']=str(ROOT/'tool_runtime'/('control-'+suffix+'.sqlite'))
 b=Business(cfg,'admin');b.store.put('administrators','control-owner',{'role':'owner','enabled':True})
 session=token(b.key,'admin-session',{'site':cfg['site'],'sub':'control-owner','role':'owner'});headers={'Authorization':'Bearer '+session};client=TestClient(b.app);r=Runner(cfg);results=[]
 def record(name,passed):
  results.append({'case':name,'passed':bool(passed),'scope':'isolated control logic'});print(name,'PASS' if passed else 'FAIL',flush=True);assert passed,name
 body={'op':'apply_policy','params':{'policy':Policy().model_dump()},'account':None,'policy_version':1,'account_version':0}
 value=client.post('/jobs',json=body,headers=headers);record('durable_queue',value.status_code==200);id=value.json()['id']
 value=client.post('/jobs/'+id+'/cancel',json={},headers=headers);record('queued_job_cancelled',value.json()['status']=='cancelled');record('cancelled_job_cannot_be_claimed',r.claim(id) is None)
 value=client.post('/jobs',json=body,headers=headers);id=value.json()['id'];record('job_claim_transaction',r.claim(id)['status']=='running')
 value=client.post('/jobs/'+id+'/cancel',json={},headers=headers);record('running_job_cannot_be_cancelled_as_unexecuted',value.status_code==409)
 value=client.post('/settings',json={'retention_days':90,'scheduled_release':False,'automatic_review':True,'registration_batch_size':1,'k':3,'j':1,'cost_cap':0,'pending_cap':0},headers=headers);record('review_configuration_saved',value.status_code==200)
 from services.common.store import Store
 published=Store(str(ROOT/'tool_runtime/business.sqlite')).get('site','state')['profiles']['population-14']
 state=b.store.get('site','state');state['profiles']={'population-14':copy.deepcopy(published)};state['registrations_total']=2;state['epsilon_spent']=3;b.store.put('site','state',state);b.store.put('schedule','reviewed_registrations',1)
 epoch=[14]
 transport=httpx.MockTransport(lambda request:httpx.Response(200,json={'ready':True,'data_epoch':epoch[0]}))
 async def schedule():
  async with httpx.AsyncClient(transport=transport) as http:
   await r.schedule(http)
   record('automatic_review_records_hold',any(e['kind']=='automatic_policy_review' and e['data']['decision']=='hold' for e in b.store.events()))
   record('review_does_not_invent_policy_gain',b.store.get('site','state')['policy_version']==1)
   # Isolate queue triggering by marking the earlier simulated claimed job done.
   old=b.store.get('jobs',id);old['status']='completed';b.store.put('jobs',id,old)
   epoch[0]=15;state=b.store.get('site','state');state['registrations_total']=3;b.store.put('site','state',state);r.last_schedule=0
   await r.schedule(http)
   record('approved_missing_snapshot_queues_release',any(j['op']=='profile' and j['status']=='queued' for j in b.store.list('jobs').values()))
 asyncio.run(schedule())
 (ROOT/'reports/tool_acceptance/control-results.json').write_text(json.dumps({'passed':True,'scope':'isolated HTTP/queue/scheduler logic; no actual MPC release simulated','cases':results},ensure_ascii=False,indent=2),encoding='utf-8')
if __name__=='__main__':run()
