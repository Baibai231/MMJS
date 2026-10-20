from pathlib import Path
import sys,json,time,gzip,subprocess,shutil,traceback,hashlib
from collections import Counter
B=Path(__file__).resolve().parent;R=B/'source/ZipfGuard';sys.path.insert(0,str(R));O=B/'runs/registration_paired';PYTHON=sys.executable
def write(p,d):p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(d,ensure_ascii=False,indent=2))
def status(s,**kw):write(O/'status.json',{'stage':s,'time':time.strftime('%Y-%m-%dT%H:%M:%S%z'),**kw});print(s,kw,flush=True)
def packed(p,d):
 p.parent.mkdir(parents=True,exist_ok=True)
 with gzip.open(p,'wt') as f:json.dump(d,f)
def model(endpoint,out):
 out.mkdir(parents=True,exist_ok=True)
 if not (out/'result.json').exists():
  with (out/'run.log').open('a') as log:subprocess.run([PYTHON,str(B/'run_registration_model.py'),str(endpoint),str(out)],stdout=log,stderr=subprocess.STDOUT,check=True)
 r=json.loads((out/'result.json').read_text());assert r['raw_draws']==1000000
 guesses={}
 for i,w in enumerate((out/'guesses.txt').read_text().splitlines(),1):guesses.setdefault(w,i)
 return guesses,r

def evaluate(counts,index,llm,result,label,phase,path):
 hits={k:0 for k in ['pcfg','markov','passllm','union']};n=sum(counts.values())
 for w,c in counts.items():
  yes={k:r.get(w,1000001)<=1000000 for k,r in {**index.ranks,'passllm':llm}.items()}
  for k,v in yes.items():hits[k]+=c*bool(v)
  hits['union']+=c*any(yes.values())
 write(path,{'label':label,'phase':phase,'accounts':n,'budget_per_model':1000000,'max_total_budget':3000000,'rates':{k:v/n for k,v in hits.items()},'hits':hits,'PassLLM_generation':result,'budget_note':'Raw draw budget, retained output may be smaller; no claim of million valid strings.'})

def main():
 try:
  from experiments.registration_joint_data import partition,load_private,save_private
  from tools.run_registration_five_pareto import run_arm
  from ai.dual_attack import build_dual_index
  status('preparing_new_partition')
  split={'split_seed':202610190957,'role_sizes':{'train':60000,'tuning':10000,'preview_select':10000,'preview_confirm':10000,'test_11':100000}}
  data=load_private(O/'private/partition.pkl.gz')
  write(O/'partition_manifest.json',data['metadata'])
  fixed_counts=Counter(w for _,w in data['roles']['train']);ep=O/'private/fixed_model.endpoint.json.gz';packed(ep,{'reference':fixed_counts})
  dualcfg=json.loads((B/'runs/existing_80_15_5/policy/config.json').read_text());dualcfg['seed']=11
  status('fixed_PassLLM_training_generation')
  Fllm,Fresult=model(ep,O/'fixed_passllm')
  Findex=build_dual_index(fixed_counts,dualcfg)
  base=json.loads((R/'configs/registration_five_pareto_100k.json').read_text());base.update(candidate_workers=6,reuse_attack_roots=[],exclude_experiment_roots=[],comparison_source=str(O))
  scenarios=[('baseline_60_25_15',[.6,.25,.15]),('local_80_15_5',[.8,.15,.05])]
  for name,weights in scenarios:
   root=O/name;root.mkdir(exist_ok=True);cfg={**base,'weights':weights}
   save_private(root/'private/partition.pkl.gz',data)
   manifest={'config':cfg,'partition_sha256':hashlib.sha256((O/'private/partition.pkl.gz').read_bytes()).hexdigest(),'new_partition':True,'pairing':'same target/dev roles and seed11 in both behavior scenarios','initial_Segreti':'same frozen list, built once from baseline development; reused in treatment','F_training':'raw independent development; shared across methods and weights','A_training':'method-conditioned independent development, separate for each scenario and method','evaluation':'PCFG+OMEN+PassLLM each 1000000 raw attempts/draws; union max3000000','controller':'original PCFG/OMEN; PassLLM endpoint evaluation only'}
   mp=root/'manifest.json'
   if mp.exists():assert json.loads(mp.read_text())==manifest,'Frozen registration configuration changed'
   else:write(mp,manifest)
   for arm in ['segreti_struct_m_nohint','schechter_strong','malone','github_core','bidirectional_exchange']:
    status('registration_policy',scenario=name,arm=arm)
    ready=root/'runs/11'/arm/'result.json'
    while not ready.exists():
     ps=O/'policy_status.json'
     if ps.exists() and json.loads(ps.read_text()).get('stage')=='failed': raise RuntimeError('CPU policy producer failed; inspect policy_status.json')
     time.sleep(15)
    state=load_private(root/'runs/11'/arm/'state.pkl.gz');target=state['history'];reference=state['dev_history'];assert sum(target.values())==100000 and sum(reference.values())==60000
    evaluate(target,Findex,Fllm,Fresult,arm,'F',root/'evaluation'/f'{arm}_F.json')
    ae=root/'private'/f'{arm}.endpoint.json.gz';packed(ae,{'reference':reference})
    status('adaptive_PassLLM_training_generation',scenario=name,arm=arm)
    Allm,Aresult=model(ae,root/'adaptive_passllm'/arm)
    Aindex=build_dual_index(reference,dualcfg)
    evaluate(target,Aindex,Allm,Aresult,arm,'A',root/'evaluation'/f'{arm}_A.json')
  status('completed',scenarios=2,methods_per_scenario=5,F_A_evaluations=20)
 except BaseException as e:status('failed',error=str(e),traceback=traceback.format_exc());raise
if __name__=='__main__':main()
