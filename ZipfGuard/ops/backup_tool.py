"""Back up or restore ONE operator's store, with an external monotone ledger.

Never package the three operators' secret directories into one archive.
The external ledger must live on independent retained/WORM storage in production.
"""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from services.common import bootstrap
import argparse,json,sqlite3,shutil,time
from services.common.store import Store
from services.common.security import fingerprint_public

def read_ledger(store):
    state=store.get('state','current',{})
    return {'epoch':state.get('epoch',0),'epsilon':max(state.get('epsilon_spent',0),store.get('budget','epsilon',0)),
            'task_used':state.get('task_used',0),'ingress':store.list('ingress'),
            'decisions':store.list('decisions')}

def backup(config,target,anchor):
    cfg=json.loads(Path(config).read_text('utf-8'));folder=Path(config).resolve().parent
    source=cfg.get('store',str(folder/'state.sqlite'));store=Store(source)
    target=Path(target).resolve();target.mkdir(parents=True,exist_ok=False)
    if source.startswith(('postgres://','postgresql://')):
        raise ValueError('PostgreSQL: use operator-local pg_dump plus this budget ledger, as documented')
    with sqlite3.connect(source) as conn,sqlite3.connect(target/'state.sqlite') as dest:conn.backup(dest)
    # Anchor BEFORE recording a successful backup. It is external and survives
    # subsequent restore; its old version must never overwrite a newer one.
    ledger=read_ledger(store);previous=json.loads(Path(anchor).read_text('utf-8')) if Path(anchor).exists() else {}
    ledger['epsilon']=max(ledger['epsilon'],previous.get('epsilon',0));ledger['task_used']=max(ledger['task_used'],previous.get('task_used',0));ledger['epoch']=max(ledger['epoch'],previous.get('epoch',0))
    Path(anchor).parent.mkdir(parents=True,exist_ok=True);Path(anchor).write_text(json.dumps(ledger),encoding='utf-8')
    for name in ['receipt.key','binding.key','tls.crt','tls.key','config.json']:
        if (folder/name).is_file():shutil.copyfile(folder/name,target/name)
    if (folder/'.config').is_dir():shutil.copytree(folder/'.config',target/'.config')
    (target/'manifest.json').write_text(json.dumps({'operator':folder.name,'created':time.time(),'ledger':{k:v for k,v in ledger.items() if k!='ingress'},'single_party_only':True},indent=2),encoding='utf-8')
    print('Single-operator backup completed. Retain the separate monotone ledger.')

def restore(config,source,anchor):
    cfg=json.loads(Path(config).read_text('utf-8'));folder=Path(config).resolve().parent;dest=cfg.get('store',str(folder/'state.sqlite'))
    if dest.startswith(('postgres://','postgresql://')):raise ValueError('PostgreSQL restore requires operator-local transaction and ledger procedure')
    source=Path(source).resolve()
    if not Path(anchor).is_file():raise ValueError('external ledger missing: restore refused')
    ledger=json.loads(Path(anchor).read_text('utf-8'));backup_store=Store(str(source/'state.sqlite'));state=backup_store.get('state','current',{})
    # Old shares cannot reconstruct later passwords: do not invent a recovery.
    if state.get('epoch',0)<ledger['epoch']:raise ValueError('backup predates committed state; restore latest own shares instead')
    if Path(dest).exists():
        current=read_ledger(Store(dest))
        if state.get('epoch',0)<current['epoch']:raise ValueError('restore would roll back current credentials')
        ledger['epsilon']=max(ledger['epsilon'],current['epsilon']);ledger['task_used']=max(ledger['task_used'],current['task_used'])
    # Services MUST be stopped. SQLite backup API handles WAL without deleting
    # or moving computed filesystem targets.
    with sqlite3.connect(source/'state.sqlite') as original,sqlite3.connect(dest) as restored:original.backup(restored)
    store=Store(dest)
    state=store.get('state','current');state['epsilon_spent']=max(state['epsilon_spent'],ledger['epsilon']);state['task_used']=max(state['task_used'],ledger['task_used'])
    with store.transaction() as conn:
        store.put('state','current',state,conn);store.put('budget','epsilon',state['epsilon_spent'],conn)
        for key,times in ledger['ingress'].items():store.put('ingress',key,sorted(set(times+store.get('ingress',key,[],conn))),conn)
        for key,decision in ledger['decisions'].items():store.put('decisions',key,decision,conn)
    print('Restored own shares with high-water budgets. Restart all nodes to reconcile common decisions.')

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['backup','restore']);parser.add_argument('--config',required=True);parser.add_argument('--path',required=True);parser.add_argument('--ledger',required=True)
    args=parser.parse_args();(backup if args.action=='backup' else restore)(args.config,args.path,args.ledger)
