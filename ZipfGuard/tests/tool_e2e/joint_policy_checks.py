from client import Client,ROOT
import json,time
from services.common.contracts import Policy
def run():
 c=Client();cfg=c.cfg;admin=json.loads((ROOT/'tool_runtime/qa-admin-session.json').read_text('utf-8'))['session'];headers={'Authorization':'Bearer '+admin};results=[];out=ROOT/'reports/tool_acceptance'
 def record(name,passed,**details):
  results.append({'case':name,'passed':bool(passed),**details});(out/'joint-policy-results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8');print(name,'PASS' if passed else 'FAIL',flush=True);assert passed,name
 dashboard=c.http.get(cfg['admin_api']+'/dashboard',headers=headers).json();profiles=dashboard['profiles'];snapshot='population-14-confirmation'
 record('independent_confirmation_present',snapshot in profiles and profiles[snapshot]['stage']=='confirmation')
 # The synthetic source controller knows these fixture strings. Nodes and
 # administrators still see only anonymous (OTHER, rank1), never its label.
 policy=Policy(label='验收手动联合阻断',snapshot=snapshot,positions=[[8,1]],shapes=[0],k=1,j=1).model_dump()
 result=c.operation('apply_policy',params={'policy':policy},session=admin);record('frozen_joint_policy_applied',result['outcome']=='policy_applied')
 result=c.operation('check',passwords={'new':'Unique-Sample-Password-37'});record('secret_complete_password_blocked',result['outcome']=='policy_rejected')
 result=c.operation('check',passwords={'new':'abcdefgh'});record('secret_derived_structure_blocked',result['outcome']=='policy_rejected')
 result=c.operation('check',passwords={'new':'New-Unblocked-Choice-64'});record('unmatched_candidate_accepted',result['outcome']=='accepted')
 result=c.operation('login','integration-smoke',{'candidate':'Unique-Sample-Password-37'});record('hidden_policy_not_applied_to_existing_login',result['outcome']=='accepted')
 # An anonymous-hotspot remediation really completes only when the same old
 # secret is authenticated and a different acceptable secret is committed.
 used=c.http.get(cfg['admin_api']+'/dashboard',headers=headers).json()['task_used']
 result=c.operation('assign_plan',params={'snapshot':snapshot,'shapes':[],'positions':[[8,1]],'fragments':[],'quota':1,'total_cap':used+1},session=admin)
 record('anonymous_hotspot_task_selection',result['outcome']=='plan_assigned' and result['result']['assigned']==['integration-smoke'])
 result=c.operation('change','integration-smoke',{'old':'Unique-Sample-Password-37','new':'Updated-Smoke-Password-92'});record('hotspot_task_changed_privately',result['outcome']=='accepted')
 me=c.http.get(cfg['user_api']+'/me',headers={'Authorization':'Bearer '+result['session']}).json();record('hotspot_task_completed',me['task']['status']=='completed')
 result=c.operation('apply_policy',params={'policy':Policy().model_dump()},session=admin);record('restore_public_base_after_joint_test',result['outcome']=='policy_applied')
 # Independent confirmation re-query is cached and must not resample noise.
 before=c.http.get(cfg['admin_api']+'/dashboard',headers=headers).json()['epsilon_spent']
 record('joint_checks_do_not_spend_release_budget',before==3)
 print('Joint hidden policy checks passed:',len(results),flush=True)
if __name__=='__main__':run()
