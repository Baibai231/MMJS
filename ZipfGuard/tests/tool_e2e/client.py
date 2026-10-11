"""Synthetic integration client. This file never runs inside a computation party."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from services.common import bootstrap
import json,secrets,time,httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from privacy.transaction_contract import canonical
from services.common.contracts import Context
from services.common.security import public_hex,fingerprint_public,receipt_quorum

class Client:
    def __init__(self,runtime=None):
        self.cfg=json.loads((Path(runtime or ROOT/'tool_runtime')/'installation.json').read_text('utf-8'))
        self.http=httpx.Client(verify=self.cfg['ca'],timeout=20,trust_env=False)
        deadline=time.time()+40
        while time.time()<deadline:
            try:
                health=[self.http.get(url+'/health').json() for url in self.cfg['nodes']]
                if all(value['ready'] for value in health):break
            except Exception:pass
            time.sleep(.5)
        else:raise TimeoutError('three parties not ready')
        self.prime=int(health[0]['field_prime'])

    def share(self,password):
        if len(password)>64 or not 8<=len(password) or any(not 32<=ord(c)<=126 for c in password):raise ValueError('invalid ASCII input')
        raw=list(password.encode('ascii'))+[0]*(64-len(password));parts=[[],[],[]]
        for c in raw:
            for b in range(8):
                slope=secrets.randbelow(self.prime)
                for i in range(3):parts[i].append(str(((c>>b)&1)+(i+1)*slope % self.prime))
        # Parentheses include bit addition in the modulus.
        return [[str(int(v)%self.prime) for v in part] for part in parts]

    def operation(self,op,account=None,passwords=None,params=None,session=None,nonce=None,recovery=None,timeout=600,complete=True,mutate_shares=None):
        api=self.cfg['user_api'] if op in {'check','register','migrate','login','change','recover','delete'} else self.cfg['admin_api']
        station=self.http.get(self.cfg['user_api']+'/station').json()
        version=self.http.get(self.cfg['user_api']+'/account-version/'+account).json()['version'] if account else 0
        key=Ed25519PrivateKey.generate();context=Context(site=self.cfg['site'],op=op,account=account,nonce=nonce or secrets.token_hex(32),
            policy_version=station['policy_version'],account_version=version,client_key=public_hex(key),params=params or {}).model_dump()
        if recovery:context['params']['recovery_signature']=recovery.sign(canonical(context)).hex()
        headers={'Authorization':'Bearer '+session} if session else {}
        response=self.http.post(api+'/ticket',json={'context':context},headers=headers);response.raise_for_status();ticket=response.json()
        fields={name:self.share(value) for name,value in (passwords or {}).items()}
        if mutate_shares:mutate_shares(fields)
        envelopes=[];started=time.perf_counter()
        for i,url in enumerate(self.cfg['nodes']):
            env={'context':context,'ticket':ticket['ticket'],'fields':{name:parts[i] for name,parts in fields.items()},'salt':secrets.token_hex(32)}
            env['signature']=key.sign(canonical(env)).hex();envelopes.append(env)
            response=self.http.post(url+'/submit',json=env);response.raise_for_status()
        events=[]
        deadline=time.time()+timeout
        while time.time()<deadline:
            data=[self.http.get(url+'/result/'+context['nonce'],headers={'Authorization':'Bearer '+ticket['query']}).json() for url in self.cfg['nodes']]
            if all(d.get('status')=='completed' for d in data):events=[d['event'] for d in data];break
            time.sleep(.3)
        if not events:raise TimeoutError('protocol result unavailable')
        receipt=receipt_quorum(events,self.cfg['node_keys'],{'site':self.cfg['site'],'nonce':context['nonce'],'context_digest':fingerprint_public(context)})
        result={'receipt':receipt,'events':events,'context':context,'envelopes':envelopes,'ticket':ticket,'seconds':time.perf_counter()-started}
        if complete:
            response=self.http.post(api+'/complete',json={'context':context,'events':events},headers=headers);response.raise_for_status();result.update(response.json())
        return result

if __name__=='__main__':
    client=Client();recovery=Ed25519PrivateKey.generate()
    result=client.operation('register','integration-smoke',{'new':'Unique-Sample-Password-37'},params={'recovery_key':public_hex(recovery)})
    print(json.dumps({'outcome':result['outcome'],'seconds':result['seconds']}))
