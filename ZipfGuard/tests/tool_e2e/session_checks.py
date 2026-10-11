from client import Client,ROOT
import json
def run():
 c=Client();cfg=c.cfg;results=[]
 def record(name,passed):results.append({'case':name,'passed':bool(passed)});print(name,'PASS' if passed else 'FAIL',flush=True);assert passed,name
 result=c.operation('login','integration-smoke',{'candidate':'Updated-Smoke-Password-92'})
 record('fresh_login_mints_limited_session','session' in result)
 body={'context':result['context'],'events':result['events']}
 repeated=c.http.post(cfg['user_api']+'/complete',json=body).json();record('same_receipt_returns_same_session',repeated.get('session')==result['session'])
 response=c.http.post(cfg['user_api']+'/logout',json={},headers={'Authorization':'Bearer '+result['session']});record('session_logout',response.status_code==200)
 repeated=c.http.post(cfg['user_api']+'/complete',json=body).json();record('receipt_cannot_remint_session_after_logout','session' not in repeated and repeated.get('session_status')=='used_or_expired')
 record('logged_out_session_denied',c.http.get(cfg['user_api']+'/me',headers={'Authorization':'Bearer '+result['session']}).status_code==401)
 (ROOT/'reports/tool_acceptance/session-results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
if __name__=='__main__':run()
