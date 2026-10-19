from pathlib import Path
import sys,json,gzip,inspect,subprocess,time,traceback,hashlib
B=Path(__file__).resolve().parent;R=B/'source/ZipfGuard';sys.path.insert(0,str(R))
O=B/'runs/existing_80_15_5';P=O/'policy';P.mkdir(parents=True,exist_ok=True)
PYTHON='/Users/cjx_main/Desktop/new/MMJS/ZipfGuard/local_attack_models/passllm_venv/bin/python'
def write(p,d):p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(d,ensure_ascii=False,indent=2))
def stage(s,**kw):write(O/'completion_status.json',{'stage':s,'time':time.strftime('%Y-%m-%dT%H:%M:%S%z'),**kw});print(s,kw,flush=True)
def packed(p,d):
 with gzip.open(p,'wt') as f:json.dump(d,f)
def unpack(p):
 with gzip.open(p,'rt') as f:return json.load(f)
try:
 import experiments.intervention_pipeline as pipeline
 cfg=json.loads((B/'runs/existing_80_15_5/policy/config.json').read_text())
 cfg['evaluation']['adaptive']=False
 write(P/'config.json',cfg)
 # Endpoint-only observation hook; no intervention choices or scores changed.
 def sink(arm,counts,reference):packed(P/(arm['method']+'.endpoint.json.gz'),{'method':arm['method'],'label':arm['label'],'target':counts,'reference':reference})
 # Use stable method-name mapping even when original result lacks method key.
 def sink(arm,counts,reference):
  label=arm['label'];key=hashlib.sha256(label.encode()).hexdigest()[:12]
  packed(P/(key+'.endpoint.json.gz'),{'key':key,'label':label,'target':counts,'reference':reference})
 pipeline.__dict__['endpoint_sink']=sink
 code=inspect.getsource(pipeline.google_round_zipf_experiment)
 needle="        arm['attacks'] = {'F': arm['final']['risk'], 'A1': None,"
 assert needle in code
 code=code.replace(needle,"        endpoint_sink(arm, counts, reference_counts)\n"+needle)
 exec(compile(code,'<endpoint-only-observation-hook>','exec'),pipeline.__dict__)

 original_run_arm=pipeline.run_arm
 def cached_arm(*args,**kwargs):
  method=args[0];path=P/(method+'.completed-arm.json.gz')
  if path.exists():
   r=unpack(path)
   if r['config']==args[5]:
    from collections import Counter
    return r['result'],Counter(r['target']),Counter(r['reference'])
  result,target,reference=original_run_arm(*args,**kwargs)
  packed(path,{'config':args[5],'result':result,'target':target,'reference':reference})
  return result,target,reference
 pipeline.run_arm=cached_arm
 if not (P/'COMPLETE.json').exists():
  stage('policy_simulation')
  report=pipeline.run_intervention_pipeline(cfg,output_dir=P,progress=lambda m:print(m,flush=True),checkpoint=lambda n,d:write(P/(n+'.json'),d))
  write(P/'COMPLETE.json',{'run_id':report['metadata']['run_id'],'instrumentation':'only captures final target/reference counts; no changes to algorithm'})
 endpoints=sorted(P.glob('*.endpoint.json.gz'));assert len(endpoints)==4
 from core.registration import load_registration
 from ai.dual_attack import build_dual_index
 data=load_registration('/Users/cjx_main/Desktop/new/MMJS/rockyou-withcount.txt',users=100000,development=100000,seed=42,cohort_size=100000)
 fixed=build_dual_index(data['development']['train'],cfg)
 fixed_ranks={w:i for i,w in reversed(list(enumerate((O/'passllm_F_generation_fast/guesses.txt').read_text().splitlines(),1)))}
 def evaluate(target,index,llm,phase,key):
  total=sum(target.values());points=[]
  for budget in [1000,10000,100000,1000000]:
   sets={name:{w for w,r in ranks.items() if r<=budget} for name,ranks in index.ranks.items()}
   sets['passllm']={w for w,r in llm.items() if r<=budget}
   union=set().union(*sets.values());rates={name:sum(n for w,n in target.items() if w in words)/total for name,words in sets.items()}
   rates['union']=sum(n for w,n in target.items() if w in union)/total
   points.append({'budget_per_model':budget,'max_total_budget':3*budget,'rates':rates})
  write(O/'evaluation'/f'{key}_{phase}.json',{'phase':phase,'key':key,'accounts':total,'points':points,'passllm_note':'retained strings are shorter than raw samples when no EOS by length cap; no sorting/dedup; raw attempt counts reported separately'})
 for file in []:
  e=unpack(file);key=e['key'];a=O/'adaptive'/key;a.mkdir(parents=True,exist_ok=True)
  evaluate(e['target'],fixed,fixed_ranks,'F',key)
  if not (a/'status.json').exists() or json.loads((a/'status.json').read_text()).get('stage')!='completed':
   stage('adaptive_passllm',arm=e['label'],key=key)
   with (a/'run.log').open('w') as f:subprocess.run([PYTHON,str(B/'run_adaptive_model.py'),str(file),str(a)],stdout=f,stderr=subprocess.STDOUT,check=True)
  stage('adaptive_dual_evaluation',arm=e['label'])
  idx=build_dual_index(e['reference'],cfg)
  guesses=(a/'guesses.txt').read_text().splitlines();ranks={w:i for i,w in reversed(list(enumerate(guesses,1)))}
  evaluate(e['target'],idx,ranks,'A',key)
 stage('policy_completed_fixed_F_only',endpoints=4,evaluation='fixed-F evaluator runs separately',adaptive='paused by user')
except BaseException as e:stage('failed',error=str(e),traceback=traceback.format_exc());raise
