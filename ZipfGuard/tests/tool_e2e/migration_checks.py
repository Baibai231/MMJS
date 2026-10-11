from client import Client,ROOT
import json,secrets
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from services.common.security import load_private,public_hex
from sdk.zipfguard import migration_authorization
from services.common.contracts import Context

def run():
 c=Client();cfg=c.cfg;results=[];out=ROOT/'reports/tool_acceptance';suffix=secrets.token_hex(3)
 def record(name,passed,**details):
  results.append({'case':name,'passed':bool(passed),**details});(out/'migration-results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8');print(name,'PASS' if passed else 'FAIL',flush=True);assert passed,name
 site_key=load_private(ROOT/'tool_runtime/reference/migration.key');recovery=public_hex(Ed25519PrivateKey.generate())
 station=c.http.get(cfg['user_api']+'/station').json()
 context=Context(site=cfg['site'],op='register',account='legacy-user-001',nonce=secrets.token_hex(32),policy_version=station['policy_version'],client_key=public_hex(Ed25519PrivateKey.generate()),params={'recovery_key':recovery}).model_dump()
 record('reserved_legacy_registration_denied',c.http.post(cfg['user_api']+'/ticket',json={'context':context}).status_code==403)
 name='migrate-'+suffix
 wrong=migration_authorization(Ed25519PrivateKey.generate(),cfg['site'],name)
 result=c.operation('migrate',name,{'new':'Migrated-Secret-Password-53'},params={'recovery_key':recovery,'migration_ticket':wrong});record('wrong_migration_issuer_denied',result['outcome']=='migration_authorization_failed')
 eligibility=migration_authorization(site_key,cfg['site'],name)
 result=c.operation('migrate',name,{'new':'Migrated-Secret-Password-53'},params={'recovery_key':recovery,'migration_ticket':eligibility});record('authorized_migration_uses_same_secret',result['outcome']=='accepted',seconds=round(result['seconds'],3))
 original=result['session']
 result=c.operation('migrate',name,{'new':'Malicious-Overwrite-Password-7'},params={'recovery_key':recovery,'migration_ticket':eligibility});record('migration_cannot_overwrite_enrolled_account',result['outcome']=='account_exists')
 result=c.operation('login',name,{'candidate':'Migrated-Secret-Password-53'});record('migrated_private_login',result['outcome']=='accepted')
 result=c.operation('delete',name,{'candidate':'Migrated-Secret-Password-53'});record('migrated_private_deletion',result['outcome']=='accepted')
 result=c.operation('register',name,{'new':'Recreated-New-Password-73'},params={'recovery_key':public_hex(Ed25519PrivateKey.generate())});record('account_generation_never_resets',result['result']['account_version']>1)
 record('old_session_not_revived_on_recreation',c.http.get(cfg['user_api']+'/me',headers={'Authorization':'Bearer '+original}).status_code==401)
 print('Migration and account generation checks passed:',len(results),flush=True)
if __name__=='__main__':run()
