"""Prepare LOCAL synthetic accounts; execute real MPC plans, changes and releases.

Initialization is a trusted, offline, synthetic test fixture. It is NOT a
measurement of 1,000 HTTP registrations, nor a production import endpoint.
Only client-side private files hold generated fixture passwords. Website
metadata holds identifiers, versions, task status and approved noisy summaries.
"""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'tests/tool_e2e'))
from services.common import bootstrap
import argparse,collections,copy,hashlib,json,random,secrets,sqlite3,time
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from services.common.security import public_hex
from services.common.store import Store
from ops.tool import run as service_run,recorded_process_is_running
from client import Client

class DemoClient(Client):
    def __init__(self):
        import httpx
        self.cfg=json.loads((ROOT/'tool_runtime/installation.json').read_text('utf-8'))
        self.http=httpx.Client(verify=self.cfg['ca'],timeout=60,trust_env=False)
        self.prime=int(json.loads((ROOT/'tool_runtime/population-1000-client/public-field.json').read_text('utf-8'))['prime'])
        self.calls=[]

    def operation(self,op,account=None,passwords=None,params=None,session=None,timeout=7200,**kwargs):
        from privacy.transaction_contract import canonical
        from services.common.contracts import Context,USER_OPS
        from services.common.security import fingerprint_public,receipt_quorum
        from cryptography.hazmat.primitives import serialization
        import httpx
        checkpoint=PRIVATE/'pending-operation.json'
        pending=json.loads(checkpoint.read_text('utf-8')) if checkpoint.exists() else None
        if pending and (pending['context']['op']!=op or pending['context']['account']!=account):
            raise RuntimeError('Reconcile the saved original request before creating another operation.')
        api=self.cfg['user_api'] if op in USER_OPS else self.cfg['admin_api']
        headers={'Authorization':'Bearer '+session} if session else {}
        if pending is None:
            station=self.http.get(self.cfg['user_api']+'/station').json()
            version=self.http.get(self.cfg['user_api']+'/account-version/'+account).json()['version'] if account else 0
            key=Ed25519PrivateKey.generate()
            context=Context(site=self.cfg['site'],op=op,account=account,nonce=secrets.token_hex(32),
                            policy_version=station['policy_version'],account_version=version,
                            client_key=public_hex(key),params=params or {}).model_dump()
            now=time.time();self.calls=[at for at in self.calls if at>now-300]
            while len(self.calls)>=80:
                pause=max(.1,self.calls[0]+301-time.time())
                progress('waiting_normal_rate_limit',seconds=round(pause,1));time.sleep(min(pause,30))
                self.calls=[at for at in self.calls if at>time.time()-300]
            self.calls.append(time.time())
            response=self.http.post(api+'/ticket',json={'context':context},headers=headers);response.raise_for_status();ticket=response.json()
            fields={name:self.share(value) for name,value in (passwords or {}).items()}
            envelopes=[]
            for i in range(3):
                env={'context':context,'ticket':ticket['ticket'],'fields':{name:parts[i] for name,parts in fields.items()},'salt':secrets.token_hex(32)}
                env['signature']=key.sign(canonical(env)).hex();envelopes.append(env)
            pending={'context':context,'ticket':ticket,'envelopes':envelopes,'submitted':[],
                     'started':time.time(),'api':api}
            save(checkpoint,pending)
        context=pending['context'];ticket=pending['ticket']
        for i,url in enumerate(self.cfg['nodes']):
            if i not in pending['submitted']:
                response=self.http.post(url+'/submit',json=pending['envelopes'][i]);response.raise_for_status()
                pending['submitted'].append(i);save(checkpoint,pending)
        deadline=time.time()+timeout;events=[]
        while time.time()<deadline:
            try:
                responses=[self.http.get(url+'/result/'+context['nonce'],headers={'Authorization':'Bearer '+ticket['query']}) for url in self.cfg['nodes']]
                if any(r.status_code==401 for r in responses):
                    renewed=self.http.post(api+'/ticket',json={'context':context},headers=headers)
                    if renewed.status_code==401 and op not in USER_OPS:
                        session=admin_session(self);headers={'Authorization':'Bearer '+session}
                        renewed=self.http.post(api+'/ticket',json={'context':context},headers=headers)
                    renewed.raise_for_status()
                    pending['ticket']['query']=renewed.json()['query'];save(checkpoint,pending);continue
                for r in responses:r.raise_for_status()
                data=[r.json() for r in responses]
                if all(d.get('status')=='completed' for d in data):events=[d['event'] for d in data];break
            except httpx.TransportError:
                print('Waiting for original request; HTTP service busy.',flush=True)
            time.sleep(.5)
        if not events:raise TimeoutError('Original request still pending; resume from private checkpoint.')
        receipt=receipt_quorum(events,self.cfg['node_keys'],{'site':self.cfg['site'],'nonce':context['nonce'],'context_digest':fingerprint_public(context)})
        response=self.http.post(api+'/complete',json={'context':context,'events':events},headers=headers)
        if response.status_code==401 and op not in USER_OPS:
            session=admin_session(self);headers={'Authorization':'Bearer '+session}
            response=self.http.post(api+'/complete',json={'context':context,'events':events},headers=headers)
        response.raise_for_status()
        result={'receipt':receipt,'events':events,'context':context,'seconds':time.time()-pending['started'],**response.json()}
        checkpoint.unlink();return result

RUNTIME=ROOT/'tool_runtime'
OUT=ROOT/'reports/tool_population_1000_20261012'
PRIVATE=RUNTIME/'population-1000-client'
SEED=241012

def save(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')

def progress(stage,**data):
    save(OUT/'progress.json',{'stage':stage,'updated':time.time(),**data})
    print(stage,json.dumps(data,ensure_ascii=False),flush=True)

def split_values(values,prime):
    result=[[],[],[]]
    for value in values:
        slope=secrets.randbelow(prime)
        for i in range(3):result[i].append(str((value+(i+1)*slope)%prime))
    return result

def encode(word):return list(word.encode('ascii'))+[0]*(64-len(word))

def distribution(passwords):
    counts=collections.Counter(passwords);n=sum(counts.values())
    return {'population':n,'unique_passwords':len(counts),'collision_probability':sum((v/n)**2 for v in counts.values()),
            'top1_users':max(counts.values()),'top10_users':sum(sorted(counts.values(),reverse=True)[:10]),
            'below_15_users':sum(v for w,v in counts.items() if len(w)<15)}

def prepare(prime):
    OUT.mkdir(exist_ok=True);PRIVATE.mkdir(exist_ok=True)
    save(PRIVATE/'public-field.json',{'prime':str(prime)})
    if (PRIVATE/'client-state.json').exists():raise RuntimeError('Synthetic fixture already prepared; use run to continue.')
    cfg=json.loads((RUNTIME/'installation.json').read_text('utf-8'))
    if not cfg.get('local_demo'):raise RuntimeError('Only provisioned LOCAL demos may prepare synthetic accounts.')
    processes=json.loads((RUNTIME/'processes.json').read_text('utf-8')) if (RUNTIME/'processes.json').exists() else []
    if any(recorded_process_is_running(p,RUNTIME) for p in processes):raise RuntimeError('Stop local services before preparation.')
    business_cfg=json.loads((RUNTIME/'admin/config.json').read_text('utf-8'))
    business=Store(business_cfg['store']);state=business.get('site','state')
    if any(j['status'] in {'queued','running','reconcile'} for j in business.list('jobs').values()):raise RuntimeError('Finish pending management jobs first.')
    added=1000-len(state['accounts'])
    if added<100:raise RuntimeError('Expected the original small synthetic demonstration population.')
    backup=PRIVATE/'before-import';backup.mkdir()
    paths=[Path(business_cfg['store'])]+[RUNTIME/f'party_{i}'/'state.sqlite' for i in range(3)]
    for i,path in enumerate(paths):
        with sqlite3.connect(path) as source,sqlite3.connect(backup/f'database-{i}.sqlite') as target:source.backup(target)
    for i in range(3):
        ledger=RUNTIME/f'party_{i}'/'highwater.json'
        if ledger.exists():(backup/f'ledger-{i}.json').write_bytes(ledger.read_bytes())
    for rel in ['installation.json','user/config.json','admin/config.json','party_0/config.json','party_1/config.json','party_2/config.json']:
        file=RUNTIME/rel;configuration=json.loads(file.read_text('utf-8'))
        save(backup/rel,configuration)
        configuration['task_cap']=200;configuration['round_task_cap']=20
        if rel in {'user/config.json','admin/config.json'}:configuration['name']='ZipfGuard · 1000用户模拟站点'
        save(file,configuration)
    # Constructed popularity mix; deliberately includes short and concentrated
    # choices across the eight published structures and OTHER. No leaked corpus.
    mix=[('amberfox',90),('mosslake',80),('silkfern',60),('bluepond',50),
         ('silverfern',70),('cloudriver',50),('meadowlark',40),
         ('58271946',90),('73185624',70),('5827194630',50),('7318562490',40),
         ('cedar5827!',50),('mossy7318@',40),('Cedar582!',40),('Mossy731@',35),
         ('silverfern58',35),('cloudriver73',30),('Mossylake58!',25),('Amberfoxq73@',20)]
    words=[w for w,n in mix for _ in range(n)]
    longwords=['Cobalt-Harbor-57!','Violet-Meadow-82@']
    words += [longwords[i%2] for i in range(added-len(words))]
    if len(words)!=added or added<len(words):raise RuntimeError('Constructed population size mismatch.')
    rng=random.Random(SEED);rng.shuffle(words)
    ids=[f'000-sim-{i+1:04d}' for i in range(added)]
    credentials={a:{'password':w,'recovery_public':public_hex(Ed25519PrivateKey.generate())} for a,w in zip(ids,words)}
    grouped=list(collections.Counter(words).items())
    shared_accounts={a:split_values(encode(v['password']),prime) for a,v in credentials.items()}
    shared_groups=[(split_values(encode(w),prime),split_values([n],prime)) for w,n in grouped]
    for i in range(3):
        store=Store(RUNTIME/f'party_{i}'/'state.sqlite');local=store.get('state','current')
        original=[copy.deepcopy(row['shares']) for row in local['accounts'].values()]
        for a,v in credentials.items():
            if a in local['accounts']:raise RuntimeError('Synthetic identifier collision.')
            local['accounts'][a]={'version':1,'enabled':True,'recovery_key':v['recovery_public'],'shares':shared_accounts[a][i],'task':None}
            local.setdefault('account_generations',{})[a]=1
        local['epoch']+=1;local['data_epoch']=local.get('data_epoch',0)+1
        local['synthetic_compact']={'source':'local-synthetic-client-fixture-v1','data_epoch':local['data_epoch'],'normalized':False,
                                  'fixture_groups':len(grouped),'groups':[{'shares':parts[i],'weight':weight[i][0]} for parts,weight in shared_groups]+
                                  [{'shares':row,'weight':'1'} for row in original]}
        store.put('state','current',local)
        store.append('synthetic_fixture_initialization',{'population':1000,'added':added,'seed':SEED,'offline_prepared':True})
    for a in ids:state['accounts'][a]={'version':1,'enabled':True,'task':None,'synthetic':True}
    state['simulation']={'status':'prepared','population':1000,'added':added,'preserved':1000-added,'seed':SEED,
                         'initialization':'客户端离线预置合成历史账户；不计作1000次网络注册',
                         'response':'5轮，每轮20人；每轮16人实际完成三方改密，4人保留待处理',
                         'round_limit':20,'experiment_cap':100,'site_task_cap':200,'notified':0,'completed':0,'pending':0,
                         'rounds':[],
                         'statistics':'结构及匿名频次由真实三方秘密计算加噪发布；明文仅在模拟客户端生成'}
    business.put('site','state',state)
    business.append('synthetic_fixture_initialization',{'population':1000,'added':added,'seed':SEED,'offline_prepared':True})
    settings=business.get('settings','current',{})
    settings['scheduled_release']=False;settings['automatic_review']=False;business.put('settings','current',settings)
    client={'seed':SEED,'accounts':credentials,'initial_passwords':words,'baseline_fixture':distribution(words),'rounds':[],
            'source':'constructed synthetic historical accounts; generated in private client workspace'}
    save(PRIVATE/'client-state.json',client)
    progress('prepared',population=1000,added=added,preserved=1000-added)

def admin_session(c):
    from playwright.sync_api import sync_playwright
    from ops.open_ready_demo import browser_path
    saved=PRIVATE/'runner-passkey.json';identity='simulation-1000-runner'
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=str(browser_path()),headless=True,args=['--ignore-certificate-errors'])
        context=browser.new_context(ignore_https_errors=True);page=context.new_page()
        response_data={}
        def capture(response):
            if response.url==c.cfg['admin_api']+'/auth/verify' and response.status==200:response_data.update(response.json())
        page.on('response',capture);page.goto(c.cfg['admin_api'])
        cdp=context.new_cdp_session(page);cdp.send('WebAuthn.enable')
        auth=cdp.send('WebAuthn.addVirtualAuthenticator',{'options':{'protocol':'ctap2','transport':'internal','hasResidentKey':True,'hasUserVerification':True,'isUserVerified':True,'automaticPresenceSimulation':True}})['authenticatorId']
        if saved.exists():
            for credential in json.loads(saved.read_text('utf-8'))['credentials']:cdp.send('WebAuthn.addCredential',{'authenticatorId':auth,'credential':credential})
            page.locator('input').first.fill(identity);page.get_by_role('button',name='使用通行密钥登录',exact=True).click()
        else:
            invitation=secrets.token_urlsafe(32)
            config=json.loads((RUNTIME/'admin/config.json').read_text('utf-8'))
            Store(config['store']).put('invitations',invitation,{'identifier':identity,'role':'owner','expires':time.time()+120})
            page.get_by_role('button',name='首次安装 / 接受邀请',exact=True).click()
            page.locator('input').nth(0).fill(identity);page.locator('input').nth(1).fill(invitation)
            page.get_by_role('button',name='创建通行密钥',exact=True).click()
        page.get_by_role('heading',name='运行总览',exact=True).wait_for(timeout=30000)
        save(saved,{'credentials':cdp.send('WebAuthn.getCredentials',{'authenticatorId':auth})['credentials']})
        context.close();browser.close()
        return response_data['session']

def public_progress(client,stage,**extra):
    config=json.loads((RUNTIME/'admin/config.json').read_text('utf-8'));store=Store(config['store'])
    with store.transaction() as conn:
        state=store.get('site','state',connection=conn);s=state['simulation']
        s.update(status=stage,rounds=[{k:v for k,v in r.items() if k not in {'fixture_distribution'}} for r in client['rounds']],**extra)
        s['notified']=sum(row['assigned'] for row in client['rounds'])
        s['completed']=sum(row['completed'] for row in client['rounds'])
        s['pending']=s['notified']-s['completed']
        s['baseline_snapshot']=client.get('baseline_snapshot');s['final_snapshot']=client.get('final_snapshot')
        s['updated']=time.time();store.put('site','state',state,conn)

def experiment():
    private=json.loads((PRIVATE/'client-state.json').read_text('utf-8'));c=DemoClient()
    config=json.loads((RUNTIME/'admin/config.json').read_text('utf-8'));store=Store(config['store'])
    if store.get('site','state')['simulation']['status']=='completed':
        progress('already_completed');return
    session=admin_session(c)
    def administrative(op,params,timeout=5400):
        nonlocal session
        session=admin_session(c)
        result=c.operation(op,params=params,session=session,timeout=timeout)
        safe={'op':op,'outcome':result['outcome'],'seconds':result['seconds'],'nonce':result['context']['nonce']}
        save(OUT/(op+'-'+result['context']['nonce'][:10]+'.json'),safe)
        return result
    def publish(label):
        progress('publishing_'+label,population=1000)
        result=administrative('profile',{'epsilon':1},timeout=7200)
        if result['outcome'] not in {'published','cached'}:raise RuntimeError('Profile failed: '+result['outcome'])
        save(OUT/(label+'-profile.json'),result['result'])
        private[label+'_snapshot']=result['result']['snapshot'];save(PRIVATE/'client-state.json',private)
        return result['result']
    if 'baseline_snapshot' not in private:
        publish('baseline');public_progress(private,'running')
    state=store.get('site','state')
    if state['policy']['minimum']!=12 or state['policy']['fragments']!=[2]:
        from services.common.contracts import Policy
        result=administrative('apply_policy',{'policy':Policy(minimum=12,fragments=[2],label='模拟站点：新口令至少12字符').model_dump()})
        if result['outcome']!='policy_applied':raise RuntimeError('Policy did not apply.')
    rng=random.Random(SEED+1)
    for round_id in range(1,6):
        existing=next((r for r in private['rounds'] if r['round']==round_id),None)
        if existing and existing['completed']==16:continue
        progress('round_'+str(round_id)+'_assigning',notified=sum(r['assigned'] for r in private['rounds']),completed=sum(r['completed'] for r in private['rounds']))
        if existing is None:
            result=administrative('assign_plan',{'snapshot':private['baseline_snapshot'],'shapes':[],'positions':[],
                                    'quota':20,'total_cap':200,'fragments':[3]})
            if result['outcome']!='plan_assigned':raise RuntimeError('Assignment failed: '+result['outcome'])
            accounts=result['result']['assigned']
            if len(accounts)!=20 or any(a not in private['accounts'] for a in accounts):raise RuntimeError('Unexpected target population.')
            existing={'round':round_id,'assigned':20,'completed':0,'pending':20,'task':result['context']['nonce']}
            private['rounds'].append(existing)
            private.setdefault('assigned_accounts',{})[str(round_id)]=accounts
            save(PRIVATE/'client-state.json',private);public_progress(private,'running')
        accounts=private['assigned_accounts'][str(round_id)]
        for index,a in enumerate(accounts[:16]):
            record=private['accounts'][a]
            if record.get('changed'):continue
            progress('round_'+str(round_id)+'_changing',account_index=index+1,notified=sum(r['assigned'] for r in private['rounds']),completed=sum(r['completed'] for r in private['rounds']))
            login=c.operation('login',a,{'candidate':record['password']})
            if login['outcome']!='accepted':raise RuntimeError('Synthetic account login failed.')
            if index==0:
                rejected=c.operation('change',a,{'old':record['password'],'new':'N8!sT4@vK6#p'},session=login['session'])
                if rejected['outcome']!='policy_rejected':raise RuntimeError('Task rejection not enforced.')
                if store.get('site','state')['accounts'][a]['task']['status']!='pending':raise RuntimeError('Rejected change completed task.')
                existing['rejected_trial']=True
            # Deterministic unique synthetic choices; no claim of model ranking.
            digest=hashlib.sha256((str(SEED)+a).encode()).digest()
            alpha='abcdefghjkmnpqrstuvwxyzABCDEFGHJKMNPQRSTUVWXYZ23456789!@#$%'
            password='R7!'+''.join(alpha[value%len(alpha)] for value in digest[:13])
            record['proposed']=password;save(PRIVATE/'client-state.json',private)
            changed=c.operation('change',a,{'old':record['password'],'new':password},session=login['session'])
            if changed['outcome']!='accepted':raise RuntimeError('Synthetic change failed: '+changed['outcome'])
            record.update(password=password,changed=True);record.pop('proposed',None)
            existing['completed']+=1;existing['pending']=existing['assigned']-existing['completed']
            save(PRIVATE/'client-state.json',private);public_progress(private,'running')
        existing['fixture_distribution']=distribution([v['password'] for v in private['accounts'].values()])
        save(PRIVATE/'client-state.json',private);public_progress(private,'running')
        progress('round_'+str(round_id)+'_completed',notified=sum(r['assigned'] for r in private['rounds']),completed=sum(r['completed'] for r in private['rounds']),pending=sum(r['pending'] for r in private['rounds']))
    if 'final_snapshot' not in private:publish('final')
    current=store.get('site','state')
    simulated={a:row for a,row in current['accounts'].items() if a in private['accounts']}
    completed=sum(row.get('task',{}).get('status')=='completed' for row in simulated.values() if row.get('task'))
    pending=sum(row.get('task',{}).get('status')=='pending' for row in simulated.values() if row.get('task'))
    final=distribution([v['password'] for v in private['accounts'].values()])
    checks={'site_population_1000':len(current['accounts'])==1000,'new_notified_100':completed+pending==100,
            'new_completed_80':completed==80,'new_pending_20':pending==20,'rejection_keeps_pending':all(r['rejected_trial'] for r in private['rounds']),
            'profile_population_1000':current['profiles'][private['final_snapshot']]['population']==1000,
            'fixture_collision_reduced':final['collision_probability']<private['baseline_fixture']['collision_probability']}
    if not all(checks.values()):raise RuntimeError('Experiment consistency checks failed: '+json.dumps(checks))
    summary={'population':1000,'added':current['simulation']['added'],'preserved':current['simulation']['preserved'],'seed':SEED,
             'initialization':'trusted client-prepared synthetic historical account fixture; not 1000 registration requests',
             'notified':100,'completed':80,'pending':20,'rounds':private['rounds'],'baseline_fixture':private['baseline_fixture'],
             'final_fixture':final,'baseline_snapshot':private['baseline_snapshot'],'final_snapshot':private['final_snapshot'],'checks':checks,
             'claims':'functional synthetic workflow; no human-response estimate, attack superiority or production-capacity claim'}
    save(OUT/'result.json',summary)
    public_progress(private,'completed',checks={k:v for k,v in checks.items() if k!='fixture_collision_reduced'},baseline_snapshot=private['baseline_snapshot'],final_snapshot=private['final_snapshot'])
    store.append('synthetic_experiment_completed',{'population':1000,'notified':100,'completed':80,'pending':20,'checks_passed':True})
    progress('completed',population=1000,notified=100,completed=80,pending=20)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['prepare','run']);parser.add_argument('--prime',type=int);args=parser.parse_args()
    if args.action=='prepare':
        if args.prime is None:raise SystemExit('Public field prime required.')
        prepare(args.prime)
    else:experiment()
