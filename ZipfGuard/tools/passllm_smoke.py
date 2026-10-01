"""Reproduce a small author-checkpoint trawling sample, never a study result.

The bundled LoRA was trained on RockYou. Its output must not be used to
evaluate this project's RockYou-based policy experiment.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ai.research_models import (PASSLLM_ARTIFACT, PASSLLM_BASE,
                                PASSLLM_ARTIFACT_HASHES, file_hash, source_status)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sample-size', type=int, default=16)
    parser.add_argument('--batch-size', type=int, default=4)
    parser.add_argument('--max-length', type=int, default=30)
    args = parser.parse_args()
    if not 1 <= args.sample_size <= 1000 or not 1 <= args.batch_size <= 32:
        parser.error('sample-size must be 1..1000 and batch-size 1..32')
    if not 1 <= args.max_length <= 30:
        parser.error('max-length must be 1..30')

    status = source_status('passllm')
    if not status['source_available'] or not status['base_model_present']:
        raise RuntimeError(status['reason'])

    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer
    from peft import PeftModel

    sys.path.insert(0, str(PASSLLM_ARTIFACT))
    from src.search.search import random_sample
    from src.utils.tokenize import get_alpha_vocab, process_test_trawling

    torch.manual_seed(42)
    torch.set_num_threads(4)
    started = time.monotonic()
    base = AutoModelForCausalLM.from_pretrained(
        PASSLLM_BASE, local_files_only=True, trust_remote_code=False,
        torch_dtype=torch.float32, device_map='cpu')
    tokenizer = AutoTokenizer.from_pretrained(
        PASSLLM_BASE, local_files_only=True, trust_remote_code=False)
    checkpoint = PASSLLM_ARTIFACT / 'checkpoints/rockyou_100w_disQwen0.5B'
    model = PeftModel.from_pretrained(base, checkpoint, is_trainable=False)
    model.eval()
    output_dir = ROOT / 'local_attack_models' / 'runtime' / 'passllm_author_smoke'
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / 'author_sample.tsv'
    with torch.inference_mode():
        rows = random_sample(
            model=model, tokenizer=tokenizer, vocab=get_alpha_vocab(tokenizer),
            batch_size=args.batch_size, max_length=args.max_length,
            sample_size=args.sample_size,
            prompt_ids=process_test_trawling(tokenizer, 1)['input_ids_no_response'],
            output_file=str(output))
    report = {
        'purpose': 'author_checkpoint interface reproduction only',
        'model': 'PassLLM distilled Qwen2.5-0.5B-Instruct',
        'method': 'author random_sample, trawling prompt_id=1; not Divide Search',
        'training_data': 'author RockYou LoRA; excluded from RockYou evaluation',
        'sample_request': args.sample_size,
        'retained_rows': len(rows),
        'axis': 'sorted retained candidates, not raw draw order',
        'elapsed_seconds': round(time.monotonic() - started, 3),
        'base_model_sha256': file_hash(PASSLLM_BASE / 'model.safetensors'),
        'adapter_sha256': PASSLLM_ARTIFACT_HASHES[
            'checkpoints/rockyou_100w_disQwen0.5B/adapter_model.safetensors'],
        'artifact_path': str(PASSLLM_ARTIFACT),
        'output_path': str(output),
    }
    (output_dir / 'report.json').write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
