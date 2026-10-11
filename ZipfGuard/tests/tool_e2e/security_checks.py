from client import Client,ROOT
import json,secrets,time,sqlite3
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from services.common.security import public_hex
from services.common.contracts import Context
from privacy.transaction_contract import canonical,private_envelope_binding
from services.common.store import Store
from ops.backup_tool import backup,restore

def run():
 c=Client();cfg=c.cfg;suffix=secrets.token_hex(3);results=[];out=ROOT/'reports/tool_acceptance'
 def record(name,passed,**details):
  results.append({'case':name,'passed':bool(passed),**details});(out/'security-results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8');print(name,'PASS' if passed else 'FAIL',flush=True);assert passed,name
 recovery=public_hex(Ed25519PrivateKey.generate())
 def inconsistent(fields):fields['new'][2][0]=str((int(fields['new'][2][0])+1)%c.prime)
 r=c.operation('register','badpoly-'+suffix,{'new':'Malformed-Synthetic-Password-7'},params={'recovery_key':recovery},mutate_shares=inconsistent)
 record('signed_inconsistent_shares_rejected',r['outcome']=='invalid_operation')
 def nonbinary(fields):
  for part in fields['new']:part[0]=str((int(part[0])+1)%c.prime)
 r=c.operation('register','badbits-'+suffix,{'new':'Malformed-Synthetic-Password-7'},params={'recovery_key':recovery},mutate_shares=nonbinary)
 record('signed_nonbinary_shares_rejected',r['outcome']=='invalid_operation')
 info=c.http.get(cfg['user_api']+'/station').json();key=Ed25519PrivateKey.generate();account='rate-'+suffix
 context=Context(site=cfg['site'],op='login',account=account,nonce=secrets.token_hex(32),policy_version=info['policy_version'],account_version=0,client_key=public_hex(key)).model_dump()
 wrong={**context,'site':'other-site'};record('cross_site_ticket_denied',c.http.post(cfg['user_api']+'/ticket',json={'context':wrong}).status_code==403)
 statuses=[];first=None
 for index in range(31):
  context['nonce']=secrets.token_hex(32);ticket=c.http.post(cfg['user_api']+'/ticket',json={'context':context});ticket.raise_for_status();value=ticket.json()
  env={'context':dict(context),'ticket':value['ticket'],'fields':{'candidate':c.share('Never-Compare-Password-9')[0]},'salt':secrets.token_hex(32)};env['signature']=key.sign(canonical(env)).hex()
  response=c.http.post(cfg['nodes'][0]+'/submit',json=env);statuses.append(response.status_code)
  if index==0:first=env
 record('account_rate_limit_before_binding',statuses[:30]==[202]*30 and statuses[30]==429)
 store=Store(str(ROOT/'tool_runtime/party_0/state.sqlite'))
 record('denied_request_not_bound',not store.get('pending',context['nonce']))
 record('rate_counter_persistent',len(store.get('ingress','account:'+account))==30)
 record('rate_limit_in_retained_ledger',len(json.loads((ROOT/'tool_runtime/party_0/highwater.json').read_text('utf-8'))['ingress']['account:'+account])==30)
 # Backup/restore of a standalone operator fixture proves high-water merging.
 # It never touches live node secrets or consolidates the three real nodes.
 fixture=ROOT/'tool_runtime'/('backup-check-'+suffix);fixture.mkdir();local=Store(str(fixture/'state.sqlite'))
 state={'epoch':4,'epsilon_spent':1,'task_used':1};local.put('state','current',state);local.put('budget','epsilon',1);local.charge('account:test',30)
 config=fixture/'config.json';config.write_text(json.dumps({'store':str(fixture/'state.sqlite')}),encoding='utf-8');anchor=fixture/'external-highwater.json';archive=fixture/'one-operator-backup'
 backup(config,archive,anchor)
 # A failed future release keeps the same epoch but charges privacy budget.
 current=json.loads(anchor.read_text('utf-8'));current['epsilon']=2;anchor.write_text(json.dumps(current),encoding='utf-8')
 restore(config,archive,anchor);record('restore_does_not_refund_epsilon',local.get('state','current')['epsilon_spent']==2)
 local.put('state','current',{'epoch':5,'epsilon_spent':2,'task_used':2})
 try:restore(config,archive,anchor);refused=False
 except ValueError:refused=True
 record('old_credentials_backup_refused',refused)
 # Scan only the operators' actual DBs, ledgers and logs for synthetic strings.
 # Client fixtures legitimately hold synthetic secrets and are excluded.
 needles=[b'Unique-Sample-Password-37',b'Completed-Task-Password-56',b'Crash-Recovery-Password-48',b'Unconsumed-Commit-Password-52',b'Malformed-Synthetic-Password-7']
 files=[];hits=[]
 for i in range(3):
  folder=ROOT/'tool_runtime'/f'party_{i}'
  for name in ['state.sqlite','state.sqlite-wal','highwater.json']:
   path=folder/name
   if path.is_file():files.append(path)
  files.append(ROOT/'tool_runtime'/f'party_{i}.log')
 for name in ['business.sqlite','business.sqlite-wal','reference.sqlite','user.log','admin.log','jobs.log','reference.log']:
  path=ROOT/'tool_runtime'/name
  if path.is_file():files.append(path)
 for path in files:
  if any(needle in path.read_bytes() for needle in needles):hits.append(str(path.relative_to(ROOT)))
 record('runtime_plaintext_scan',not hits,files=len(files),hits=hits)
 print('Security checks passed:',len(results),flush=True)
if __name__=='__main__':run()
