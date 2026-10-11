"""Two different ASGI services with separate keys, origins, and audiences."""
from __future__ import annotations
import asyncio
import json
import secrets
import time
from pathlib import Path
from fastapi import FastAPI,HTTPException,Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from services.common.store import Store
from services.common.security import load_private,public_hex,token,verify_token,fingerprint_public,receipt_quorum
from services.common.contracts import TicketRequest,ReceiptRequest,AdminAuth,CredentialResponse,Policy,USER_OPS,ADMIN_OPS
from adapters.live_engines import registration_candidates,intervention_candidates
from policy.intervention_fragments import LABELS

ROOT=Path(__file__).resolve().parents[2]


class Business:
    def __init__(self,cfg,kind):
        self.cfg=cfg; self.kind=kind
        self.store=Store(cfg['store'])
        self.key=load_private(cfg['key'])
        self.app=FastAPI(title=f'ZipfGuard {kind} API',docs_url=None,redoc_url=None)
        self.app.add_middleware(CORSMiddleware,allow_origins=cfg['origins'],allow_methods=['GET','POST'],allow_headers=['Content-Type','Authorization'])
        if not self.store.get('site','state'):
            self.store.put('site','state',{'policy':Policy().model_dump(),'policy_version':1,'epsilon_spent':0,'task_used':0,'accounts':{},'profiles':{},'plans':{}})
        # Backfill exact business receipt times for historical test snapshots,
        # only when every published journal event matches one cached snapshot.
        state=self.store.get('site','state')
        published=sorted([e for e in self.store.events(10000) if e['kind']=='business_result' and e['data'].get('op')=='profile' and e['data'].get('outcome')=='published'],key=lambda e:e['sequence'])
        if len(published)==len(state['profiles']) and published:
            for profile,event in zip(state['profiles'].values(),published):profile.setdefault('received_at',event['created'])
            self.store.put('site','state',state)
        self.routes()

    def session(self,request):
        try:
            claims=verify_token(request.headers.get('authorization','').removeprefix('Bearer '),public_hex(self.key),self.kind+'-session')
            if claims['site']!=self.cfg['site']: raise ValueError()
            if self.store.get('revoked',claims['jti']): raise ValueError()
            if self.kind=='admin':
                admin=self.store.get('administrators',claims['sub'])
                if not admin or not admin['enabled']: raise ValueError()
                claims['role']=admin['role']
            return claims
        except Exception: raise HTTPException(401,'请重新登录')

    def owner(self,request):
        claims=self.session(request)
        if claims['role']!='owner': raise HTTPException(403,'仅站点负责人可执行')
        return claims

    def routes(self):
        app=self.app
        @app.get('/health')
        def health(): return {'service':self.kind,'ready':True,'site':self.cfg['site']}

        @app.get('/station')
        def station():
            state=self.store.get('site','state')
            return {'site':self.cfg['site'],'name':self.cfg['name'],'nodes':self.cfg['nodes'],
                    'node_keys':self.cfg['node_keys'],'policy':state['policy'],'policy_version':state['policy_version'],
                    'user_api':self.cfg['user_api'],'admin_api':self.cfg['admin_api'],'reference_site':self.cfg['reference_site'],'local_demo':self.cfg.get('local_demo',False),
                    'reference':json.loads((ROOT/'contracts/public_reference.json').read_text('utf-8')),
                    'rules':LABELS,'minimum':8,'maximum':64,'supported_charset':'printable ASCII',
                    'privacy_model':'最多一方被动腐化；可信扩展；本机三进程仅用于展示'}

        @app.post('/ticket')
        def ticket(body:TicketRequest,request:Request):
            context=body.context.model_dump()
            if context['site']!=self.cfg['site']: raise HTTPException(403,'站点不匹配')
            if not self.store.charge('ticket-source:'+request.client.host,100,300): raise HTTPException(429,'请求过多')
            if self.kind=='user':
                if context['op'] not in USER_OPS: raise HTTPException(403,'用户接口不接受管理操作')
                if context['op']=='register' and context['account'] in self.cfg.get('reserved_legacy_accounts',[]):raise HTTPException(403,'历史账户需先取得既有授权证明')
                role='user'
            else:
                claims=self.session(request); role=claims['role']
                if role not in {'owner','operator'} or context['op'] not in ADMIN_OPS: raise HTTPException(403,'无操作权限')
            self.store.put('issued',context['nonce'],{'context':context,'created':time.time()})
            return {'ticket':token(self.key,'node-operation',{'site':self.cfg['site'],'context':fingerprint_public(context),'role':role},180),
                    'query':token(self.key,'node-result',{'site':self.cfg['site'],'nonce':context['nonce']},1800)}

        @app.post('/complete')
        def complete(body:ReceiptRequest,request:Request):
            context=body.context.model_dump()
            if context['site']!=self.cfg['site'] or context['op'] not in (USER_OPS if self.kind=='user' else ADMIN_OPS): raise HTTPException(403,'操作不属于此接口')
            if self.kind=='admin': self.session(request)
            issued=self.store.get('issued',context['nonce'])
            if not issued or issued['context']!=context: raise HTTPException(403,'未知请求')
            try:
                receipt=receipt_quorum(body.events,self.cfg['node_keys'],{'site':self.cfg['site'],'nonce':context['nonce'],
                      'op':context['op'],'account':context['account'],'context_digest':fingerprint_public(context)})
            except ValueError: raise HTTPException(400,'三方回执核验失败')
            nonce=context['nonce']; op=context['op']; account=context['account']; outcome=receipt['outcome']
            result=receipt['result']
            newly_consumed=False
            with self.store.transaction() as conn:
                if not self.store.get('consumed',nonce,connection=conn):
                    newly_consumed=True
                    state=self.store.get('site','state',connection=conn)
                    epochs=state.setdefault('account_epochs',{})
                    latest=receipt['epoch']>epochs.get(account,-1)
                    if outcome=='accepted':
                        if op in {'register','migrate','change','recover'} and latest:
                            row=state['accounts'].get(account,{'task':None,'enabled':True})
                            row['version']=result['account_version']
                            if row.get('task') and row['task']['status']=='pending': row['task']['status']='completed'
                            state['accounts'][account]=row
                            epochs[account]=receipt['epoch']
                            if op in {'register','migrate'}:state['registrations_total']=state.get('registrations_total',0)+1
                        elif op=='delete' and latest:
                            state['accounts'].pop(account,None);epochs[account]=receipt['epoch']
                    if outcome=='policy_applied' and result['policy_version']>=state['policy_version']: state['policy']=result['policy'];state['policy_version']=result['policy_version']
                    if outcome in {'published','cached'}:
                        received=state['profiles'].get(result['snapshot'],{}).get('received_at',time.time())
                        state['profiles'][result['snapshot']]={**result,'received_at':received}; state['epsilon_spent']=max(state['epsilon_spent'],result['epsilon_spent'])
                    if outcome=='plan_assigned':
                        state['task_used']=max(state['task_used'],result['task_used']);state['plans'][nonce]={'status':'assigned','assigned_count':len(result['assigned'])}
                        for a in result['assigned']:
                            if a in state['accounts'] and receipt['epoch']>epochs.get(a,-1):
                                state['accounts'][a]['task']={'id':nonce,'status':'pending','fragments':context['params']['fragments']};epochs[a]=receipt['epoch']
                    if outcome=='plan_stopped' and context['params']['plan'] in state['plans']:state['plans'][context['params']['plan']]['status']='stopped'
                    if outcome=='account_updated' and account in state['accounts'] and latest:
                        state['accounts'][account]['enabled']=result['enabled'];epochs[account]=receipt['epoch']
                    self.store.put('site','state',state,conn)
                    safe={'nonce':nonce,'op':op,'account':account,'outcome':outcome,'policy_version':receipt['policy_version'],'epoch':receipt['epoch']}
                    self.store.append('business_result',safe,conn);self.store.put('consumed',nonce,safe,conn)
            response={'outcome':outcome,'result':result,'receipt':receipt}
            if self.kind=='user' and outcome=='accepted' and op in {'login','register','migrate','change','recover'}:
                cached=self.store.get('receipt_sessions',nonce)
                if newly_consumed and not cached and issued['created']>time.time()-1800:
                    value=token(self.key,'user-session',{'site':self.cfg['site'],'sub':account,'version':result['account_version'],'client_key':context['client_key'],'auth_nonce':nonce},900)
                    cached={'token':value};self.store.put('receipt_sessions',nonce,cached)
                if cached:
                    try:
                        claims=verify_token(cached['token'],public_hex(self.key),'user-session')
                        row=self.store.get('site','state')['accounts'].get(account)
                        if self.store.get('revoked',claims['jti']) or not row or row['version']!=claims['version'] or not row['enabled']:raise ValueError()
                        response['session']=cached['token']
                    except Exception:response['session_status']='used_or_expired'
                else:response['session_status']='used_or_expired'
            return response

        @app.post('/logout')
        def logout(request:Request):
            claims=self.session(request);self.store.put('revoked',claims['jti'],True)
            return {'status':'logged_out'}

        if self.kind=='user': self.user_routes()
        else: self.admin_routes()

    def user_routes(self):
        app=self.app
        @app.get('/account-version/{account}')
        def version(account:str):
            row=self.store.get('site','state')['accounts'].get(account)
            return {'version':row['version'] if row else 0,'enabled':bool(row and row['enabled'])}

        @app.get('/me')
        def me(request:Request):
            claims=self.session(request);state=self.store.get('site','state');row=state['accounts'].get(claims['sub'])
            if not row or row['version']!=claims['version'] or not row['enabled']: raise HTTPException(401,'账户凭据已变更或停用')
            history=[e for e in self.store.events(500) if e['data'].get('account')==claims['sub']]
            return {'account':claims['sub'],**row,'policy':state['policy'],'policy_version':state['policy_version'],'history':history[:50]}

    def admin_routes(self):
        app=self.app
        from webauthn import generate_registration_options,generate_authentication_options,verify_registration_response,verify_authentication_response,options_to_json
        from webauthn.helpers.structs import PublicKeyCredentialDescriptor,AuthenticatorSelectionCriteria,UserVerificationRequirement,ResidentKeyRequirement
        from services.common.security import b64,unb64

        @app.post('/auth/options/{mode}')
        def options(mode:str,body:AdminAuth,request:Request):
            if not self.store.charge('auth:'+request.client.host,15,300): raise HTTPException(429,'登录请求过多')
            row=self.store.get('administrators',body.identifier)
            if mode=='enroll':
                invitation=self.store.get('invitations',body.invitation or '')
                if not invitation or invitation['expires']<time.time() or invitation['identifier'] not in {'*',body.identifier} or row:
                    raise HTTPException(403,'安装凭证或邀请无效')
                opts=generate_registration_options(rp_id=self.cfg['rp_id'],rp_name=self.cfg['name'],user_name=body.identifier,
                    user_id=secrets.token_bytes(32),authenticator_selection=AuthenticatorSelectionCriteria(resident_key=ResidentKeyRequirement.REQUIRED,user_verification=UserVerificationRequirement.REQUIRED))
            elif mode=='login':
                if not row or not row['enabled']: raise HTTPException(403,'无法登录')
                opts=generate_authentication_options(rp_id=self.cfg['rp_id'],allow_credentials=[PublicKeyCredentialDescriptor(id=unb64(row['credential_id']))],user_verification=UserVerificationRequirement.REQUIRED)
            else: raise HTTPException(404,'未知认证操作')
            challenge=secrets.token_urlsafe(24)
            self.store.put('challenges',challenge,{'challenge':b64(opts.challenge),'mode':mode,'identifier':body.identifier,
                  'invitation':body.invitation,'expires':time.time()+120})
            return {'challenge_id':challenge,'options':json.loads(options_to_json(opts))}

        @app.post('/auth/verify')
        def verify(body:CredentialResponse):
            with self.store.transaction() as conn:
                challenge=self.store.get('challenges',body.challenge_id,connection=conn)
                self.store.remove('challenges',body.challenge_id,conn)
            if not challenge or challenge['expires']<time.time(): raise HTTPException(403,'认证挑战已过期')
            identifier=challenge['identifier']
            try:
                if challenge['mode']=='enroll':
                    verified=verify_registration_response(credential=body.credential,expected_challenge=unb64(challenge['challenge']),
                         expected_rp_id=self.cfg['rp_id'],expected_origin=self.cfg['web_origin'],require_user_verification=True)
                    with self.store.transaction() as conn:
                        invitation=self.store.get('invitations',challenge['invitation'],connection=conn)
                        if not invitation: raise ValueError('invitation consumed')
                        self.store.remove('invitations',challenge['invitation'],conn)
                        row={'role':invitation['role'],'enabled':True,'credential_id':b64(verified.credential_id),
                             'public_key':b64(verified.credential_public_key),'sign_count':verified.sign_count}
                        self.store.put('administrators',identifier,row,conn)
                else:
                    row=self.store.get('administrators',identifier)
                    verified=verify_authentication_response(credential=body.credential,expected_challenge=unb64(challenge['challenge']),
                         expected_rp_id=self.cfg['rp_id'],expected_origin=self.cfg['web_origin'],credential_public_key=unb64(row['public_key']),credential_current_sign_count=row['sign_count'],require_user_verification=True)
                    row['sign_count']=verified.new_sign_count; self.store.put('administrators',identifier,row)
            except Exception: raise HTTPException(403,'通行密钥核验失败')
            return {'session':token(self.key,'admin-session',{'site':self.cfg['site'],'sub':identifier,'role':row['role']},1800),'role':row['role']}

        @app.get('/dashboard')
        def dashboard(request:Request):
            claims=self.session(request);state=self.store.get('site','state')
            return {'identity':claims['sub'],'role':claims['role'],'site':self.cfg['site'],'name':self.cfg['name'],**state,
                  'legacy_reserved':len(self.cfg.get('reserved_legacy_accounts',[])),'migration_pending':sum(1 for a in self.cfg.get('reserved_legacy_accounts',[]) if a not in state['accounts']),
                  'epsilon_cap':self.cfg['epsilon_cap'],'task_cap':self.cfg['task_cap'],'events':self.store.events(100),
                  'jobs':{key:{k:v for k,v in job.items() if k not in {'params','actor','context'}} for key,job in self.store.list('jobs').items()},'settings':self.store.get('settings','current',{'retention_days':90,'scheduled_release':False})}

        @app.post('/jobs')
        async def queue_job(request:Request):
            claims=self.session(request)
            if claims['role'] not in {'owner','operator'}:raise HTTPException(403,'只读角色不能创建作业')
            body=await request.json()
            if set(body)!={'op','params','account','policy_version','account_version'} or body['op'] not in ADMIN_OPS:
                raise HTTPException(400,'作业参数无效')
            from services.common.contracts import Context
            nonce=secrets.token_hex(32)
            try:Context(site=self.cfg['site'],nonce=nonce,client_key='0'*64,**body)
            except Exception:raise HTTPException(400,'不支持的作业参数')
            self.store.put('jobs',nonce,{**body,'status':'queued','actor':claims['sub'],'created':time.time(),'id':nonce})
            return {'id':nonce,'status':'queued'}

        @app.get('/jobs/{job_id}')
        def job(job_id:str,request:Request):
            self.session(request);value=self.store.get('jobs',job_id)
            if not value:raise HTTPException(404,'作业不存在')
            return {k:v for k,v in value.items() if k not in {'params','actor','context'}}

        @app.post('/jobs/{job_id}/{action}')
        def job_action(job_id:str,action:str,request:Request):
            claims=self.session(request)
            if claims['role'] not in {'owner','operator'}:raise HTTPException(403,'只读角色不能修改作业')
            with self.store.transaction() as conn:
                value=self.store.get('jobs',job_id,connection=conn)
                if not value:raise HTTPException(404,'作业不存在')
                if action=='cancel' and value['status']=='queued':value['status']='cancelled'
                elif action=='retry' and value['status'] in {'failed','unknown'}:
                    value['status']='reconcile' if self.store.get('job_private',job_id,connection=conn) else 'queued'
                else:raise HTTPException(409,'计算中的作业需等待结果；已完成操作不能重复执行')
                self.store.put('jobs',job_id,value,conn)
            return {'status':value['status']}

        @app.post('/preview/{engine}')
        async def preview(engine:str,request:Request):
            self.session(request);body=await request.json();state=self.store.get('site','state')
            if set(body)-{'snapshot','k','j','cost_cap','pending_cap','quota','total_cap'}:raise HTTPException(400,'无效参数')
            profile=state['profiles'].get(body.get('snapshot'))
            if not profile: raise HTTPException(409,'先发布受保护画像')
            if engine=='registration':result=registration_candidates(profile,state['policy'],body.get('k',3),body.get('j',1),body.get('cost_cap',0),body.get('pending_cap',0))
            elif engine=='intervention':result=intervention_candidates(profile,body.get('quota',10),body.get('total_cap',self.cfg['task_cap']),state['task_used'])
            else:raise HTTPException(404,'未知引擎')
            self.store.append('preview',{'engine':engine,'snapshot':profile['snapshot'],'candidate_count':len(result)})
            return {'candidates':result,'source':'approved DP snapshot','confirmation':'响应与成本尚未独立确认的联合候选保持基础策略'}

        @app.get('/roles')
        def roles(request:Request):
            self.owner(request)
            return {name:{'role':row['role'],'enabled':row['enabled']} for name,row in self.store.list('administrators').items()}

        @app.post('/roles')
        async def set_role(request:Request):
            owner=self.owner(request);body=await request.json()
            if set(body)!={'identifier','role','enabled'} or body['role'] not in {'owner','operator','auditor'} or not isinstance(body['enabled'],bool):raise HTTPException(400,'角色参数无效')
            if body['identifier']==owner['sub'] and (not body['enabled'] or body['role']!='owner'):raise HTTPException(409,'负责人不能关闭自己的管理权限')
            row=self.store.get('administrators',body['identifier'])
            if not row:raise HTTPException(404,'管理员不存在')
            row.update(role=body['role'],enabled=body['enabled']);self.store.put('administrators',body['identifier'],row)
            self.store.append('role_update',body);return {'status':'updated'}

        @app.post('/invitations')
        async def invite(request:Request):
            self.owner(request);body=await request.json()
            if set(body)!={'identifier','role'} or body['role'] not in {'owner','operator','auditor'}:raise HTTPException(400,'邀请参数无效')
            value=secrets.token_urlsafe(32);self.store.put('invitations',value,{**body,'expires':time.time()+86400})
            return {'invitation':value,'expires_in':86400}

        @app.post('/settings')
        async def settings(request:Request):
            self.owner(request);body=await request.json()
            allowed={'retention_days','scheduled_release','automatic_review','registration_batch_size','k','j','cost_cap','pending_cap'}
            if set(body)-allowed or not {'retention_days','scheduled_release'}<=set(body) or not 7<=int(body['retention_days'])<=365 or not isinstance(body['scheduled_release'],bool):raise HTTPException(400,'设置参数无效')
            body={**{'automatic_review':False,'registration_batch_size':50,'k':3,'j':1,'cost_cap':0,'pending_cap':0},**body}
            if type(body['automatic_review']) is not bool or not 1<=int(body['registration_batch_size'])<=10000 or not 0<=int(body['k'])<=30 or not 0<=int(body['j'])<=8 or not 0<=float(body['cost_cap'])<=1 or not 0<=int(body['pending_cap'])<=10000:raise HTTPException(400,'复核参数无效')
            if not self.store.get('settings','current',{}).get('automatic_review') and body['automatic_review']:
                self.store.put('schedule','reviewed_registrations',self.store.get('site','state').get('registrations_total',0))
            self.store.put('settings','current',body);return {'status':'saved'}

        @app.get('/export')
        def export(request:Request):
            self.session(request);state=self.store.get('site','state')
            # No credential, per-account structure, shares, or secret set hashes.
            return {'site':self.cfg['site'],'generated':time.time(),'policy':state['policy'],'profiles':state['profiles'],
                    'plans':state['plans'],'task_used':state['task_used'],'events':self.store.events(100)}

        @app.get('/integration')
        def integration(request:Request):
            self.session(request)
            return {k:self.cfg[k] for k in ('site','nodes','node_keys','user_api','admin_api','reference_site')}

        # Separate origin: the admin frontend is only served here.
        dist=ROOT/'apps/admin-console/dist'
        @app.get('/{path:path}')
        def frontend(path:str):
            resolved=(dist/path).resolve()
            if not resolved.is_relative_to(dist.resolve()):raise HTTPException(404)
            return FileResponse(resolved if resolved.is_file() else dist/'index.html')
