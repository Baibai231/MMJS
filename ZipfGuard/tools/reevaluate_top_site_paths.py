"""Higher-sample sensitivity check for the exhaustive A1 top ten."""
from __future__ import annotations

import json
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.dynamic_config import load_dynamic_config
from tools.exhaustive_site_sequence_a1 import (_evaluate_train_batch, _prepare,
                                               _worker_init)

ARTIFACT = ROOT / 'reports' / 'dynamic' / 'top15_exhaustive_a1'


def main():
    status = json.loads((ARTIFACT / 'status.json').read_text(encoding='utf-8'))
    if status['status'] != 'complete' or status['remaining_groups']:
        raise RuntimeError('Exhaustive result is not complete')
    rows = {}
    with (ARTIFACT / 'a1_checkpoint.jsonl').open(encoding='utf-8') as stream:
        for line in stream:
            row = json.loads(line)
            rows[row['group_id']] = row
    if len(rows) != status['exact_outcome_groups']:
        raise RuntimeError('A1 checkpoint does not cover all groups')
    top = sorted(rows.values(), key=lambda r: (r['a1']['hits'], r['collision']))[:10]
    cfg = load_dynamic_config(path=ROOT / 'configs' / 'dynamic_full_top15_all10.json')
    metadata, prepared, _ = _prepare(cfg)
    if metadata['development_hashes'] != json.loads(
            (ARTIFACT / 'manifest.json').read_text(encoding='utf-8'))['development_hashes']:
        raise RuntimeError('Development data changed')
    updated = {}
    with ProcessPoolExecutor(max_workers=4, initializer=_worker_init,
                             initargs=(prepared, cfg, 100000)) as executor:
        futures = {executor.submit(_evaluate_train_batch,
                                   [(row['group_id'], row['path'])]): row['group_id']
                   for row in top}
        for future in as_completed(futures):
            result = future.result()[0]
            updated[result['group_id']] = result
            print(f'100k MC {len(updated)}/10', flush=True)
    comparison = [{'group_id': row['group_id'], 'path': row['path'],
                   'a1_hits_10k_mc': row['a1']['hits'],
                   'a1_hits_100k_mc': updated[row['group_id']]['a1']['hits'],
                   'a1_rate_100k_mc': updated[row['group_id']]['a1']['rate'],
                   'collision': row['collision']}
                  for row in top]
    comparison.sort(key=lambda r: (r['a1_hits_100k_mc'], r['collision']))
    output = {'status': 'complete', 'scope': 'Top ten outcome groups from exhaustive 10k-MC search only',
              'development_hashes': metadata['development_hashes'],
              'budget': 10**6, 'mc_samples': 100000, 'validation_users': 20000,
              'winner_stable': comparison[0]['path'] == status['best_evaluated']['path'],
              'rows': comparison,
              'limitation': 'Other feasible groups were not reranked at 100k MC samples.'}
    (ARTIFACT / 'top10_high_sample_check.json').write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'winner_stable': output['winner_stable'],
                      'top_rates': [r['a1_rate_100k_mc'] for r in comparison[:3]]},
                     ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
