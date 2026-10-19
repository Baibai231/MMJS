from pathlib import Path
import sys,json,gzip,subprocess,time,traceback
B=Path(__file__).resolve().parent;R=B/'source/ZipfGuard';sys.path.insert(0,str(R));O=B/'runs/existing_80_15_5';P=O/'policy';PYTHON='/Users/cjx_main/Desktop/new/MMJS/ZipfGuard/local_attack_models/passllm_venv/bin/python'
def write(p,d):p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(d,ensure_ascii=False,indent=2))
def status(s,**kw):write(O/'adaptive_status.json',{'stage':s,'time':time.strftime('%Y-%m-%dT%H:%M:%S%z'),**kw});print(s,kw,flush=True)
try:
 while json.loads((O/'fixed_F_deliverable/status.json').read_text()).get('stage')!='completed':time.sleep(15)
 (B/'PAUSE_ADAPTIVE').unlink(missing_ok=True)
 files=list(P.glob('*.endpoint.json.gz'));assert len(files)==4
 files.sort(key=lambda p:0 if p.stem.startswith('ab41940db15e') else 1)
 cfg=json.loads((P/'config.json').read_text());errors=[]
 for file in files:
  with gzip.open(file,'rt') as f:e=json.load(f)
  a=O/'adaptive'/e['key'];a.mkdir(parents=True,exist_ok=True)
  result=a/'result.json'
  if not result.exists() or json.loads(result.read_text()).get('raw_draws')!=1000000:
   status('training_and_generating',arm=e['label'],key=e['key'])
   with (a/'run.log').open('a') as log:subprocess.run([PYTHON,str(B/'run_adaptive_model.py'),str(file),str(a)],stdout=log,stderr=subprocess.STDOUT,check=True)
  try:
   status('evaluating_adaptive_three_models',arm=e['label'])
   from ai.dual_attack import build_dual_index
   index=build_dual_index(e['reference'],cfg);llm={}
   for i,w in enumerate((a/'guesses.txt').read_text().splitlines(),1):llm.setdefault(w,i)
   n=sum(e['target'].values());hits={k:0 for k in ['pcfg','markov','passllm','union']}
   for w,count in e['target'].items():
    yes={k:ranks.get(w,1000001)<=1000000 for k,ranks in {**index.ranks,'passllm':llm}.items()}
    for k,v in yes.items():hits[k]+=count*bool(v)
    hits['union']+=count*any(yes.values())
   write(a/'evaluation.json',{'phase':'A','label':e['label'],'accounts':n,'budget_per_model':1000000,'max_total_budget':3000000,'hits':hits,'rates':{k:v/n for k,v in hits.items()},'passllm_generation':json.loads(result.read_text()),'raw_draws_and_retained_strings_reported_separately':True})
  except Exception as exc:
   failure={'arm':e['label'],'error':str(exc),'traceback':traceback.format_exc()};errors.append(failure);write(a/'evaluation_failure.json',failure)
 status('completed_with_evaluation_errors' if errors else 'completed',arms=4,evaluation_errors=errors)
except BaseException as exc:status('failed',error=str(exc),traceback=traceback.format_exc());raise
