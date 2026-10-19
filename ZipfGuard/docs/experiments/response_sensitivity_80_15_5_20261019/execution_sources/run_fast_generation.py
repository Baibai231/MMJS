from pathlib import Path
import sys,time,json,hashlib,inspect,traceback
B=Path(__file__).resolve().parent;R=Path('/Users/cjx_main/Desktop/new/MMJS/ZipfGuard');sys.path.insert(0,str(R))
from ai.research_models import PASSLLM_BASE,PASSLLM_ARTIFACT
sys.path.insert(0,str(PASSLLM_ARTIFACT))
import torch
from transformers import AutoModelForCausalLM,AutoTokenizer
from peft import PeftModel
from src.search import search
from src.utils.tokenize import process_test_trawling,get_alpha_vocab
O=B/'runs/existing_80_15_5/passllm_F_generation_fast';O.mkdir(parents=True,exist_ok=True);C=B/'frozen_checkpoints/existing_F'
hashes=json.loads((C/'SHA256.json').read_text())
def verify():
 assert all(hashlib.sha256((C/name).read_bytes()).hexdigest()==h for name,h in hashes.items())
def status(stage,**kw):
 d={'stage':stage,'updated_at':time.strftime('%Y-%m-%dT%H:%M:%S%z'),**kw};(O/'status.json').write_text(json.dumps(d,indent=2));print(json.dumps(d),flush=True)
try:
 verify();assert torch.backends.mps.is_available();torch.set_num_threads(4);torch.mps.set_per_process_memory_fraction(.5)
 status('loading_frozen_model')
 base=AutoModelForCausalLM.from_pretrained(PASSLLM_BASE,local_files_only=True,torch_dtype=torch.float32,attn_implementation='sdpa').to('mps')
 model=PeftModel.from_pretrained(base,C,is_trainable=False).merge_and_unload();model.eval()
 tokenizer=AutoTokenizer.from_pretrained(PASSLLM_BASE,local_files_only=True);tokenizer.pad_token_id=tokenizer.eos_token_id
 vocab=torch.tensor(list(get_alpha_vocab(tokenizer).values()),dtype=torch.int);prompt=process_test_trawling(tokenizer,1)['input_ids_no_response']
 code=inspect.getsource(search._random_sample).replace('torch.float64','torch.float32');exec(compile(code,'<mps-score-float32>','exec'),search.__dict__)
 rows=[]
 with torch.inference_mode():
  # Warm-up before timings. Benchmarks never enter the formal guess file.
  torch.manual_seed(42);search._random_sample(model,prompt,16,30,vocab);torch.mps.synchronize()
  for size in (64,128,256,512):
   try:
    torch.manual_seed(42);t=time.monotonic();seq=search._random_sample(model,prompt,size,30,vocab);torch.mps.synchronize();elapsed=time.monotonic()-t
    row={'batch':size,'seconds':elapsed,'draws_per_second':size/elapsed,'driver_bytes':torch.mps.driver_allocated_memory()};rows.append(row);status('benchmark',**row)
    del seq;torch.mps.empty_cache()
   except RuntimeError as e:
    rows.append({'batch':size,'error':str(e)});torch.mps.empty_cache();break
  (O/'benchmark.json').write_text(json.dumps(rows,indent=2))
  best=max((r for r in rows if 'draws_per_second' in r),key=lambda r:r['draws_per_second']);size=best['batch']
  torch.manual_seed(42);draws=emitted=0;t=time.monotonic()
  status('generating',selected_batch=size,benchmark_draws_per_second=best['draws_per_second'])
  with (O/'guesses.txt').open('w') as f:
   while draws<1000000:
    n=min(size,1000000-draws);seq=search._random_sample(model,prompt,n,30,vocab)
    words=search.post_process_sequences(seq,tokenizer,sort_by_score=False,verbose=False)
    for _,word in words:f.write(word+'\n')
    draws+=n;emitted+=len(words);torch.mps.synchronize()
    if draws//4096 != (draws-n)//4096:
     f.flush();elapsed=time.monotonic()-t;status('generating',selected_batch=size,raw_draws=draws,emitted=emitted,elapsed_seconds=elapsed,estimated_remaining_seconds=(1000000-draws)/(draws/elapsed))
  verify();status('completed',raw_draws=draws,emitted=emitted,elapsed_seconds=time.monotonic()-t,model_hashes_verified=True,complete_actual_strings=(emitted==1000000),warning='raw draws and retained strings are reported separately; no unsupported completeness claim')
except BaseException as e:status('failed',error=str(e),traceback=traceback.format_exc());raise
