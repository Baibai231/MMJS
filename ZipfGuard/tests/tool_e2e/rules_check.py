"""Prepare separate randomized synthetic inputs, launch real MPC circuits."""
from client import Client,ROOT
import json,subprocess,time,os,sys

def run():
 c=Client();reference=json.loads((ROOT/'contracts/public_reference.json').read_text('utf-8'))
 vectors={1:('Abc9!xyz','LongEnough7!'),2:('Abc9!xyz123','LongEnough78!'),3:('Abc9!xyz1234','SufficientLength7!'),
 4:('1234!678','Abcd!678'),5:('ABCD!678','Abcd!678'),6:('abcd!678','Abcd!678'),7:('abcd!xyz','abcd!xy7'),
 8:('abcd xyz','abcd!xyz'),9:('abcdefgh','abcdefg7'),10:('abababab','abcdabcd'),11:('aaaBcd78','aaBcd789'),
 12:('aaaaaaaa','aaaaaaab'),13:('QwErTy78','QxErTy78'),14:('4321abcd','4201abcz'),
 15:((reference['dictionary'][0]+'0'*8)[:8], 'Dictionary-Free-82'),16:('gitlab78','gitlox78'),17:('Current-Hot-Fixture-7','Unique-Outside-Fixture-8'),18:(reference['words'][0],'Unique-Outside-Fixture-8')}
 # Dictionary reference includes short words: pad only inside the share encoder,
 # never change the semantic fixture to satisfy registration's minimum length.
 def split(word):
  import secrets
  raw=list(word.encode('ascii'))+[0]*(64-len(word));parts=[[],[],[]]
  for ch in raw:
   for bit in range(8):
    slope=secrets.randbelow(c.prime)
    for p in range(3):parts[p].append(str(((ch>>bit)&1)+(p+1)*slope % c.prime))
  return [[str(int(v)%c.prime) for v in part] for part in parts]
 vectors[15]=(reference['dictionary'][0],vectors[15][1])
 cases=[[] for _ in range(3)];hot=split(vectors[17][0])
 for n,(bad,good) in vectors.items():
  for label,word,expected in [('reject',bad,False),('accept',good,True)]:
   parts=split(word)
   for p in range(3):cases[p].append({'id':f'{n:02d}-{label}','fragment':n,'shares':parts[p],'expected':expected})
 children=[];handles=[]
 for p in range(3):
  folder=ROOT/'tool_runtime'/f'party_{p}';fixture=folder/'rule-fixture.json';fixture.write_text(json.dumps({'cases':cases[p],'hot':hot[p]}),encoding='utf-8')
  log=open(folder/'rule-tests.log','w',encoding='utf-8');handles.append(log)
  command=[sys.executable,str(ROOT/'tests/tool_e2e/rule_party.py'),'-M','3','-I',str(p),'-T','1','-B','8651','-K','40','--ssl','--fixture',str(fixture),'--result-path',str(folder/'rule-results.json')]
  children.append(subprocess.Popen(command,cwd=folder,stdout=log,stderr=subprocess.STDOUT,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0))
 try:
  deadline=time.time()+600
  while any(child.poll() is None for child in children):
   if time.time()>deadline:raise TimeoutError('rule circuit timeout')
   time.sleep(1)
  outputs=[json.loads((ROOT/'tool_runtime'/f'party_{p}'/'rule-results.json').read_text('utf-8')) for p in range(3)]
  assert all(child.returncode==0 for child in children)
  assert all(all(row['passed'] for row in value['cases']) for value in outputs)
  assert all(outputs[0]['cases']==value['cases'] for value in outputs)
  path=ROOT/'reports/tool_acceptance/rule-results.json';path.write_text(json.dumps({'passed':True,'cases':outputs[0]['cases'],'seconds':[v['seconds'] for v in outputs],'real_mpc':True},ensure_ascii=False,indent=2),encoding='utf-8')
  print('All 18 rules, acceptance and rejection, passed in real three-party MPC.',flush=True)
 finally:
  for child in children:
   if child.poll() is None:child.terminate()
  for handle in handles:handle.close()
if __name__=='__main__':run()
