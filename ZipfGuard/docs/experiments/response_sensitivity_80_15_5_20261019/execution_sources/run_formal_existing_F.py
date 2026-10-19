from pathlib import Path
import sys,json,time,traceback,inspect,hashlib
BASE=Path(__file__).resolve().parent
ROOT=BASE/'source/ZipfGuard';ORIGINAL=Path('/Users/cjx_main/Desktop/new/MMJS/ZipfGuard')
sys.path.insert(0,str(ROOT))
OUT=BASE/'runs/existing_80_15_5/passllm_F';OUT.mkdir(parents=True,exist_ok=True)
def status(stage,**kw):
 d={'stage':stage,'updated_at':time.strftime('%Y-%m-%dT%H:%M:%S%z'),'scope':'formal existing-account fixed F; not full two-board results',**kw}
 (OUT/'status.json').write_text(json.dumps(d,ensure_ascii=False,indent=2));print(json.dumps(d,ensure_ascii=False),flush=True)
try:
 from core.registration import load_registration
 from core.corpus import counts_hash
 import ai.research_models as research
 research.PASSLLM_ARTIFACT=ORIGINAL/'local_attack_models/passllm/Available artifacts for USENIX Security 2025 #772-v1'
 research.PASSLLM_BASE=ORIGINAL/'local_attack_models/passllm/base/Qwen2.5-0.5B-Instruct'
 from ai.research_models import PASSLLM_ARTIFACT,PASSLLM_BASE
 status('sampling_formal_data')
 data=load_registration('/Users/cjx_main/Desktop/new/MMJS/rockyou-withcount.txt',users=100000,development=100000,seed=42,cohort_size=100000,progress=lambda x:print(x,flush=True))
 old=json.loads((ORIGINAL/'reports/multiseed_v9_2026-10-11/seed_42/report.json').read_text())
 # Exact role reconstruction is checked before any model training.
 expected=old['dataset'];actual=data['metadata']
 (OUT/'dataset_comparison.json').write_text(json.dumps({'historical':expected,'reconstructed':actual},ensure_ascii=False,indent=2))
 assert expected==actual,'Dataset identity differs from original seed42 report; aborting'
 counts=data['development']['train'];training=OUT/'training.txt';used=0
 with training.open('w') as f:
  for w,n in sorted(counts.items()):
   if 0<len(w)<=30 and all(33<=ord(c)<=126 for c in w):f.write((w+'\n')*n);used+=n
 cfg={'threads':4,'max_length':30,'epochs':3,'train_batch_size':16,'generation_batch_size':64,'learning_rate':5e-4,'lora_r':16,'lora_alpha':64,'lora_dropout':.2}
 request={'config':cfg,'generation':{'raw_limit':1000000,'timeout_seconds':259200},'seed':42,'training_path':str(training),'work':str(OUT),'output_path':str(OUT/'guesses.txt')}
 (OUT/'request.json').write_text(json.dumps(request,indent=2));status('training',training_records=used,epochs=3)
 import torch
 assert torch.backends.mps.is_available()
 torch.mps.set_per_process_memory_fraction(0.5)
 sys.path.insert(0,str(PASSLLM_ARTIFACT))
 from src.search import search
 code=inspect.getsource(search._random_sample).replace('torch.float64','torch.float32');exec(compile(code,'<mps-float32-score>','exec'),search.__dict__)
 text=(ROOT/'tools/passllm_worker.py').read_text()
 text=text.replace("ROOT = Path(__file__).resolve().parents[1]",f"ROOT = Path({str(ROOT)!r})")
 text=text.replace("torch_dtype=torch.float32, device_map='cpu'","torch_dtype=torch.float32, device_map='mps', attn_implementation='sdpa'")
 text=text.replace('dtype=torch.long)','dtype=torch.long, device="mps")')

 old="""                optimizer.zero_grad(set_to_none=True)
                model(input_ids=ids, attention_mask=masks, labels=labels).loss.backward()
                torch.nn.utils.clip_grad_norm_(
                    (p for p in model.parameters() if p.requires_grad), 1.0)
                optimizer.step()
                training_steps += 1"""
 new="""                group_start = (start // 64) * 64
                group_end = min(group_start + 64, len(order))
                if start == group_start:
                    optimizer.zero_grad(set_to_none=True)
                group_tokens = sum(sum(v != -100 for v in records[i]['labels'][1:]) for i in order[group_start:group_end])
                micro_tokens = sum(sum(v != -100 for v in r['labels'][1:]) for r in batch)
                loss = model(input_ids=ids, attention_mask=masks, labels=labels, use_cache=False).loss
                (loss * (micro_tokens / group_tokens)).backward()
                if start + len(batch) == group_end:
                    torch.nn.utils.clip_grad_norm_((p for p in model.parameters() if p.requires_grad), 1.0)
                    optimizer.step()
                torch.mps.synchronize()
                time.sleep(0.1)
                training_steps += 1"""
 assert old in text
 text=text.replace(old,new)
 text=text.replace('draws += size', 'draws += size\n                torch.mps.synchronize()\n                time.sleep(0.1)')

 helper="""
def supervised_loss(model, ids, masks, labels):
    import torch
    if not getattr(supervised_loss, 'verified', False):
        was_training = model.training
        model.eval()
        with torch.no_grad():
            reference = model(input_ids=ids[:1], attention_mask=masks[:1], labels=labels[:1], use_cache=False).loss
            supervised_loss.verified = True
            optimized = supervised_loss(model, ids[:1], masks[:1], labels[:1])
            if not torch.allclose(reference, optimized, rtol=1e-5, atol=1e-5):
                raise RuntimeError('Optimized loss differs from original')
            status('loss_verified', original_loss=float(reference.cpu()), optimized_loss=float(optimized.cpu()))
        model.train(was_training)
    base = model.get_base_model()
    hidden = base.model(input_ids=ids, attention_mask=masks, use_cache=False, return_dict=True).last_hidden_state
    valid = labels[:, 1:] != -100
    selected = hidden[:, :-1, :][valid]
    logits = base.lm_head(selected).float()
    return torch.nn.functional.cross_entropy(logits, labels[:, 1:][valid])

"""
 text=text.replace('def run(request: dict)', helper+'def run(request: dict)')
 text=text.replace('model(input_ids=ids, attention_mask=masks, labels=labels, use_cache=False).loss', 'supervised_loss(model, ids, masks, labels)')
 text=text.replace('training_steps += 1','training_steps += 1\n                if training_steps % 10 == 0:\n                    torch.mps.synchronize()\n                    status("training", steps=training_steps, records=training_records, elapsed_seconds=round(time.monotonic()-started,2))')
 text=text.replace('\n    model.eval()\n    training_seconds','\n    status("generating", training_steps=training_steps, training_seconds=round(time.monotonic()-started,2))\n    model = model.merge_and_unload()\n    model.eval()\n    training_seconds')
 text=text.replace('draws += size','draws += size\n                if draws % 4096 == 0:\n                    status("generating", raw_draws=draws, emitted=emitted, elapsed_seconds=round(time.monotonic()-generation_start,2))')
 (OUT/'executed_worker.py').write_text(text)
 ns={'__name__':'formal_worker','__file__':str(ROOT/'tools/passllm_worker.py'),'status':status};exec(compile(text,str(OUT/'executed_worker.py'),'exec'),ns)
 result=ns['run'](request);result.update(training_counts_sha256=counts_hash(counts),dataset_verified=True,device='mps',precision='float32',score_accumulator='float32',complete=result['raw_draws']==1000000 and result['upstream_raw_count']==1000000)
 (OUT/'result.json').write_text(json.dumps(result,indent=2));status('completed' if result['complete'] else 'incomplete',**result)
except BaseException as e:status('failed',error=str(e),traceback=traceback.format_exc());raise
