"""One long-lived, independent computation party and direct HTTP ingress.

Run from the party's own directory with MPyC -M3 -I<id> -T1 --ssl.
Only keyed envelope bindings and public commands traverse the control plane.
"""
from __future__ import annotations
import asyncio
import copy
import json
from pathlib import Path
import sys
import time
import os

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from services.common import bootstrap
from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
from mpyc.runtime import mpc
from privacy.service.compute import Computer, WIDTH, SHAPES, SHAPE_VERSION
from privacy.transaction_contract import canonical, private_envelope_binding, randomized_request_digest
from services.common.contracts import Context, Envelope, Policy, FIELDS, USER_OPS, ADMIN_OPS
from services.common.security import load_private, verify_token, verify_client_signature, fingerprint_public, receipt_quorum
from services.common.store import Store


class Node:
    def __init__(self, cfg, directory):
        self.cfg=cfg; self.directory=Path(directory); self.pid=mpc.pid
        self.store=Store(cfg.get('store',str(self.directory/'state.sqlite')))
        self.key=load_private(self.directory/'receipt.key')
        self.binding=(self.directory/'binding.key').read_bytes()
        self.computer=Computer(mpc,json.loads((ROOT/'contracts/public_reference.json').read_text('utf-8')))
        self.active=False; self.failed=None
        self.state=self.store.get('state','current',{
            'epoch':0,'data_epoch':0,'policy_version':1,'policy':Policy().model_dump(),'policies':{},'accounts':{},
            'snapshots':{},'epsilon_spent':0,'task_used':0,'plans':{},'releases':{},'account_generations':{}})
        self.ledger_path=Path(cfg.get('ledger_path',str(self.directory/'highwater.json')))
        self.app=FastAPI(title=f'ZipfGuard independent party {self.pid}',docs_url=None,redoc_url=None)
        self.app.add_middleware(CORSMiddleware,allow_origins=cfg['allowed_origins'],allow_methods=['GET','POST'],allow_headers=['Content-Type','Authorization'])
        self.routes()

    def public_state(self):
        return {k:v for k,v in self.state.items() if k not in {'accounts','snapshots','synthetic_compact'}} | {
            'accounts':{a:{k:v for k,v in row.items() if k!='shares'} for a,row in self.state['accounts'].items()},
            'snapshots':list(self.state['snapshots'])}

    def connected(self):
        return self.active and not self.failed and all(p.protocol is not None and not p.protocol.transport.is_closing() for p in mpc.parties if p.pid!=self.pid)

    def local_fault(self,phase,nonce):
        # Filesystem-only deterministic crash injection for acceptance. Never
        # exposed as an API and disabled in independent production configuration.
        path=self.directory/'local-test-fault.json'
        if not self.cfg.get('local_demo') or not path.is_file():return
        fault=json.loads(path.read_text('utf-8'))
        if fault.get('phase')==phase and fault.get('nonce')==nonce:
            path.rename(self.directory/('used-fault-'+nonce+'.json'))
            os._exit(73)

    def ledger(self,restore=False):
        previous=json.loads(self.ledger_path.read_text('utf-8')) if self.ledger_path.is_file() else {}
        if restore:
            with self.store.transaction() as conn:
                self.store.put('budget','epsilon',max(previous.get('epsilon',0),self.store.get('budget','epsilon',0,conn)),conn)
                for key,times in previous.get('ingress',{}).items():
                    self.store.put('ingress',key,sorted(set(times+self.store.get('ingress',key,[],conn))),conn)
                for nonce,decision in previous.get('decisions',{}).items():self.store.put('decisions',nonce,decision,conn)
            self.state['task_used']=max(self.state['task_used'],previous.get('task_used',0))
        value={'epoch':max(previous.get('epoch',0),self.state['epoch']),
               'epsilon':max(previous.get('epsilon',0),self.state['epsilon_spent'],self.store.get('budget','epsilon',0)),
               'task_used':max(previous.get('task_used',0),self.state['task_used']),
               'ingress':self.store.list('ingress'),'decisions':self.store.list('decisions')}
        if value['decisions']:value['epoch']=max(value['epoch'],max(d['epoch'] for d in value['decisions'].values()))
        self.ledger_path.parent.mkdir(parents=True,exist_ok=True)
        temporary=self.ledger_path.with_suffix('.pending')
        with temporary.open('w',encoding='utf-8') as output:
            json.dump(value,output,separators=(',',':'));output.flush();os.fsync(output.fileno())
        os.replace(temporary,self.ledger_path)

    def charge(self,key,limit,window=300):
        allowed=self.store.charge(key,limit,window)
        if allowed:self.ledger()  # Durable retained record BEFORE input binding.
        return allowed

    def routes(self):
        app=self.app
        @app.get('/health')
        def health():
            return {'party':self.pid,'ready':self.connected(),'epoch':self.state['epoch'],
                    'data_epoch':self.state.get('data_epoch',0),
                    'epsilon_spent':max(self.state['epsilon_spent'],self.store.get('budget','epsilon',0)),'task_used':self.state['task_used'],
                    'protocol':'zipfguard-tool-v1','field_prime':str(self.computer.sec.field.order),'maximum':WIDTH}

        @app.post('/submit',status_code=202)
        async def submit(request:Request):
            # Every attempt, including malformed requests, gets a persistent
            # source quota before parsing. Authenticated attempts then consume
            # site/account quota BEFORE replay lookup or keyed share binding.
            ip=request.client.host if request.client else 'unknown'
            if not self.charge('source:'+ip,self.cfg['source_limit'],300):
                raise HTTPException(429,'入口请求过多，请稍后再试')
            try:
                body=await request.body()
                if len(body)>180000: raise ValueError('request too large')
                env=Envelope.model_validate_json(body)
                context=env.context.model_dump()
                issuer=self.cfg['user_issuer'] if context['op'] in USER_OPS else self.cfg['admin_issuer']
                claims=verify_token(env.ticket,issuer,'node-operation')
                if claims.get('context')!=fingerprint_public(context) or context['site']!=self.cfg['site']:
                    raise ValueError('wrong context or site')
                verify_client_signature(context['client_key'],env.signature,{'context':context,'fields':env.fields,'salt':env.salt,'ticket':env.ticket})
                if not self.charge('site:'+context['site'],self.cfg['site_limit'],300): raise HTTPException(429,'站点计算额度已用完')
                if not self.charge('account:'+str(context['account']),self.cfg['account_limit'],300): raise HTTPException(429,'本账户查询额度已用完')
                if context['op'] in ADMIN_OPS and claims.get('role') not in {'owner','operator'}:
                    raise ValueError('role denied')
                fields=FIELDS.get(context['op'],set())
                if set(env.fields)!=fields: raise ValueError('unexpected secret fields')
                prime=self.computer.sec.field.order
                for values in env.fields.values():
                    if len(values)!=WIDTH*8 or any(not v.isdecimal() or len(v)>25 or not 0<=int(v)<prime for v in values):
                        raise ValueError('invalid share format')
                if not self.connected(): raise HTTPException(503,'计算服务尚未就绪或有计算方断线')
                nonce=context['nonce']
                # Capability renewal must not change the input identity. Bind the
                # randomized secret envelope and public context, excluding its
                # expiring transport ticket/signature (both still verified first).
                binding=private_envelope_binding(self.binding,{'context':context,'fields':env.fields,'salt':env.salt})
                old=self.store.get('completed',nonce)
                if old:
                    if old['binding']!=binding: raise HTTPException(409,'请求编号冲突')
                    return {'nonce':nonce,'status':'completed'}
                pending=self.store.get('pending',nonce)
                if pending and pending['binding']!=binding: raise HTTPException(409,'请求编号冲突')
                self.store.put('pending',nonce,{'context':context,'fields':env.fields,'binding':binding,'created':time.time()})
                return {'nonce':nonce,'status':'queued'}
            except HTTPException: raise
            except Exception: raise HTTPException(400,'无效的签名、权限或请求格式')

        @app.get('/result/{nonce}')
        def result(nonce:str,request:Request):
            try:
                claims=verify_token(request.headers.get('authorization','').removeprefix('Bearer '),self.cfg['user_issuer'],'node-result')
            except ValueError:
                try: claims=verify_token(request.headers.get('authorization','').removeprefix('Bearer '),self.cfg['admin_issuer'],'node-result')
                except ValueError: raise HTTPException(401,'需要有效查询凭据')
            if claims.get('nonce')!=nonce: raise HTTPException(403,'请求不属于此凭据')
            row=self.store.get('completed',nonce)
            if row: return {'status':'completed','event':row['event']}
            return {'status':'running' if self.store.get('running',nonce) else 'queued'}

        @app.get('/recovery')
        def recovery(request:Request):
            try:
                claims=verify_token(request.headers.get('authorization','').removeprefix('Bearer '),self.cfg['node_keys'][int(request.headers.get('x-party','-1'))],'node-recovery')
                if claims['site']!=self.cfg['site']: raise ValueError()
            except Exception: raise HTTPException(403,'节点身份无效')
            # Common signed decisions contain public outcomes, never local shares.
            return {'decisions':self.store.list('decisions')}

        @app.get('/events')
        def events(request:Request):
            value=request.headers.get('authorization','').removeprefix('Bearer ')
            try:claims=verify_token(value,self.cfg['admin_issuer'],'node-audit')
            except ValueError:
                try:claims=verify_token(value,self.cfg['user_issuer'],'node-audit')
                except ValueError:raise HTTPException(403,'业务服务身份无效')
            if claims.get('site')!=self.cfg['site']:raise HTTPException(403,'站点不匹配')
            return {'events':[row['event'] for row in self.store.list('completed').values()]}

    async def load_bank(self,policy):
        snapshot=self.state['snapshots'].get(policy.get('snapshot'))
        if not snapshot:
            if policy['positions'] or policy['shapes'] or 17 in policy['fragments']: raise ValueError('snapshot missing')
            return []
        bank=[]
        positions=policy['positions']
        if 17 in policy['fragments'] and not positions:
            positions=[[s,r] for s in range(len(SHAPES)+1) for r in range(3)]
        for s,r in positions:
            if not 0<=s<=len(SHAPES) or not 0<=r<3: raise ValueError('invalid rank')
            entry=snapshot['banks'][s][r]
            bank.append((self.computer.load_chars(entry['chars']),self.computer.load_chars([entry['active']])[0]))
        return bank


    def load_demo_groups(self, state):
        compact=state.get('synthetic_compact')
        if not self.cfg.get('local_demo') or not compact:return None
        if compact['data_epoch']!=state.get('data_epoch',0):
            raise ValueError('synthetic histogram epoch mismatch')
        c=self.computer
        return [(c.load_chars(item['shares']),c.load_chars([item['weight']])[0])
                for item in compact['groups']]

    async def save_demo_groups(self, state, groups):
        c=self.computer
        state['synthetic_compact']['groups']=[{'shares':await c.save_chars(row),
                                              'weight':(await c.save_chars([weight]))[0]}
                                             for row,weight in groups]
        state['synthetic_compact']['data_epoch']=state.get('data_epoch',0)

    async def execute(self,context,fields,digest):
        c=self.computer; op=context['op']; a=context['account']; params=context['params']
        old=self.state; state=copy.deepcopy(old); accounts=state['accounts']; row=accounts.get(a)
        outcome='rejected'; public={}; changed=False
        if op in {'check','register','migrate','change','recover','apply_policy','assign_plan'} and context['policy_version']!=state['policy_version']:
            return state,'stale_policy',{},False
        if op in {'login','change','recover','delete','set_account'} and (not row or context['account_version']!=row['version']):
            return state,'stale_or_missing_account',{},False
        if op in USER_OPS:
            if op=='register' and a in self.cfg.get('reserved_legacy_accounts',[]):return state,'migration_authorization_required',{},False
            if op=='migrate':
                try:
                    eligibility=verify_token(params['migration_ticket'],self.cfg['migration_issuer'],'user-migration')
                    if eligibility.get('site')!=context['site'] or eligibility.get('sub')!=a:raise ValueError()
                except Exception:return state,'migration_authorization_failed',{},False
                if row:return state,'account_exists',{},False
            secret={name:await c.input(values) for name,values in sorted(fields.items())}
            if row and not row['enabled']: return state,'account_disabled',{},False
            if op in {'login','change','delete'}:
                current=c.load_chars(row['shares'])
                match=bool(await mpc.output(c.equal(secret['old' if op=='change' else 'candidate'],current)))
                if not match: return state,'authentication_failed',{},False
            if op=='register' and row: return state,'account_exists',{},False
            if op=='recover':
                try:
                    unsigned={**context,'params':{k:v for k,v in params.items() if k!='recovery_signature'}}
                    verify_client_signature(row['recovery_key'],params['recovery_signature'],unsigned)
                except Exception: return state,'recovery_failed',{},False
            if op in {'check','register','migrate','change','recover'}:
                policy=copy.deepcopy(state['policy'])
                bank=await self.load_bank(policy)
                acceptance=c.accept(secret['new'],policy,bank)
                if row and row.get('task') and row['task']['status']=='pending':
                    task_policy=row['task']['policy']
                    acceptance*=c.accept(secret['new'],task_policy,await self.load_bank(task_policy))
                accepted=bool(await mpc.output(acceptance))
                if not accepted: return state,'policy_rejected',{},False
            outcome='accepted'
            if op=='check': return state,outcome,{},False
            if op=='login': return state,outcome,{'account_version':row['version']},False
            if op=='delete':
                state.setdefault('account_generations',{})[a]=row['version']+1
                del accounts[a]; changed=True
            else:
                generations=state.setdefault('account_generations',{})
                version=max(generations.get(a,0),row['version'] if row else 0)+1
                generations[a]=version
                task=copy.deepcopy(row.get('task')) if row else None
                if task and task['status']=='pending': task['status']='completed'; task['completed_at']=context['nonce']
                recovery=params.get('recovery_key',row['recovery_key'] if row else None)
                if op=='recover' and recovery==row['recovery_key']: return state,'recovery_rotation_required',{},False
                accounts[a]={'version':version,'enabled':True,'recovery_key':recovery,'shares':await c.save_chars(secret['new']),'task':task}
                public={'account_version':version}; changed=True
        elif op=='profile':
            epsilon=float(params.get('epsilon',1))
            if not .1<=epsilon<=5: raise ValueError('epsilon outside allowed range')
            stage=params.get('stage','analysis')
            snapshot=f"population-{state.get('data_epoch',0)}"+('-confirmation' if stage=='confirmation' else '')
            if snapshot in state['releases']: return state,'cached',state['releases'][snapshot],False
            if state['epsilon_spent']+epsilon>self.cfg['epsilon_cap']+1e-10: return state,'privacy_budget_exhausted',{},False
            # Persistent reservation before any noise generation. Failed publication
            # remains charged and cannot be resampled for free after restart.
            with self.store.transaction() as conn:
                used=self.store.get('budget','epsilon',state['epsilon_spent'],conn)
                if used+epsilon>self.cfg['epsilon_cap']+1e-10: return state,'privacy_budget_exhausted',{},False
                self.store.put('budget','epsilon',used+epsilon,conn)
            self.ledger()
            state['epsilon_spent']=used+epsilon
            groups=self.load_demo_groups(state)
            if groups is not None and not state['synthetic_compact'].get('normalized'):
                count=state['synthetic_compact']['fixture_groups']
                initial=groups[:count]
                for chars,weight in groups[count:]:
                    initial=await c.compact_add(initial,chars,1)
                groups=initial
                state['synthetic_compact']['normalized']=True
                await self.save_demo_groups(state,groups)
            rows=[c.load_chars(accounts[key]['shares']) for key in sorted(accounts)] if groups is None else [r for r,w in groups]
            if groups is None:
                vector,banks=await c.snapshot(rows)
                weights=[c.sec(1)]*len(rows)
                metas=None
            else:
                vector,banks,metas=await c.compact_snapshot(groups)
                weights=[w for r,w in groups]
                if not await mpc.output(mpc.sum(weights)==len(accounts)):
                    raise ValueError('synthetic population mismatch')
            await mpc.gather(vector)  # Local shares only; keep HTTP ingress responsive.
            # 4-sensitivity counts+conditional spectra use 80% budget.
            noisy=await c.noisy(vector,epsilon*.8,4)
            # 17 fixed fragment counts have sensitivity 1 each. The hot count
            # is a fixed-prefix spectrum sum, sensitivity <=2. Total <=19.
            # use remaining 20%; extra coordinates never reuse sensitivity 4.
            if metas is None:metas=[c.characterize(r,include_shape=False) for r in rows]
            hot=[entry for group in banks for entry in group]
            violations=[]
            for n in range(1,19):
                if n==17:
                    violation=mpc.sum(vector[len(SHAPES)+1:])
                else:
                    parts=[]
                    for i,(r,meta,weight) in enumerate(zip(rows,metas,weights)):
                        parts.append(weight*(1-c.fragment(n,r,meta,hot)))
                        if i%4==3:await mpc.gather(parts[-4:])
                    violation=mpc.sum(parts)
                violations.append(violation)
                await mpc.gather(violation)  # Nothing is opened or returned here.
            noisy_rules=await c.noisy(violations,epsilon*.2,19)
            from policy.privacy_joint_profile import simplex_projection,decreasing_projection
            dimension=len(SHAPES)+1
            counts=simplex_projection(noisy[:dimension],len(accounts)).tolist()
            spectra=[]
            for s in range(dimension):
                line=decreasing_projection(noisy[dimension+3*s:dimension+3*s+3])
                if line.sum()>counts[s] and line.sum()>0: line*=counts[s]/line.sum()
                spectra.append(line.tolist())
            saved=[]
            for group in banks:
                saved.append([{'chars':await c.save_chars(chars),'active':(await c.save_chars([active]))[0]} for chars,active in group])
            state['snapshots'][snapshot]={'banks':saved,'epoch':state['epoch']}
            public={'snapshot':snapshot,'population':len(accounts),'counts':counts,'spectra':spectra,
                    'shapes':SHAPES+['OTHER'],'shape_version':SHAPE_VERSION,'epsilon':epsilon,
                    'epsilon_spent':state['epsilon_spent'],'fragment_violations':[max(0,min(len(accounts),v)) for v in noisy_rules],
                    'stage':stage,'population_epoch':state.get('data_epoch',0),
                    'noise':'three independent discrete Laplace contributions','prediction':'DP estimates; no future user response model',
                    'input_mode':'local synthetic client-prepared secret histogram' if groups is not None else 'per-account secret inputs'}
            state['releases'][snapshot]=public; outcome='published'; changed=True
        elif op in {'apply_policy','rollback_policy'}:
            policy=Policy.model_validate(params['policy']).model_dump() if op=='apply_policy' else copy.deepcopy(state['policies'].get(str(params['version'])))
            if not policy: return state,'unknown_policy',{},False
            await self.load_bank(policy)
            if any(not 0<=s<len(SHAPES) for s in policy['shapes']): raise ValueError('invalid structure')
            state['policies'][str(state['policy_version'])]=state['policy']
            state['policy']=policy; state['policy_version']+=1; changed=True; outcome='policy_applied'
            public={'policy':policy,'policy_version':state['policy_version']}
        elif op=='assign_plan':
            quota=int(params['quota']); cap=int(params['total_cap'])
            if quota<1 or quota>self.cfg.get('round_task_cap',10000) or cap>self.cfg['task_cap'] or cap<state['task_used']: return state,'task_budget_exhausted',{},False
            policy=Policy(snapshot=params.get('snapshot'),positions=params.get('positions',[]),shapes=params.get('shapes',[]),fragments=params.get('fragments',[])).model_dump()
            bank=await self.load_bank(policy)
            assigned=[]
            for key in sorted(accounts):
                target=accounts[key]
                if len(assigned)>=min(quota,cap-state['task_used']): break
                if not target['enabled'] or target.get('task',{} ) and target['task']['status']=='pending': continue
                chars=c.load_chars(target['shares'])
                eligible=not bool(await mpc.output(c.accept(chars,policy,bank)))
                if eligible:
                    target['task']={'id':context['nonce'],'status':'pending','fragments':policy['fragments'],'policy':policy,'policy_version':state['policy_version']}
                    assigned.append(key)
            state['task_used']+=len(assigned)
            state['plans'][context['nonce']]={'status':'assigned','assigned':assigned,'quota':quota}
            public={'assigned':assigned,'task_used':state['task_used']}; changed=True; outcome='plan_assigned'
        elif op=='stop_plan':
            plan=state['plans'].get(params['plan'])
            if not plan: return state,'unknown_plan',{},False
            plan['status']='stopped'; changed=True; outcome='plan_stopped'
        elif op=='set_account':
            row['enabled']=bool(params['enabled']); changed=True; outcome='account_updated'
            public={'enabled':row['enabled']}
        elif op=='cancel_job':
            outcome='cancel_requested'; public={'job':params['job']}
        if changed:
            state['epoch']+=1
            if op in {'register','migrate','change','recover','delete'}:
                groups=self.load_demo_groups(old)
                if groups is not None:
                    if op in {'change','recover','delete'}:
                        groups=await c.compact_add(groups,c.load_chars(old['accounts'][a]['shares']),-1)
                    if op!='delete':groups=await c.compact_add(groups,secret['new'],1)
                state['data_epoch']=state.get('data_epoch',0)+1
                if groups is not None:await self.save_demo_groups(state,groups)
        return state,outcome,public,changed

    async def recover(self):
        # Reconcile common signed decisions against this party's own prepared
        # share state. No peer can request another party's staged shares.
        self.ledger(restore=True)
        import httpx
        from services.common.security import token
        decisions=self.store.list('decisions')
        async with httpx.AsyncClient(verify=self.cfg['ca'],timeout=4) as client:
            for i,url in enumerate(self.cfg['nodes']):
                if i==self.pid: continue
                try:
                    response=await client.get(url+'/recovery',headers={'X-Party':str(self.pid),'Authorization':'Bearer '+token(self.key,'node-recovery',{'site':self.cfg['site']},60)})
                    response.raise_for_status(); decisions.update(response.json()['decisions'])
                except Exception: pass
        for nonce,decision in sorted(decisions.items(),key=lambda x:x[1]['epoch']):
            receipt_quorum(decision['events'],self.cfg['node_keys'],{'site':self.cfg['site'],'nonce':nonce,'phase':'prepare'})
            prepared=self.store.get('prepared',nonce)
            if decision['epoch']>self.state['epoch']:
                if not prepared or 'state' not in prepared: raise ValueError('missing local prepared shares; restore own latest backup')
                self.state=prepared['state']
                self.store.put('state','current',self.state)
            self.store.put('decisions',nonce,decision)
            self.ledger()
            if prepared and not self.store.get('completed',nonce):
                receipt=prepared['receipt'];event={'party':self.pid,'receipt':receipt,'signature':self.key.sign(canonical(receipt)).hex()}
                pending=self.store.get('pending',nonce)
                if pending:
                    self.store.put('completed',nonce,{'binding':pending['binding'],'event':event})
                    self.store.remove('pending',nonce);self.store.remove('running',nonce)
            if prepared and self.store.get('completed',nonce) and decision['epoch']<=self.state['epoch']:
                self.store.put('prepared',nonce,{'receipt':prepared['receipt'],'committed_epoch':decision['epoch']})
        spent=self.store.get('budget','epsilon',0)
        self.state['epsilon_spent']=max(spent,self.state['epsilon_spent'])
        self.state['task_used']=max(self.state['task_used'],json.loads(self.ledger_path.read_text('utf-8')).get('task_used',0))
        self.store.put('state','current',self.state)

    async def worker(self):
        await mpc.start()
        await self.recover()
        checkpoints=await mpc.transfer(fingerprint_public(self.public_state()))
        if len(set(checkpoints))!=1: raise ValueError('节点状态不一致，需要事务恢复')
        self.active=True
        while True:
            await asyncio.sleep(.15)
            pending=self.store.list('pending')
            offered={n:{'digest':fingerprint_public(v['context']),'priority':0 if v['context']['op'] in USER_OPS else 2 if v['context']['op']=='profile' else 1,'created':v['created']} for n,v in pending.items()}
            queues=await mpc.transfer(offered)
            common=sorted(set(queues[0])&set(queues[1])&set(queues[2]),key=lambda n:(queues[0][n]['priority'],queues[0][n]['created'],n))
            if not common: continue
            nonce=common[0]
            if len({q[nonce]['digest'] for q in queues})!=1:
                self.store.remove('pending',nonce); continue
            request=pending[nonce]; context=request['context']
            self.store.put('running',nonce,{'started':time.time()})
            bindings=await mpc.transfer(request['binding'])
            digest=randomized_request_digest(context,bindings)
            started=time.perf_counter()
            try:
                state,outcome,public,changed=await self.execute(context,request['fields'],digest)
            except ValueError:
                state=copy.deepcopy(self.state); outcome='invalid_operation'; public={}; changed=False
            result={'site':context['site'],'nonce':nonce,'account':context['account'],'op':context['op'],
                    'context_digest':fingerprint_public(context),'request_digest':digest,'outcome':outcome,
                    'committed':changed,'epoch':state['epoch'],'policy_version':state['policy_version'],'result':public}
            prepare={**result,'phase':'prepare'}
            # Only mutating requests need staged shares for crash recovery.
            prepared={'receipt':result,'committed_epoch':state['epoch']}
            if changed: prepared['state']=state
            self.store.put('prepared',nonce,prepared)
            self.local_fault('after_prepare',nonce)
            signed={'party':self.pid,'receipt':prepare,'signature':self.key.sign(canonical(prepare)).hex()}
            events=await mpc.transfer(signed)
            receipt_quorum(events,self.cfg['node_keys'],prepare)
            decision={'epoch':state['epoch'],'events':events}
            self.store.put('decisions',nonce,decision)
            self.ledger()
            self.local_fault('after_decision',nonce)
            # Every party has persisted the common decision before swapping.
            await mpc.transfer({'durable':nonce})
            event={'party':self.pid,'receipt':result,'signature':self.key.sign(canonical(result)).hex()}
            with self.store.transaction() as conn:
                self.store.put('state','current',state,conn)
                self.store.put('completed',nonce,{'binding':request['binding'],'event':event},conn)
                # Current shares and the completed receipt are now durable together.
                self.store.put('prepared',nonce,{'receipt':result,'committed_epoch':state['epoch']},conn)
                self.store.remove('pending',nonce,conn); self.store.remove('running',nonce,conn)
                self.store.append('operation',{'nonce':nonce,'op':context['op'],'outcome':outcome,'seconds':time.perf_counter()-started},conn)
            self.state=state
            self.ledger()
            self.local_fault('after_commit',nonce)


async def main():
    import argparse
    parser=argparse.ArgumentParser(); parser.add_argument('--node-config',required=True); args=parser.parse_args()
    cfg=json.loads(Path(args.node_config).read_text('utf-8'))
    node=Node(cfg,Path(args.node_config).parent)
    server=uvicorn.Server(uvicorn.Config(node.app,host=cfg.get('host','127.0.0.1'),port=cfg['port'],ssl_certfile=cfg['cert'],ssl_keyfile=cfg['tls_key'],log_level='warning',access_log=False))
    async def compute():
        try: await node.worker()
        except Exception as exc:
            node.failed=type(exc).__name__; node.active=False
            print('Computation halted:',type(exc).__name__,str(exc),flush=True)
            raise
    await asyncio.gather(server.serve(),compute())

if __name__=='__main__': mpc.run(main())
