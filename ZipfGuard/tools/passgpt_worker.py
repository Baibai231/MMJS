"""Offline PassGPT training and bounded generation in an isolated environment.

Reuses the pinned author's tokenizer and causal-LM collator. The wrapper fixes
hard-coded CUDA, prevents silent truncation, and never reads evaluation targets.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import time
from pathlib import Path

os.environ['HF_HUB_OFFLINE'] = '1'
os.environ['HF_DATASETS_OFFLINE'] = '1'
os.environ['WANDB_DISABLED'] = 'true'
os.environ['TOKENIZERS_PARALLELISM'] = 'false'
ROOT = Path(__file__).resolve().parents[1]


def upstream_module(name, filename):
    path = ROOT / 'local_attack_models/upstream/passgpt/src' / filename
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def main(request_path):
    import torch
    from transformers import GPT2Config, GPT2LMHeadModel, RobertaTokenizerFast, Trainer, TrainingArguments, set_seed
    request = json.loads(Path(request_path).read_text('utf-8'))
    cfg, gen = request['config'], request['generation']
    torch.set_num_threads(cfg['threads'])
    set_seed(request['seed'])
    device = cfg['device']
    if device == 'cuda' and not torch.cuda.is_available():
        raise RuntimeError('CUDA requested but unavailable')
    if device == 'xpu' and not (hasattr(torch, 'xpu') and torch.xpu.is_available()):
        raise RuntimeError('XPU requested but unavailable')
    work = Path(request['work'])
    checkpoint = Path(request['checkpoint']) if request['checkpoint'] else work / 'checkpoint'
    training_started = time.monotonic()
    if not request['checkpoint']:
        words = Path(request['training_path']).read_text('utf-8').splitlines()
        if any(len(w) > cfg['max_length'] for w in words):
            raise ValueError('Training truncation is prohibited')
        tokenizer_class = upstream_module('passgpt_author_tokenizer', 'create_tokenizer.py').PassTokenizer
        collator_class = upstream_module('passgpt_author_utils', 'utils.py').PasswordDataCollator
        special = ['<s>', '<pad>', '</s>', '<unk>', '<mask>']
        tokenizer_native = tokenizer_class()
        alphabet = set(''.join(words))
        tokenizer_native.train_from_iterator(sorted(set(words)), vocab_size=len(alphabet)+len(special),
                                             min_frequency=1, special_tokens=special, show_progress=False)
        checkpoint.mkdir()
        tokenizer_native.save_model(str(checkpoint))
        tokenizer = RobertaTokenizerFast(vocab_file=str(checkpoint/'vocab.json'),
            merges_file=str(checkpoint/'merges.txt'), bos_token='<s>', eos_token='</s>',
            pad_token='<pad>', unk_token='<unk>', mask_token='<mask>', model_max_length=cfg['max_length']+2)
        # Tokenize words alone, adding boundaries by ID so literal special-token
        # strings in a password cannot silently become sequence boundaries.
        encoded = []
        for word in words:
            ids = [tokenizer(char, add_special_tokens=False)['input_ids'] for char in word]
            if any(len(token) != 1 for token in ids):
                raise ValueError('Tokenizer merged or split a training character')
            ids = [token[0] for token in ids]
            if len(ids) != len(word) or any(i in tokenizer.all_special_ids for i in ids):
                raise ValueError('Tokenizer does not preserve one token per training character')
            ids = [tokenizer.bos_token_id] + ids + [tokenizer.eos_token_id]
            encoded.append({'input_ids': ids, 'attention_mask': [1]*len(ids)})
        model = GPT2LMHeadModel(GPT2Config(vocab_size=len(tokenizer), n_positions=cfg['max_length']+2,
            n_ctx=cfg['max_length']+2, n_embd=cfg['embedding'], n_layer=cfg['layers'], n_head=cfg['heads'],
            bos_token_id=tokenizer.bos_token_id, eos_token_id=tokenizer.eos_token_id,
            pad_token_id=tokenizer.pad_token_id))
        args = TrainingArguments(output_dir=str(work/'trainer'), use_cpu=device=='cpu',
            per_device_train_batch_size=cfg['train_batch_size'], num_train_epochs=cfg['epochs'],
            learning_rate=cfg['learning_rate'], save_strategy='no', report_to=[],
            seed=request['seed'], data_seed=request['seed'], dataloader_num_workers=0,
            disable_tqdm=True, logging_strategy='no')
        trainer = Trainer(model=model, args=args, train_dataset=encoded,
                          data_collator=collator_class(tokenizer=tokenizer, mlm=False))
        trainer.train()
        model.save_pretrained(checkpoint, safe_serialization=True)
        tokenizer.save_pretrained(checkpoint)
        del trainer, encoded
    else:
        tokenizer = RobertaTokenizerFast.from_pretrained(checkpoint, local_files_only=True)
        model = GPT2LMHeadModel.from_pretrained(checkpoint, local_files_only=True)
    training_seconds = time.monotonic() - training_started
    model.eval().to(device)
    set_seed(request['seed'])
    started = time.monotonic()
    raw = emitted = invalid = 0
    # Invalid and unfinished samples consume the raw work allowance but never
    # become invented/truncated guesses or a false exhaustion claim.
    banned = [[i] for i in tokenizer.all_special_ids if i != tokenizer.eos_token_id]
    with Path(request['output_path']).open('w', encoding='utf-8', newline='\n') as stream:
        while raw < gen['raw_limit'] and time.monotonic()-started < gen['timeout_seconds']:
            size = min(cfg['generation_batch_size'], gen['raw_limit']-raw)
            with torch.inference_mode():
                tokens = model.generate(torch.tensor([[tokenizer.bos_token_id]], device=device),
                    attention_mask=torch.ones((1,1), dtype=torch.long, device=device),
                    do_sample=True, num_return_sequences=size, max_new_tokens=cfg['max_length']+1,
                    pad_token_id=tokenizer.pad_token_id, eos_token_id=tokenizer.eos_token_id,
                    bad_words_ids=banned, top_p=cfg['top_p'], top_k=cfg['top_k'], temperature=cfg['temperature'])
            for sequence in tokens[:,1:].tolist():
                raw += 1
                if tokenizer.eos_token_id not in sequence:
                    invalid += 1
                    continue
                ids = sequence[:sequence.index(tokenizer.eos_token_id)]
                word = tokenizer.decode(ids, clean_up_tokenization_spaces=False)
                if not word or len(word) > cfg['max_length'] or not all(33 <= ord(c) <= 126 for c in word):
                    invalid += 1
                    continue
                stream.write(word+'\n')
                emitted += 1
            stream.flush()
    meta = {'checkpoint': str(checkpoint), 'upstream_raw_count': raw, 'emitted_count': emitted,
            'invalid_or_unterminated_samples': invalid, 'source_stop': 'raw_limit' if raw >= gen['raw_limit'] else 'timeout',
            'training_seconds': training_seconds, 'generation_seconds': time.monotonic()-started,
            'order': 'seeded ancestral sampling; not probability rank', 'pretrained_password_weights': False,
            'architecture': {'layers':cfg['layers'],'heads':cfg['heads'],'embedding':cfg['embedding']},
            'smoke_architecture': (cfg['layers'],cfg['heads'],cfg['embedding']) != (8,12,768)}
    import importlib.metadata
    meta['library_versions'] = {name: importlib.metadata.version(name)
                               for name in ('torch','transformers','tokenizers','accelerate')}
    (work/'result.json').write_text(json.dumps(meta, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main(sys.argv[1])
