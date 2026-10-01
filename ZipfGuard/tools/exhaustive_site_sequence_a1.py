"""Checkpointed, exhaustive PCFG A1 evaluation of feasible ten-cohort paths.

The grouping is exact for the experimental outcome: two paths share an A1
evaluation only when every cohort yields identical training and validation
password multisets. Every original rule path maps to a group in the manifest.
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import os
import sys
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

_PROJECT = Path(__file__).resolve().parents[1]
if str(_PROJECT) not in sys.path:
    sys.path.insert(0, str(_PROJECT))

from ai.pcfg_monte_carlo import Grammar, MonteCarloIndex
from core.corpus import counts_hash
from core.monte_carlo_attack import MCRun, evaluate_mc
from core.registration import load_registration
from experiments.dynamic_config import ROOT, load_dynamic_config
from experiments.dynamic_pipeline import _response
from policy.site_catalog import site_rules
from policy.user_response import phrase_vocabulary, weighted_pool
from tools.search_site_sequences import (BUDGET, LENGTH, RULE_NAMES, _cohorts,
                                         _collision, _combine)

_WORKER = None


def _prepare(cfg):
    d = cfg['data']
    source = (ROOT / d['path']).resolve()
    dataset = load_registration(source, source_format=d['format'], encoding=d['encoding'],
                                users=d['users'], development=d['development'],
                                seed=cfg['seed'], cohort_size=d['cohort_size'])
    train = dataset['development']['train']
    pool, vocabulary = weighted_pool(train), phrase_vocabulary(train)
    rules = site_rules()
    prepared = {'train': [], 'validation': []}
    signatures = {'train': [], 'validation': []}
    for role in ('train', 'validation'):
        for j, (words, identifiers) in enumerate(_cohorts(dataset['development'][role],
                                                            cfg['seed'], role)):
            rows, hashes = {}, {}
            for name in RULE_NAMES:
                response = _response(words, rules[name], identifiers, cfg, pool,
                                     vocabulary, records=False)
                summary = response['summary']
                if summary['pending_users'] or summary['modification_rate'] > .60:
                    raise RuntimeError(f'Infeasible development response: {role} {j+1} {name}')
                rows[name] = response['final']
                hashes[name] = counts_hash(response['final'])
            prepared[role].append(rows)
            signatures[role].append(hashes)
    return dataset['metadata'], prepared, signatures


def _group_space(signatures):
    groups = {}
    path_to_group = {}
    for indices in itertools.product(range(len(RULE_NAMES)), repeat=LENGTH):
        path = tuple(RULE_NAMES[i] for i in indices)
        signature = tuple((signatures['train'][j][name],
                           signatures['validation'][j][name])
                          for j, name in enumerate(path))
        group_id = hashlib.sha256(repr(signature).encode('ascii')).hexdigest()[:24]
        code = ''.join(map(str, indices))
        if group_id not in groups:
            groups[group_id] = {'representative': list(path), 'members': 0,
                                'signature': signature}
        elif groups[group_id]['signature'] != signature:
            raise RuntimeError('Truncated group hash collision')
        groups[group_id]['members'] += 1
        path_to_group[code] = group_id
    if len(path_to_group) != len(RULE_NAMES)**LENGTH:
        raise AssertionError('Path count mismatch')
    return groups, path_to_group


def _worker_init(prepared, cfg, samples):
    global _WORKER
    _WORKER = (prepared, cfg, samples)


def _evaluate(item):
    group_id, path = item
    prepared, cfg, samples = _WORKER
    train = Counter()
    validation = Counter()
    for j, name in enumerate(path):
        train.update(prepared['train'][j][name])
        validation.update(prepared['validation'][j][name])
    grammar = Grammar.fit(train, runtime_root=ROOT / 'reports' / 'pcfg_mc_runtime',
                          timeout=cfg['pcfg']['timeout_seconds'])
    index = MonteCarloIndex(grammar, samples=samples, seed=cfg['monte_carlo']['seed'])
    point = evaluate_mc([MCRun(index, BUDGET)], validation, [BUDGET],
                        total=sum(validation.values()))['minauto'][0]
    return {'group_id': group_id, 'path': path, 'a1': point,
            'collision': _collision(validation),
            'train_hash': grammar.metadata['training_sha256'],
            'validation_hash': counts_hash(validation)}


def _evaluate_train_batch(items):
    """Fit and pre-sample once for groups with identical training responses."""
    prepared, cfg, samples = _WORKER
    first_path = items[0][1]
    train = Counter()
    for j, name in enumerate(first_path):
        train.update(prepared['train'][j][name])
    grammar = Grammar.fit(train, runtime_root=ROOT / 'reports' / 'pcfg_mc_runtime',
                          timeout=cfg['pcfg']['timeout_seconds'])
    index = MonteCarloIndex(grammar, samples=samples, seed=cfg['monte_carlo']['seed'])
    results = []
    for group_id, path in items:
        validation = Counter()
        for j, name in enumerate(path):
            validation.update(prepared['validation'][j][name])
        point = evaluate_mc([MCRun(index, BUDGET)], validation, [BUDGET],
                            total=sum(validation.values()))['minauto'][0]
        results.append({'group_id': group_id, 'path': path, 'a1': point,
                        'collision': _collision(validation),
                        'train_hash': grammar.metadata['training_sha256'],
                        'validation_hash': counts_hash(validation)})
    return results


def _read_checkpoint(path):
    rows = {}
    if not path.exists():
        return rows
    valid_lines = []
    partial_tail = False
    with path.open(encoding='utf-8') as stream:
        for line in stream:
            try:
                row = json.loads(line)
            except ValueError:
                partial_tail = True
                break  # an interrupted final write may be partial
            if 'group_id' in row and 'a1' in row:
                rows[row['group_id']] = row
                valid_lines.append(line)
    if partial_tail:
        temporary = path.with_suffix('.repaired')
        temporary.write_text(''.join(valid_lines), encoding='utf-8')
        os.replace(temporary, path)
    return rows


def run(output, *, samples=10000, workers=6):
    cfg = load_dynamic_config(path=ROOT / 'configs' / 'dynamic_full_top15_all10.json')
    started = time.time()
    print('Preparing disjoint development responses', flush=True)
    metadata, prepared, signatures = _prepare(cfg)
    groups, path_to_group = _group_space(signatures)
    output.mkdir(parents=True, exist_ok=True)
    manifest_path = output / 'manifest.json'
    checkpoint_path = output / 'a1_checkpoint.jsonl'
    status_path = output / 'status.json'
    fingerprint = hashlib.sha256(json.dumps({
        'development_hashes': metadata['development_hashes'],
        'signatures': signatures, 'samples': samples,
        'seed': cfg['monte_carlo']['seed'], 'budget': BUDGET,
    }, sort_keys=True).encode()).hexdigest()
    manifest = {'status': 'path_partition_ready', 'fingerprint': fingerprint,
                'source_sha256': metadata['source_sha256'],
                'development_hashes': metadata['development_hashes'],
                'rules': RULE_NAMES, 'path_length': LENGTH,
                'all_formal_label_paths': 15**LENGTH,
                'feasible_distinct_rule_paths': len(path_to_group),
                'exact_outcome_groups': len(groups),
                'group_members': {key: row['members'] for key, row in groups.items()},
                'path_to_group': path_to_group,
                'equivalence': ('Identical per-cohort transformed password multisets '
                                'for both PCFG train and validation; no outcome is inferred '
                                'from website labels alone.')}
    if manifest_path.exists():
        previous = json.loads(manifest_path.read_text(encoding='utf-8'))
        if previous['fingerprint'] != fingerprint:
            raise RuntimeError('Checkpoint fingerprint differs from current dataset or MC settings')
    else:
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n',
                                 encoding='utf-8')
    finished = _read_checkpoint(checkpoint_path)
    print(f'Exact groups: {len(groups)}; resume completed: {len(finished)}', flush=True)
    # Prior finalist evaluations use precisely the same data, training rule,
    # Monte Carlo seed and sample count, so they can seed the checkpoint.
    prior_path = ROOT / 'reports' / 'dynamic' / 'top15_sequence_search.json'
    if prior_path.exists():
        prior = json.loads(prior_path.read_text(encoding='utf-8'))
        if (prior.get('mc_samples') == samples and
                prior.get('dataset', {}).get('development_hashes') == metadata['development_hashes']):
            with checkpoint_path.open('a', encoding='utf-8') as stream:
                for row in prior.get('a1_rows', []):
                    code = ''.join(str(RULE_NAMES.index(name)) for name in row['path'])
                    group_id = path_to_group[code]
                    if group_id in finished:
                        continue
                    if row['train_hash'] != counts_hash(_combine(
                            {role: [{name: {'final': prepared[role][j][name]}
                                     for name in RULE_NAMES} for j in range(LENGTH)]
                             for role in ('train', 'validation')}, row['path'], 'train')):
                        raise RuntimeError('Prior PCFG A1 training hash differs')
                    record = {'group_id': group_id, 'path': row['path'], 'a1': row['a1'],
                              'collision': row['collision'], 'train_hash': row['train_hash'],
                              'source': 'reused_prior_exact_evaluation'}
                    stream.write(json.dumps(record, ensure_ascii=False) + '\n')
                    finished[group_id] = record
                stream.flush()
    pending = [(key, row['representative']) for key, row in groups.items()
               if key not in finished]
    train_batches = {}
    for item in pending:
        path = item[1]
        training_signature = tuple(signatures['train'][j][name]
                                   for j, name in enumerate(path))
        train_batches.setdefault(training_signature, []).append(item)
    print(f'PCFG A1 groups remaining: {len(pending)}', flush=True)
    print(f'Distinct remaining train/MC fits: {len(train_batches)}', flush=True)
    def save_status():
        best = min(finished.values(), key=lambda row: (row['a1']['hits'], row['collision']))
        payload = {'status': 'complete' if len(finished) == len(groups) else 'running',
                   'fingerprint': fingerprint,
                   'formal_label_paths': 15**LENGTH,
                   'feasible_distinct_rule_paths': len(path_to_group),
                   'exact_outcome_groups': len(groups),
                   'evaluated_groups': len(finished),
                   'covered_rule_paths': sum(groups[key]['members'] for key in finished),
                   'remaining_groups': len(groups) - len(finished),
                   'best_evaluated': best,
                   'elapsed_seconds': time.time() - started}
        temporary = status_path.with_suffix('.tmp')
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n',
                             encoding='utf-8')
        os.replace(temporary, status_path)
    if finished:
        save_status()
    if not pending:
        return
    with ProcessPoolExecutor(max_workers=workers, initializer=_worker_init,
                             initargs=(prepared, cfg, samples)) as executor:
        futures = {executor.submit(_evaluate_train_batch, items): tuple(key for key, _ in items)
                   for items in train_batches.values()}
        with checkpoint_path.open('a', encoding='utf-8') as stream:
            for future in as_completed(futures):
                expected = set(futures[future])
                records = future.result()
                if {row['group_id'] for row in records} != expected:
                    raise RuntimeError('Worker returned another train batch')
                for record in records:
                    stream.write(json.dumps(record, ensure_ascii=False) + '\n')
                    finished[record['group_id']] = record
                stream.flush()
                if len(finished) % 10 <= len(records) or len(finished) == len(groups):
                    save_status()
                    print(f'PCFG A1 {len(finished)}/{len(groups)} groups; '
                          f'{sum(groups[key]["members"] for key in finished)}/{len(path_to_group)} paths',
                          flush=True)
    save_status()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=ROOT / 'reports' / 'dynamic' /
                        'top15_exhaustive_a1')
    parser.add_argument('--samples', type=int, default=10000)
    parser.add_argument('--workers', type=int, default=6)
    args = parser.parse_args()
    run(args.output, samples=args.samples, workers=args.workers)
