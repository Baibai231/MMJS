"""Durable serial administrator job runner. Never receives password shares."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from services.common import bootstrap
import argparse,asyncio,json,secrets,time
import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from privacy.transaction_contract import canonical
from services.common.security import load_private,public_hex,token,fingerprint_public,receipt_quorum
from services.common.store import Store
from services.common.contracts import Context

class Runner:
    def __init__(self,cfg):
        self.cfg=cfg;self.store=Store(cfg['store']);self.key=load_private(cfg['key'])

    def claim(self,id):
        with self.store.transaction() as conn:
            job=self.store.get('jobs',id,connection=conn)
            if not job or job['status'] not in {'queued','reconcile'}:return None
            job['status']='running';self.store.put('jobs',id,job,conn)
            return job

    async def execute(self,job,client):
        completed=self.store.get('consumed',job['id'])
        if completed:
            job.update(status='completed',outcome=completed['outcome'],completed=time.time())
            self.store.put('jobs',job['id'],job);return
        admin=self.store.get('administrators',job['actor'])
        if not admin or not admin['enabled'] or admin['role'] not in {'owner','operator'}:
            raise ValueError('发起人的管理权限已失效')
        session=token(self.key,'admin-session',{'site':self.cfg['site'],'sub':job['actor'],'role':admin['role']},1800)
        private=self.store.get('job_private',job['id'])
        if not private:
            signer=Ed25519PrivateKey.generate()
            context=Context(site=self.cfg['site'],nonce=job['id'],client_key=public_hex(signer),**{k:job[k] for k in ['op','account','policy_version','account_version','params']}).model_dump()
            response=await client.post(self.cfg['admin_api']+'/ticket',json={'context':context},headers={'Authorization':'Bearer '+session});response.raise_for_status()
            capability=response.json();envelopes=[]
            for _ in range(3):
                env={'context':context,'fields':{},'salt':secrets.token_hex(32),'ticket':capability['ticket']}
                envelopes.append({**env,'signature':signer.sign(canonical(env)).hex()})
            from cryptography.hazmat.primitives import serialization
            private={'context':context,'envelopes':envelopes,'query':capability['query'],
                     'signer':signer.private_bytes(serialization.Encoding.Raw,serialization.PrivateFormat.Raw,serialization.NoEncryption()).hex()}
            self.store.put('job_private',job['id'],private)
            responses=await asyncio.gather(*[client.post(url+'/submit',json=envelopes[i]) for i,url in enumerate(self.cfg['nodes'])])
            for response in responses:response.raise_for_status()
        elif private.get('signer'):
            # Re-upload the IDENTICAL randomized no-secret admin input with a
            # refreshed ticket. Admission still charges before replay/binding.
            signer=Ed25519PrivateKey.from_private_bytes(bytes.fromhex(private['signer']))
            response=await client.post(self.cfg['admin_api']+'/ticket',json={'context':private['context']},headers={'Authorization':'Bearer '+session});response.raise_for_status()
            capability=response.json()
            for i,url in enumerate(self.cfg['nodes']):
                env={k:v for k,v in private['envelopes'][i].items() if k!='signature'};env['ticket']=capability['ticket']
                env['signature']=signer.sign(canonical(env)).hex()
                response=await client.post(url+'/submit',json=env);response.raise_for_status()
        # Retrying an uncertain operation only queries its original nonce;
        # never invents a second application or a new budget reservation.
        query=token(self.key,'node-result',{'site':self.cfg['site'],'nonce':job['id']},1800)
        events=[]
        deadline=time.monotonic()+600
        while time.monotonic()<deadline:
            try:
                responses=await asyncio.gather(*[client.get(url+'/result/'+job['id'],headers={'Authorization':'Bearer '+query}) for url in self.cfg['nodes']])
            except httpx.TransportError:
                await asyncio.sleep(1);continue
            for response in responses:response.raise_for_status()
            data=[r.json() for r in responses]
            if all(r['status']=='completed' for r in data):events=[r['event'] for r in data];break
            await asyncio.sleep(.6)
        if not events:raise TimeoutError('计算结果未知；重试将核对原请求')
        context=private['context']
        receipt=receipt_quorum(events,self.cfg['node_keys'],{'nonce':job['id'],'site':self.cfg['site'],'context_digest':fingerprint_public(context)})
        response=await client.post(self.cfg['admin_api']+'/complete',json={'context':context,'events':events},headers={'Authorization':'Bearer '+session});response.raise_for_status()
        job.update(status='completed',outcome=receipt['outcome'],completed=time.time())
        self.store.put('jobs',job['id'],job)

    async def loop(self):
        for id,job in self.store.list('jobs').items():
            if job['status']=='running':job['status']='reconcile';self.store.put('jobs',id,job)
        async with httpx.AsyncClient(verify=self.cfg['ca'],timeout=httpx.Timeout(180,connect=8)) as client:
            while True:
                try:await self.reconcile(client)
                except Exception:pass  # A disconnected node blocks new common results.
                try:await self.schedule(client)
                except Exception:pass
                eligible=sorted([j for j in self.store.list('jobs').values() if j['status'] in {'queued','reconcile'}],key=lambda j:j['created'])
                if eligible:
                    job=self.claim(eligible[0]['id'])
                    if job is None:continue
                    try:await self.execute(job,client)
                    except Exception as exc:
                        job.update(status='unknown' if self.store.get('job_private',job['id']) else 'failed',error=type(exc).__name__+'：'+str(exc)[:160])
                        self.store.put('jobs',job['id'],job)
                await asyncio.sleep(.4)

    async def reconcile(self,client):
        if getattr(self,'last_reconcile',0)>time.time()-5:return
        self.last_reconcile=time.time()
        credential=token(self.key,'node-audit',{'site':self.cfg['site']},60)
        responses=await asyncio.gather(*[client.get(url+'/events',headers={'Authorization':'Bearer '+credential}) for url in self.cfg['nodes']])
        for response in responses:response.raise_for_status()
        maps=[{e['receipt']['nonce']:e for e in r.json()['events']} for r in responses]
        common=set(maps[0])&set(maps[1])&set(maps[2])
        for nonce in sorted(common,key=lambda n:maps[0][n]['receipt']['epoch']):
            if self.store.get('consumed',nonce):
                job=self.store.get('jobs',nonce)
                if job and job['status'] not in {'completed','cancelled'}:
                    job.update(status='completed',outcome=self.store.get('consumed',nonce)['outcome'],completed=time.time());self.store.put('jobs',nonce,job)
                continue
            issued=self.store.get('issued',nonce)
            if not issued:continue
            context=issued['context'];events=[mapping[nonce] for mapping in maps]
            receipt_quorum(events,self.cfg['node_keys'],{'site':self.cfg['site'],'nonce':nonce,'context_digest':fingerprint_public(context)})
            from services.common.contracts import USER_OPS
            api=self.cfg['user_api'] if context['op'] in USER_OPS else self.cfg['admin_api']
            admins=self.store.list('administrators')
            owners=[name for name,row in admins.items() if row['enabled'] and row['role']=='owner']
            headers={}
            if api==self.cfg['admin_api']:
                if not owners:continue
                headers={'Authorization':'Bearer '+token(self.key,'admin-session',{'site':self.cfg['site'],'sub':owners[0],'role':'owner'},60)}
            response=await client.post(api+'/complete',json={'context':context,'events':events},headers=headers);response.raise_for_status()

    async def schedule(self,client):
        if getattr(self,'last_schedule',0)>time.time()-30:return
        self.last_schedule=time.time()
        settings=self.store.get('settings','current',{})
        if settings.get('retention_days'):
            with self.store.transaction() as conn:
                self.store.execute(conn,'DELETE FROM journal WHERE created < ? AND sequence < (SELECT MAX(sequence) FROM journal)',(time.time()-int(settings['retention_days'])*86400,))
        state=self.store.get('site','state')
        automatic_review=settings.get('automatic_review',False)
        due_review=automatic_review and state.get('registrations_total',0)-self.store.get('schedule','reviewed_registrations',state.get('registrations_total',0))>=int(settings.get('registration_batch_size',50))
        if not due_review and (not settings.get('scheduled_release') or self.store.get('schedule','last',0)>time.time()-3600):return
        response=await client.get(self.cfg['nodes'][0]+'/health');response.raise_for_status();health=response.json()
        snapshot='population-'+str(health['data_epoch'])
        if due_review and snapshot in state['profiles']:
            from adapters.live_engines import registration_candidates
            candidates=registration_candidates(state['profiles'][snapshot],state['policy'],settings['k'],settings['j'],settings['cost_cap'],settings['pending_cap'])
            # Without an independently validated response model, no changed
            # candidate clears the automatic gate. Record a real HOLD decision.
            self.store.append('automatic_policy_review',{'snapshot':snapshot,'decision':'hold','reason':'没有独立用户响应与成本确认，保持当前策略','candidate_count':len(candidates),'registrations_total':state.get('registrations_total',0)})
            self.store.put('schedule','reviewed_registrations',state.get('registrations_total',0));return
        if state['epsilon_spent']+1>self.cfg['epsilon_cap']:return
        if not health['ready'] or snapshot in state['profiles']:return
        admins=self.store.list('administrators');owners=[name for name,row in admins.items() if row['enabled'] and row['role']=='owner']
        if not owners:return
        if any(j['status'] in {'queued','running','reconcile'} and j['op']=='profile' for j in self.store.list('jobs').values()):return
        id=secrets.token_hex(32)
        self.store.put('jobs',id,{'id':id,'op':'profile','params':{'epsilon':1},'account':None,'policy_version':state['policy_version'],'account_version':0,'actor':owners[0],'created':time.time(),'status':'queued','source':'approved hourly schedule'})
        self.store.put('schedule','last',time.time())

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--config',required=True);args=parser.parse_args()
    asyncio.run(Runner(json.loads(Path(args.config).read_text('utf-8'))).loop())
