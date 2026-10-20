from pathlib import Path
import sys,json,time,traceback,inspect,hashlib
BASE=Path(__file__).resolve().parent
ROOT=BASE/'source/ZipfGuard';ORIGINAL=ROOT
sys.path.insert(0,str(ROOT))
OUT=Path(sys.argv[2]);OUT.mkdir(parents=True,exist_ok=True)
def status(stage,**kw):
 d={'stage':stage,'updated_at':time.strftime('%Y-%m-%dT%H:%M:%S%z'),'scope':'formal registration model; independent development/reference training',**kw}
 (OUT/'status.json').write_text(json.dumps(d,ensure_ascii=False,indent=2));print(json.dumps(d,ensure_ascii=False),flush=True)
try:
 from core.registration import load_registration
 from core.corpus import counts_hash
 import ai.research_models as research
 research.PASSLLM_ARTIFACT=BASE/'models/artifact'
 research.PASSLLM_BASE=BASE/'models/base'
 from ai.research_models import PASSLLM_ARTIFACT,PASSLLM_BASE
 import gzip
 with gzip.open(sys.argv[1],'rt') as f: counts=json.load(f)['reference']
 training=OUT/'training.txt';used=0
 with training.open('w') as f:
  for w,n in sorted(counts.items()):
   if 0<len(w)<=30 and all(33<=ord(c)<=126 for c in w):f.write((w+'\n')*n);used+=n
 cfg={'threads':4,'max_length':30,'epochs':3,'train_batch_size':16,'generation_batch_size':4096,'learning_rate':5e-4,'lora_r':16,'lora_alpha':64,'lora_dropout':.2}
 request={'config':cfg,'generation':{'raw_limit':1000000,'timeout_seconds':259200},'seed':11,'training_path':str(training),'work':str(OUT),'output_path':str(OUT/'guesses.txt')}
 (OUT/'request.json').write_text(json.dumps(request,indent=2));status('training',training_records=used,epochs=3)
 import torch
 assert torch.cuda.is_available()
 torch.cuda.set_per_process_memory_fraction(0.9)
 sys.path.insert(0,str(PASSLLM_ARTIFACT))
 from src.search import search
 code=inspect.getsource(search._random_sample).replace('torch.float64','torch.float32');exec(compile(code,'<mps-float32-score>','exec'),search.__dict__)
 text=(ROOT/'tools/passllm_worker.py').read_text()
 text=text.replace("ROOT = Path(__file__).resolve().parents[1]",f"ROOT = Path({str(ROOT)!r})")
 text=text.replace("torch_dtype=torch.float32, device_map='cpu'","torch_dtype=torch.float32, device_map='cuda', attn_implementation='sdpa'")
 text=text.replace('dtype=torch.long)','dtype=torch.long, device="cuda")')

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
                torch.cuda.synchronize()
                time.sleep(0)
                training_steps += 1"""
 assert old in text
 text=text.replace(old,new)
 text=text.replace('draws += size', 'draws += size\n                torch.cuda.synchronize()\n                time.sleep(0)')

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

 text=text.replace("        for _ in range(cfg['epochs']):", """        from peft import set_peft_model_state_dict
        resume = Path(request['work']) / 'epoch_resume.pt'
        first_epoch = 0
        if resume.exists():
            state = torch.load(resume, map_location='cpu', weights_only=False)
            set_peft_model_state_dict(model, state['adapter'])
            optimizer.load_state_dict(state['optimizer'])
            for optstate in optimizer.state.values():
                for key, value in optstate.items():
                    if torch.is_tensor(value) and key != 'step': optstate[key] = value.to('cuda')
            random.setstate(state['python_rng']); torch.set_rng_state(state['torch_rng'])
            torch.cuda.set_rng_state(state['mps_rng'])
            first_epoch = state['completed_epochs']; training_steps = state['training_steps']
        for epoch in range(first_epoch, cfg['epochs']):""")
 checkpoint_line="        checkpoint = Path(request['work']) / 'checkpoint'"
 epoch_save="""            from peft import get_peft_model_state_dict
            torch.cuda.synchronize()
            saved = {'adapter': {k:v.detach().cpu() for k,v in get_peft_model_state_dict(model).items()},
                     'optimizer': optimizer.state_dict(), 'completed_epochs':epoch+1,
                     'training_steps':training_steps, 'python_rng':random.getstate(),
                     'torch_rng':torch.get_rng_state(), 'mps_rng':torch.cuda.get_rng_state()}
            temporary = Path(request['work']) / 'epoch_resume.tmp'
            torch.save(saved, temporary); temporary.replace(resume)
            status('epoch_saved', completed_epochs=epoch+1, training_steps=training_steps)
"""
 text=text.replace(checkpoint_line,epoch_save+checkpoint_line)
 text=text.replace('training_steps += 1','training_steps += 1\n                if training_steps % 10 == 0:\n                    torch.cuda.synchronize()\n                    status("training", steps=training_steps, records=training_records, elapsed_seconds=round(time.monotonic()-started,2))')
 text=text.replace('\n    model.eval()\n    training_seconds','\n    status("generating", training_steps=training_steps, training_seconds=round(time.monotonic()-started,2))\n    model = model.merge_and_unload()\n    model.eval()\n    training_seconds')
 text=text.replace('draws += size','draws += size\n                if draws % 4096 == 0:\n                    status("generating", raw_draws=draws, emitted=emitted, elapsed_seconds=round(time.monotonic()-generation_start,2))')
 text=text.replace('with torch.inference_mode():', 'with torch.inference_mode(), torch.autocast(\"cuda\", dtype=torch.bfloat16):')
 (OUT/'executed_worker.py').write_text(text)
 ns={'__name__':'formal_worker','__file__':str(ROOT/'tools/passllm_worker.py'),'status':status};exec(compile(text,str(OUT/'executed_worker.py'),'exec'),ns)

 original_predict=search.predict_next
 @torch.no_grad()
 def fast_predict_next(model,vocab,input_ids,past_key_values=None):
  outputs=model.model(input_ids=input_ids,past_key_values=past_key_values,output_attentions=False,output_hidden_states=False,use_cache=True,return_dict=True)
  if not hasattr(fast_predict_next,'weights'):
   fast_predict_next.weights=model.lm_head.weight.index_select(0,vocab.long())
  logits=torch.nn.functional.linear(outputs.last_hidden_state[:,-1,:],fast_predict_next.weights)
  probs=logits.log_softmax(dim=1)
  if not getattr(fast_predict_next,'verified',False):
   expected,_=original_predict(model,vocab,input_ids,past_key_values)
   err=float((expected-probs).abs().max())
   if not torch.allclose(expected,probs,rtol=1e-5,atol=1e-5): raise RuntimeError('Restricted projection mismatch')
   status('generation_projection_verified',max_error=err,inference_precision='bfloat16')
   fast_predict_next.verified=True
  return probs,outputs.past_key_values
 search.predict_next=fast_predict_next
 result=ns['run'](request);result.update(training_counts_sha256=counts_hash(counts),independent_reference_training=True,device='cuda',precision='float32',generation_precision='bfloat16',generation_batch_size=4096,restricted_projection=True,score_accumulator='float32',complete=result['raw_draws']==1000000 and result['upstream_raw_count']==1000000)
 (OUT/'result.json').write_text(json.dumps(result,indent=2));status('completed' if result['complete'] else 'incomplete',**result)
except BaseException as e:status('failed',error=str(e),traceback=traceback.format_exc());raise
