"""Small engineering benchmark, never a policy-ranking experiment."""
from __future__ import annotations

import argparse
import copy
import json
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ai.research_models import DEFAULTS, generate
from core.open_attack import consume


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--model', choices=('omen','passgpt','passllm'), required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    train = Counter({'river123':20, 'river456':10, 'cedar123':15, 'cedar456':5})
    cfg = copy.deepcopy(DEFAULTS)
    cfg['passgpt']['generation_batch_size'] = 16
    cfg['training_timeout_seconds'] = 300
    generation = {'raw_limit': 32 if args.model == 'passllm' else 256,
                  'timeout_seconds': 120}
    started = time.perf_counter()
    stream, meta = generate(args.model, train, Counter(), cfg, generation, 42)
    meta.pop('_candidate_file', None)
    result = consume(args.model, stream, budget=10 if args.model == 'passllm' else 100,
                     raw_limit=generation['raw_limit'], timeout_seconds=10,
                     parameters=meta, source_stop=meta['source_stop'])
    report = {'purpose':'engineering-only synthetic training; not attack accuracy evidence',
              'training_occurrences':50, 'elapsed_seconds':time.perf_counter()-started,
              'run':result.summary(), 'million_budget_measured':False}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({'elapsed_seconds':report['elapsed_seconds'], 'stats':result.stats}), flush=True)


if __name__ == '__main__':
    main()
