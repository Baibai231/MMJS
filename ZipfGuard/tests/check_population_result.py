"""Audit completed synthetic run without exporting account secrets."""
from pathlib import Path
import sys,json,time,math
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from services.common import bootstrap
from services.common.store import Store
from services.common.security import fingerprint_public,receipt_quorum
from privacy.service.node import Node
OUT=ROOT/'reports/tool_population_1000_20261012'
while not (OUT/'result.json').exists():time.sleep(3)
result=json.loads((OUT/'result.json').read_text('utf-8'))
cfg=json.loads((ROOT/'tool_runtime/installation.json').read_text('utf-8'))
businesscfg=json.loads((ROOT/'tool_runtime/admin/config.json').read_text('utf-8'))
state=Store(businesscfg['store']).get('site','state')
profiles=[json.loads((OUT/(label+'-profile.json')).read_text('utf-8')) for label in ('baseline','final')]
checks={}
checks['both_profiles_1000_epsilon1']=all(p['population']==1000 and p['epsilon']==1 for p in profiles)
checks['profile_counts_conserve_population']=all(math.isclose(sum(p['counts']),1000,abs_tol=1e-6) and min(p['counts'])>=0 for p in profiles)
checks['anonymous_spectra_bounded_and_ordered']=all(all(sum(line)<=p['counts'][i]+1e-6 and all(a>=b>=0 for a,b in zip(line,line[1:])) for i,line in enumerate(p['spectra'])) for p in profiles)
checks['overall_tasks_102_82_20']=state['task_used']==102 and sum(bool(r.get('task')) and r['task']['status']=='completed' for r in state['accounts'].values())==82 and sum(bool(r.get('task')) and r['task']['status']=='pending' for r in state['accounts'].values())==20
checks['all_rounds_20_16_4']=all((r['assigned'],r['completed'],r['pending'])==(20,16,4) for r in result['rounds'])
checks['historical_epsilon_retained']=state['epsilon_spent']==5
fingerprints=[];events=[]
records=[json.loads(file.read_text('utf-8')) for file in OUT.glob('profile-*.json')]
finalop=next(record for record in records if Store(ROOT/'tool_runtime/party_0/state.sqlite').get('completed',record['nonce'])['event']['receipt']['result'].get('snapshot')==result['final_snapshot'])
for i in range(3):
    store=Store(ROOT/f'tool_runtime/party_{i}/state.sqlite');node=Node.__new__(Node);node.state=store.get('state','current')
    fingerprints.append(fingerprint_public(node.public_state()))
    events.append(store.get('completed',finalop['nonce'])['event'])
    checks[f'party_{i}_1000_accounts_budget5_tasks102']=len(node.state['accounts'])==1000 and store.get('budget','epsilon')==5 and node.state['task_used']==102
checks['three_common_states_agree']=len(set(fingerprints))==1
receipt=receipt_quorum(events,cfg['node_keys'],{'site':cfg['site'],'nonce':finalop['nonce'],'op':'profile'})
checks['final_snapshot_signed_by_three']=receipt['outcome']=='published' and receipt['result']['snapshot']==result['final_snapshot'] and receipt['result']['population']==1000
summary={'passed':all(checks.values()),'checks':checks,'scope':'public state agreement, signed final release, numeric consistency; no secret shares exported'}
(OUT/'consistency-check.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(summary,ensure_ascii=False))
assert summary['passed']
