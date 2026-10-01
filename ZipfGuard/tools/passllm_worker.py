"""Train a fresh PassLLM trawling LoRA on development data and emit guesses.

Uses the author's prompt, character vocabulary, preprocessing and sampling
core. The released RockYou LoRA is never loaded by this worker.
"""
from __future__ import annotations

import json
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ai.research_models import PASSLLM_ARTIFACT, PASSLLM_BASE, file_hash


def run(request: dict) -> dict:
    import torch
    from peft import LoraConfig, PeftModel, get_peft_model
    from transformers import AutoModelForCausalLM, AutoTokenizer

    sys.path.insert(0, str(PASSLLM_ARTIFACT))
    from src.search.search import _random_sample, post_process_sequences
    from src.utils.tokenize import (get_alpha_vocab, process_test_trawling,
                                    process_train_trawling)

    cfg = request['config']
    generation = request['generation']
    torch.manual_seed(request['seed'])
    random.seed(request['seed'])
    torch.set_num_threads(cfg['threads'])
    started = time.monotonic()
    base = AutoModelForCausalLM.from_pretrained(
        PASSLLM_BASE, local_files_only=True, trust_remote_code=False,
        torch_dtype=torch.float32, device_map='cpu')
    tokenizer = AutoTokenizer.from_pretrained(
        PASSLLM_BASE, local_files_only=True, trust_remote_code=False)
    tokenizer.pad_token_id = tokenizer.eos_token_id
    vocab = get_alpha_vocab(tokenizer)
    checkpoint = request.get('checkpoint')
    training_records = 0
    training_steps = 0
    if checkpoint:
        model = PeftModel.from_pretrained(base, checkpoint, is_trainable=False)
    else:
        model = get_peft_model(base, LoraConfig(
            r=cfg['lora_r'], lora_alpha=cfg['lora_alpha'],
            lora_dropout=cfg['lora_dropout'], bias='none',
            target_modules=['q_proj', 'k_proj', 'v_proj', 'o_proj',
                            'gate_proj', 'up_proj', 'down_proj'],
            task_type='CAUSAL_LM'))
        records = []
        with Path(request['training_path']).open(encoding='utf-8') as stream:
            for line in stream:
                word = line.rstrip('\r\n')
                if word:
                    records.append(process_train_trawling(
                        {'password': word}, tokenizer, 1, vocab,
                        MAX_LENGTH=cfg['max_length'] + 96))
        if not records:
            raise ValueError('PassLLM has no supported development records')
        training_records = len(records)
        optimizer = torch.optim.AdamW(
            (p for p in model.parameters() if p.requires_grad),
            lr=cfg['learning_rate'])
        model.train()
        for _ in range(cfg['epochs']):
            order = list(range(len(records)))
            random.shuffle(order)
            for start in range(0, len(order), cfg['train_batch_size']):
                batch = [records[i] for i in order[start:start + cfg['train_batch_size']]]
                width = max(len(row['input_ids']) for row in batch)
                ids = torch.tensor([row['input_ids'] +
                                    [tokenizer.pad_token_id] * (width - len(row['input_ids']))
                                    for row in batch], dtype=torch.long)
                masks = torch.tensor([row['attention_mask'] +
                                      [0] * (width - len(row['attention_mask']))
                                      for row in batch], dtype=torch.long)
                labels = torch.tensor([row['labels'] +
                                       [-100] * (width - len(row['labels']))
                                       for row in batch], dtype=torch.long)
                optimizer.zero_grad(set_to_none=True)
                model(input_ids=ids, attention_mask=masks, labels=labels).loss.backward()
                torch.nn.utils.clip_grad_norm_(
                    (p for p in model.parameters() if p.requires_grad), 1.0)
                optimizer.step()
                training_steps += 1
        checkpoint = Path(request['work']) / 'checkpoint'
        model.save_pretrained(checkpoint)
    model.eval()
    training_seconds = time.monotonic() - started

    prompt = process_test_trawling(tokenizer, 1)['input_ids_no_response']
    vocab_ids = torch.tensor(list(vocab.values()), dtype=torch.int)
    emitted = 0
    draws = 0
    output = Path(request['output_path'])
    generation_start = time.monotonic()
    stop = 'raw_limit'
    with output.open('w', encoding='utf-8', newline='\n') as stream:
        with torch.inference_mode():
            while draws < generation['raw_limit']:
                if time.monotonic() - generation_start >= generation['timeout_seconds']:
                    stop = 'timeout'
                    break
                size = min(cfg['generation_batch_size'], generation['raw_limit'] - draws)
                sequences = _random_sample(model, prompt, size, cfg['max_length'], vocab_ids)
                guesses = post_process_sequences(
                    sequences, tokenizer, sort_by_score=False, verbose=False)
                for _, word in guesses:
                    stream.write(word + '\n')
                emitted += len(guesses)
                draws += size
    return {
        'checkpoint': str(checkpoint),
        'checkpoint_hashes': {p.name: file_hash(p) for p in Path(checkpoint).iterdir()
                              if p.is_file()},
        'training_records': training_records,
        'training_steps': training_steps,
        'training_seconds': round(training_seconds, 3),
        'generation_seconds': round(time.monotonic() - generation_start, 3),
        'raw_draws': draws,
        'upstream_raw_count': emitted,
        'source_stop': stop,
        'method': 'PassLLM development-trained LoRA; author trawling prompt and random sampler',
        'prompt_id': 1,
        'sampling_order': 'raw draw order, not probability rank',
        'pretrained_password_weights': False,
        'base_model_sha256': file_hash(PASSLLM_BASE / 'model.safetensors'),
    }


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit('Usage: passllm_worker.py request.json')
    request = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
    result = run(request)
    (Path(request['work']) / 'result.json').write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
