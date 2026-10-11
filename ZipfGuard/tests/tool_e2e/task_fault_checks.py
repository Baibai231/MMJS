"""Real task completion, durable recovery, quotas, and one-node backup tests."""
from client import Client,ROOT
import json,time,secrets,subprocess,sys,os,copy
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from services.common.security import public_hex,receipt_quorum,load_private,token
from services.common.store import Store
from services.common.contracts import Policy

def run():
 c=Client();cfg=c.cfg;results=[];out=ROOT/'reports/tool_acceptance';admin=json.loads((ROOT/'tool_runtime/qa-admin-session.json').read_text('utf-8'))['session'];headers={'Authorization':'Bearer '+admin};suffix=secrets.token_hex(3)
 def record(name,passed,**details):
  results.append({'case':name,'passed':bool(passed),**details});(out/'task-fault-results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8');print(name,'PASS' if passed else 'FAIL',flush=True);assert passed,name
 def operation(name,op,expected='accepted',**kwargs):
  result=c.operation(op,**kwargs);record(name,result['outcome']==expected,outcome=result['outcome'],seconds=round(result['seconds'],3));return result
 operation('reset_public_policy_for_task_fixture','apply_policy','policy_applied',params={'policy':Policy().model_dump()},session=admin)
 accounts=[]
 for index in range(2):
  name=f'task-{index}-{suffix}';result=operation('task_fixture_registration_'+str(index),'register',account=name,passwords={'new':'Ab3!xy89'},params={'recovery_key':public_hex(Ed25519PrivateKey.generate())});accounts.append((name,result['session']))
 used=c.http.get(cfg['admin_api']+'/dashboard',headers=headers).json()['task_used']
 plan=operation('secret_quota_one_of_two','assign_plan','plan_assigned',params={'quota':2,'total_cap':used+1,'fragments':[1],'shapes':[],'positions':[]},session=admin)
 assigned=plan['result']['assigned'];record('exactly_one_task_with_budget',len(assigned)==1 and assigned[0] in [a for a,_ in accounts])
 a=assigned[0];session=next(s for n,s in accounts if n==a)
 me=c.http.get(cfg['user_api']+'/me',headers={'Authorization':'Bearer '+session}).json();record('user_sees_own_pending_task',me['task']['status']=='pending')
 operation('task_requirement_rejects_short_change','change','policy_rejected',account=a,passwords={'old':'Ab3!xy89','new':'Cd4!ab12'})
 me=c.http.get(cfg['user_api']+'/me',headers={'Authorization':'Bearer '+session}).json();record('failed_change_keeps_task_pending',me['task']['status']=='pending')
 changed=operation('real_change_completes_task','change',account=a,passwords={'old':'Ab3!xy89','new':'Completed-Task-Password-56'})
 me=c.http.get(cfg['user_api']+'/me',headers={'Authorization':'Bearer '+changed['session']}).json();record('task_completion_after_credential_swap',me['task']['status']=='completed')
 repeat=operation('cumulative_task_budget_preserved','assign_plan','plan_assigned',params={'quota':2,'total_cap':used+1,'fragments':[1],'shapes':[],'positions':[]},session=admin);record('no_extra_assignment_after_budget',repeat['result']['assigned']==[])
 # A compute transaction can complete while the business API hasn't consumed
 # the receipt; the authorized reconciler repairs only whitelisted metadata.
 recovery=Ed25519PrivateKey.generate();name='unconsumed-'+suffix
 result=c.operation('register',name,{'new':'Unconsumed-Commit-Password-52'},params={'recovery_key':public_hex(recovery)},complete=False)
 deadline=time.time()+25
 while time.time()<deadline:
  row=c.http.get(cfg['user_api']+'/account-version/'+name).json()
  if row['version']==1:break
  time.sleep(.5)
 record('business_write_failure_reconciled',row['version']==1)
 # Crash a single party after common signatures are durably recorded but
 # before swapping credentials. Other nodes must not claim full readiness.
 nonce=secrets.token_hex(32);name='crash-'+suffix
 (ROOT/'tool_runtime/party_0/local-test-fault.json').write_text(json.dumps({'phase':'after_decision','nonce':nonce}),encoding='utf-8')
 try:c.operation('register',name,{'new':'Crash-Recovery-Password-48'},params={'recovery_key':public_hex(Ed25519PrivateKey.generate())},nonce=nonce,timeout=20);unexpected=True
 except Exception:unexpected=False
 record('injected_single_party_interrupt',not unexpected and (ROOT/'tool_runtime/party_0'/('used-fault-'+nonce+'.json')).exists())
 health=c.http.get(cfg['nodes'][1]+'/health').json();record('peer_disconnect_not_ready',not health['ready'])
 subprocess.run([sys.executable,str(ROOT/'ops/tool.py'),'stop'],cwd=ROOT,check=True)
 subprocess.run([sys.executable,str(ROOT/'ops/tool.py'),'start'],cwd=ROOT,check=True)
 deadline=time.time()+30
 while time.time()<deadline:
  try:
   health=[c.http.get(url+'/health').json() for url in cfg['nodes']]
   if all(h['ready'] for h in health):break
  except Exception:pass
  time.sleep(.5)
 record('restart_common_decision_recovery',all(h['ready'] for h in health))
 deadline=time.time()+25
 while time.time()<deadline:
  row=c.http.get(cfg['user_api']+'/account-version/'+name).json()
  if row['version']==1:break
  time.sleep(.5)
 record('crashed_commit_business_reconciled',row['version']==1)
 operation('recovered_credential_login','login',account=name,passwords={'candidate':'Crash-Recovery-Password-48'})
 # Persistent ingress is charged before bindings/replay, including failures.
 store=Store(str(ROOT/'tool_runtime/party_0/state.sqlite'));before=store.get('ingress','source:127.0.0.1',[])
 response=c.http.post(cfg['nodes'][0]+'/submit',json={'invalid':True});after=store.get('ingress','source:127.0.0.1',[])
 record('malformed_ingress_charged',response.status_code==400 and len(after)>len(before))
 # Sign a read-only administrator identity: role authorization still applies
 # even with a valid service signature in the controlled integration fixture.
 business=Store(str(ROOT/'tool_runtime/business.sqlite'));business.put('administrators','qa-auditor',{'role':'auditor','enabled':True})
 admin_cfg=json.loads((ROOT/'tool_runtime/admin/config.json').read_text('utf-8'));auditor=token(load_private(admin_cfg['key']),'admin-session',{'site':cfg['site'],'sub':'qa-auditor','role':'auditor'})
 record('auditor_can_read',c.http.get(cfg['admin_api']+'/dashboard',headers={'Authorization':'Bearer '+auditor}).status_code==200)
 record('auditor_cannot_create_job',c.http.post(cfg['admin_api']+'/jobs',json={},headers={'Authorization':'Bearer '+auditor}).status_code==403)
 print('Task and fault checks passed:',len(results),flush=True)
if __name__=='__main__':run()
