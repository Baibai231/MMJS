"""Resume the original unknown profile job and finish genuine service checks."""
from client import Client,ROOT
import json,time,secrets
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from services.common.security import public_hex
def run():
 c=Client();cfg=c.cfg;admin=json.loads((ROOT/'tool_runtime/qa-admin-session.json').read_text('utf-8'))['session'];headers={'Authorization':'Bearer '+admin};results=[]
 output=ROOT/'reports/tool_acceptance'
 def record(name,passed,**other):
  results.append({'case':name,'passed':bool(passed),**other});(output/'completion-results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8');print(name,'PASS' if passed else 'FAIL',flush=True);assert passed,name
 def dashboard():return c.http.get(cfg['admin_api']+'/dashboard',headers=headers).json()
 def wait(id,timeout=900):
  start=time.time()
  while time.time()-start<timeout:
   value=c.http.get(cfg['admin_api']+'/jobs/'+id,headers=headers).json()
   if value['status'] in {'completed','unknown','failed','cancelled'}:return value,time.time()-start
   time.sleep(.5)
  raise TimeoutError(id)
 def job(name,op,params,account=None):
  info=c.http.get(cfg['user_api']+'/station').json();version=c.http.get(cfg['user_api']+'/account-version/'+account).json()['version'] if account else 0
  response=c.http.post(cfg['admin_api']+'/jobs',json={'op':op,'params':params,'account':account,'policy_version':info['policy_version'],'account_version':version},headers=headers);response.raise_for_status();id=response.json()['id'];value,elapsed=wait(id);record(name,value['status']=='completed',outcome=value.get('outcome'),seconds=round(elapsed,3),error=value.get('error'));return value
 # Reconcile original nonce first. If it never reached all nodes, leave the
 # charged failed reservation intact and create an explicit new attempt.
 for id,value in dashboard()['jobs'].items():
  if value['op']=='profile' and value['status']=='unknown':
   response=c.http.post(cfg['admin_api']+'/jobs/'+id+'/retry',json={},headers=headers);response.raise_for_status();value,elapsed=wait(id);record('original_profile_request_reconciled',value['status']=='completed',outcome=value.get('outcome'),seconds=round(elapsed,3),error=value.get('error'))
 state=dashboard();profile=list(state['profiles'].values())[-1];record('private_profile_real_release',len(profile['counts'])==9 and len(profile['spectra'])==9 and len(profile['fragment_violations'])==18)
 spent=state['epsilon_spent'];job('cached_profile_live_job','profile',{'epsilon':1});record('cache_no_new_epsilon',dashboard()['epsilon_spent']==spent)
 response=c.http.post(cfg['admin_api']+'/preview/registration',json={'snapshot':profile['snapshot'],'k':3,'j':1},headers=headers);response.raise_for_status();record('joint_candidates_hold_without_response_confirmation',all(v['eligible'] or v['decision']=='hold' for v in response.json()['candidates']))
 response=c.http.post(cfg['admin_api']+'/preview/intervention',json={'snapshot':profile['snapshot'],'quota':2,'total_cap':100},headers=headers);response.raise_for_status();record('all_18_intervention_candidates',len(response.json()['candidates'])==18)
 job('secret_eligibility_task_quota','assign_plan',{'snapshot':profile['snapshot'],'shapes':[],'positions':[],'quota':2,'total_cap':100,'fragments':[3]})
 record('pending_tasks_not_falsely_completed',all(v['task']['status']=='pending' for v in dashboard()['accounts'].values() if v.get('task')))
 # Choose the known fixture account from the original acceptance attempt.
 accounts=dashboard()['accounts'];a=next(a for a in accounts if a.startswith('full-'));password='Synthetic-Recovered-Password-83'
 job('account_disable','set_account',{'enabled':False},a)
 result=c.operation('login',a,{'candidate':password});record('disabled_login_denied',result['outcome']=='account_disabled')
 job('account_enable','set_account',{'enabled':True},a)
 challenge=c.http.post(cfg['reference_site']+'/challenge').json()['nonce'];result=c.operation('login',a,{'candidate':password},nonce=challenge);record('site_challenge_private_login',result['outcome']=='accepted',seconds=round(result['seconds'],3))
 proof={'events':result['events'],'context':result['context'],'challenge':challenge};response=c.http.post(cfg['reference_site']+'/authenticate',json=proof);record('site_quorum_authentication',response.status_code==200);record('site_quorum_replay_denied',c.http.post(cfg['reference_site']+'/authenticate',json=proof).status_code==403)
 report=c.http.get(cfg['admin_api']+'/export',headers=headers).text;record('export_no_fixture_plaintext',all(v not in report for v in ['Synthetic-Old-Password-39','Synthetic-New-Password-74',password,'Unique-Sample-Password-37']))
 result=c.operation('delete',a,{'candidate':password});record('private_deletion',result['outcome']=='accepted');result=c.operation('login',a,{'candidate':password});record('deleted_account_denied',result['outcome']=='stale_or_missing_account')
 print('Completion checks passed:',len(results),flush=True)
if __name__=='__main__':run()
