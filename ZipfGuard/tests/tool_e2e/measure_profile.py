from client import Client,ROOT
import json,time
def run():
 c=Client();cfg=c.cfg;admin=json.loads((ROOT/'tool_runtime/qa-admin-session.json').read_text('utf-8'))['session'];headers={'Authorization':'Bearer '+admin}
 info=c.http.get(cfg['user_api']+'/station').json();start=time.perf_counter()
 response=c.http.post(cfg['admin_api']+'/jobs',json={'op':'profile','params':{'epsilon':1,'stage':'confirmation'},'account':None,'policy_version':info['policy_version'],'account_version':0},headers=headers);response.raise_for_status();id=response.json()['id']
 deadline=time.time()+900
 while time.time()<deadline:
  response=c.http.get(cfg['admin_api']+'/jobs/'+id,headers=headers);response.raise_for_status();job=response.json()
  if job['status']=='completed':break
  if job['status'] in {'unknown','failed'}:
   response=c.http.post(cfg['admin_api']+'/jobs/'+id+'/retry',json={},headers=headers);response.raise_for_status()
  time.sleep(.5)
 else:raise TimeoutError('profile unavailable')
 elapsed=time.perf_counter()-start;dashboard=c.http.get(cfg['admin_api']+'/dashboard',headers=headers).json();profile=list(dashboard['profiles'].values())[-1]
 from services.common.store import Store
 timings=[]
 for p in range(3):
  events=Store(str(ROOT/'tool_runtime'/f'party_{p}'/'state.sqlite')).events(500)
  timings.extend(e['data']['seconds'] for e in events if e['data'].get('nonce')==id)
 result={'passed':job.get('outcome') in {'published','cached'},'population':profile['population'],'seconds':elapsed,'node_seconds':timings,'epsilon_spent':dashboard['epsilon_spent'],'snapshot':profile['snapshot'],'full_18_rules':True,'registered_account_count':len(dashboard['accounts']),'outcome':job.get('outcome')}
 (ROOT/'reports/tool_acceptance/profile-performance.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');assert result['passed'];print(json.dumps(result,ensure_ascii=False),flush=True)
if __name__=='__main__':run()
