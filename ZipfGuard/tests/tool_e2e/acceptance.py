"""Real services acceptance, synthetic secrets only; durable results per case."""
from client import Client,ROOT
import json,time,secrets,copy
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from services.common.security import public_hex

def run():
    client=Client();cfg=client.cfg
    admin=json.loads((ROOT/'tool_runtime/qa-admin-session.json').read_text('utf-8'))['session']
    results=[];output=ROOT/'reports/tool_acceptance';output.mkdir(exist_ok=True,parents=True)
    suffix=secrets.token_hex(3)
    def record(name,passed,**values):
        results.append({'case':name,'passed':bool(passed),**values});(output/'api-results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
        print(name,':','PASS' if passed else 'FAIL',flush=True)
        assert passed,name
    def call(name,op,expected='accepted',**kwargs):
        result=client.operation(op,**kwargs);record(name,result['outcome']==expected,outcome=result['outcome'],seconds=round(result['seconds'],3));return result
    a='full-'+suffix;old='Synthetic-Old-Password-39';new='Synthetic-New-Password-74';recovered='Synthetic-Recovered-Password-83'
    recovery=Ed25519PrivateKey.generate()
    registration=call('private_registration','register',account=a,passwords={'new':old},params={'recovery_key':public_hex(recovery)})
    user=registration['session']
    record('user_token_denied_admin',client.http.get(cfg['admin_api']+'/dashboard',headers={'Authorization':'Bearer '+user}).status_code==401)
    record('admin_token_denied_user',client.http.get(cfg['user_api']+'/me',headers={'Authorization':'Bearer '+admin}).status_code==401)
    record('unknown_password_field_denied_business',client.http.post(cfg['user_api']+'/ticket',json={'password':old}).status_code==422)
    record('no_other_account_private_route',client.http.get(cfg['user_api']+'/me/'+a,headers={'Authorization':'Bearer '+user}).status_code==404)
    call('wrong_private_login','login',expected='authentication_failed',account=a,passwords={'candidate':'Wrong-Synthetic-Password-02'})
    login=call('correct_private_login','login',account=a,passwords={'candidate':old})
    call('private_password_change','change',account=a,passwords={'old':old,'new':new})
    call('old_password_revoked','login',expected='authentication_failed',account=a,passwords={'candidate':old})
    call('new_password_login','login',account=a,passwords={'candidate':new})
    next_recovery=Ed25519PrivateKey.generate()
    call('recovery_signed_rotation','recover',account=a,passwords={'new':recovered},params={'recovery_key':public_hex(next_recovery)},recovery=recovery)
    call('old_recovery_revoked','recover',expected='recovery_failed',account=a,passwords={'new':new},params={'recovery_key':public_hex(Ed25519PrivateKey.generate())},recovery=recovery)
    latest=call('recovered_password_login','login',account=a,passwords={'candidate':recovered})
    record('stale_session_revoked',client.http.get(cfg['user_api']+'/me',headers={'Authorization':'Bearer '+user}).status_code==401)
    for i,url in enumerate(cfg['nodes']):
        response=client.http.post(url+'/submit',json=latest['envelopes'][i]);record('identical_nonce_replay_'+str(i),response.status_code==202)
        bad=copy.deepcopy(latest['envelopes'][i]);bad['fields']['candidate'][0]='0'
        record('changed_share_bad_signature_'+str(i),client.http.post(url+'/submit',json=bad).status_code==400)
    long='Aq7!'+''.join('abcdefghijkLMNOPQRST123456789!@#$%'[i%32] for i in range(60))
    call('64_character_registration','register',account='long-'+suffix,passwords={'new':long},params={'recovery_key':public_hex(Ed25519PrivateKey.generate())})
    call('64_character_login','login',account='long-'+suffix,passwords={'candidate':long})
    try:client.share(long+'x');invalid=False
    except ValueError:invalid=True
    record('65_character_no_truncation',invalid)
    # Job API drives the real serial runner, including profile publication.
    def job(name,op,params,account=None,timeout=600):
        station=client.http.get(cfg['user_api']+'/station').json()
        version=client.http.get(cfg['user_api']+'/account-version/'+account).json()['version'] if account else 0
        response=client.http.post(cfg['admin_api']+'/jobs',json={'op':op,'params':params,'account':account,'policy_version':station['policy_version'],'account_version':version},headers={'Authorization':'Bearer '+admin});response.raise_for_status()
        id=response.json()['id'];start=time.time()
        while time.time()-start<timeout:
            current=client.http.get(cfg['admin_api']+'/jobs/'+id,headers={'Authorization':'Bearer '+admin}).json()
            if current['status'] in {'completed','failed','unknown','cancelled'}:
                record(name,current['status']=='completed',outcome=current.get('outcome'),seconds=round(time.time()-start,3));return current
            time.sleep(.5)
        raise TimeoutError(name)
    base=client.http.get(cfg['user_api']+'/station').json()['policy']
    job('dynamic_base_policy','apply_policy',{'policy':{**base,'minimum':12,'fragments':[1]}})
    call('new_register_rejected_by_policy','register',expected='policy_rejected',account='reject-'+suffix,passwords={'new':'ab39Cd!x'},params={'recovery_key':public_hex(Ed25519PrivateKey.generate())})
    call('existing_login_not_locked_by_policy','login',account=a,passwords={'candidate':recovered})
    profile_job=job('private_profile_live_job','profile',{'epsilon':1},timeout=900)
    dashboard=client.http.get(cfg['admin_api']+'/dashboard',headers={'Authorization':'Bearer '+admin}).json()
    profile=list(dashboard['profiles'].values())[-1]
    record('profile_closed_output',len(profile['counts'])==9 and len(profile['spectra'])==9 and len(profile['fragment_violations'])==18 and 'words' not in profile)
    spent=dashboard['epsilon_spent'];job('profile_same_population_cached','profile',{'epsilon':1})
    dashboard=client.http.get(cfg['admin_api']+'/dashboard',headers={'Authorization':'Bearer '+admin}).json()
    record('cached_release_no_extra_epsilon',dashboard['epsilon_spent']==spent)
    response=client.http.post(cfg['admin_api']+'/preview/registration',json={'snapshot':profile['snapshot'],'k':3,'j':1},headers={'Authorization':'Bearer '+admin});response.raise_for_status()
    record('joint_candidates_keep_unconfirmed_base',all(c['eligible'] or c['decision']=='hold' for c in response.json()['candidates']))
    job('secret_task_eligibility_and_quota','assign_plan',{'snapshot':profile['snapshot'],'shapes':[],'positions':[],'quota':2,'total_cap':100,'fragments':[3]})
    dashboard=client.http.get(cfg['admin_api']+'/dashboard',headers={'Authorization':'Bearer '+admin}).json()
    record('no_task_false_completion',all(row['task']['status']=='pending' for row in dashboard['accounts'].values() if row.get('task')))
    disabled=job('administrator_disable','set_account',{'enabled':False},account=a)
    call('disabled_login_rejected','login',expected='account_disabled',account=a,passwords={'candidate':recovered})
    job('administrator_enable','set_account',{'enabled':True},account=a)
    challenge=client.http.post(cfg['reference_site']+'/challenge').json()['nonce']
    auth=call('site_challenge_private_login','login',account=a,passwords={'candidate':recovered},nonce=challenge)
    proof={'events':auth['events'],'context':auth['context'],'challenge':challenge}
    response=client.http.post(cfg['reference_site']+'/authenticate',json=proof)
    record('website_consumes_quorum_without_password',response.status_code==200)
    record('website_challenge_cannot_replay',client.http.post(cfg['reference_site']+'/authenticate',json=proof).status_code==403)
    export=client.http.get(cfg['admin_api']+'/export',headers={'Authorization':'Bearer '+admin}).text
    record('admin_export_no_secret_plaintext',all(secret not in export for secret in [old,new,recovered,long]))
    call('private_account_deletion','delete',account=a,passwords={'candidate':recovered})
    call('deleted_account_authentication_fails','login',expected='stale_or_missing_account',account=a,passwords={'candidate':recovered})
    print('Completed',len(results),'real-service assertions.',flush=True)

if __name__=='__main__':run()
