"""Screen every feasible ten-cohort site-rule path on development data.

The exhaustive stage uses a frozen PCFG rank as an additive screening score.
Finalists are then refit and scored with PCFG A1. The latter is a candidate
search, not a proof of the global A1 optimum.
"""
from __future__ import annotations

import argparse
import itertools
import json
import math
import random
import time
from collections import Counter
from pathlib import Path

from ai.pcfg_monte_carlo import Grammar, MonteCarloIndex
from core.monte_carlo_attack import MCRun, evaluate_mc
from core.registration import load_registration
from experiments.dynamic_config import ROOT, load_dynamic_config
from experiments.dynamic_pipeline import _response
from policy.site_catalog import site_rules
from policy.user_response import phrase_vocabulary, weighted_pool

RULE_NAMES = ('site_google', 'site_amazon', 'site_netflix')
LENGTH = 10
BUDGET = 10**6


def _cohorts(counts, seed, label):
    words = [word for word, n in sorted(counts.items()) for _ in range(n)]
    random.Random(seed ^ (0xA17 if label == 'train' else 0xB18)).shuffle(words)
    return [([words[i] for i in range(j, len(words), LENGTH)],
             [f'{label}-{i}' for i in range(j, len(words), LENGTH)])
            for j in range(LENGTH)]


def _collision(counts):
    n = sum(counts.values())
    return sum(v * (v - 1) for v in counts.values()) / (n * (n - 1)) if n > 1 else 0.


def _frozen_hits(index, counts):
    return sum(n for word, n in counts.items()
               if (rank := index.query(word)['guess_count']) is not None and rank <= BUDGET)


def _combine(prepared, path, role):
    result = Counter()
    for j, name in enumerate(path):
        result.update(prepared[role][j][name]['final'])
    return result


def _path_row(prepared, path, *, frozen=True):
    stats = prepared.get('collision_stats')
    if stats is None:
        targets = _combine(prepared, path, 'validation')
        collision, users = _collision(targets), sum(targets.values())
    else:
        users = stats['users']
        numerator = sum(stats['self'][j][name] for j, name in enumerate(path))
        numerator += 2 * sum(stats['cross'][j][k][path[j]][path[k]]
                             for j in range(LENGTH) for k in range(j + 1, LENGTH))
        collision = (numerator - users) / (users * (users - 1))
    return {'path': list(path), 'frozen_hits': sum(prepared['frozen_hits'][j][name]
                                                 for j, name in enumerate(path)) if frozen else None,
            'collision': collision, 'validation_users': users,
            'modification_rate': sum(prepared['validation'][j][name]['summary']['modified_users']
                                     for j, name in enumerate(path)) / users}


def _collision_stats(prepared):
    rows = prepared['validation']
    self_terms = [{name: sum(n * n for n in row[name]['final'].values())
                   for name in RULE_NAMES} for row in rows]
    cross = [[{} for _ in range(LENGTH)] for _ in range(LENGTH)]
    for j in range(LENGTH):
        for k in range(j + 1, LENGTH):
            cross[j][k] = {a: {b: sum(n * rows[k][b]['final'].get(word, 0)
                                     for word, n in rows[j][a]['final'].items())
                               for b in RULE_NAMES} for a in RULE_NAMES}
    return {'self': self_terms, 'cross': cross,
            'users': sum(sum(rows[j][RULE_NAMES[0]]['final'].values())
                         for j in range(LENGTH))}


def _score_a1(prepared, path, cfg, samples):
    train = _combine(prepared, path, 'train')
    validation = _combine(prepared, path, 'validation')
    grammar = Grammar.fit(train, runtime_root=ROOT / 'reports' / 'pcfg_mc_runtime',
                          timeout=cfg['pcfg']['timeout_seconds'])
    index = MonteCarloIndex(grammar, samples=samples, seed=cfg['monte_carlo']['seed'])
    estimate = evaluate_mc([MCRun(index, BUDGET)], validation, [BUDGET],
                           total=sum(validation.values()))['minauto'][0]
    return {**_path_row(prepared, path), 'a1': estimate,
            'train_hash': grammar.metadata['training_sha256']}


def search(output, *, samples=10000, finalists=12):
    cfg = load_dynamic_config(path=ROOT / 'configs' / 'dynamic_full_top15_all10.json')
    cfg['monte_carlo']['samples'] = samples
    d = cfg['data']
    source = (ROOT / d['path']).resolve()
    started = time.time()
    print('Loading disjoint registration and development samples', flush=True)
    dataset = load_registration(source, source_format=d['format'], encoding=d['encoding'],
                                users=d['users'], development=d['development'],
                                seed=cfg['seed'], cohort_size=d['cohort_size'])
    dev = dataset['development']
    rules = site_rules()
    pool = weighted_pool(dev['train'])
    vocabulary = phrase_vocabulary(dev['train'])
    prepared = {'train': [], 'validation': [], 'frozen_hits': []}
    for role in ('train', 'validation'):
        for j, (words, identifiers) in enumerate(_cohorts(dev[role], cfg['seed'], role)):
            rows = {}
            for name in RULE_NAMES:
                rows[name] = _response(words, rules[name], identifiers, cfg, pool,
                                       vocabulary, records=False)
                if rows[name]['summary']['pending_users']:
                    raise RuntimeError(f'{role} cohort {j + 1} {name} has pending users')
            prepared[role].append(rows)
        print(f'Prepared {role} response tables', flush=True)
    prepared['collision_stats'] = _collision_stats(prepared)
    frozen, _ = MonteCarloIndex.load(ROOT / 'reports' / 'dynamic' /
                                     '3e6a20184a7a2f50' / 'pcfg_frozen_private.json')
    for j, rows in enumerate(prepared['validation']):
        prepared['frozen_hits'].append({name: _frozen_hits(frozen, item['final'])
                                        for name, item in rows.items()})
        print(f'Frozen score cohort {j + 1}/{LENGTH}', flush=True)
    cap = cfg['controller']['max_modification_rate']
    allowed = [tuple(name for name in RULE_NAMES
                     if prepared['train'][j][name]['summary']['modification_rate'] <= cap
                     and prepared['validation'][j][name]['summary']['modification_rate'] <= cap)
               for j in range(LENGTH)]
    if any(not row for row in allowed):
        raise RuntimeError('No feasible rules in one development cohort')
    paths = itertools.product(*allowed)
    rows = []
    for path in paths:
        rows.append(_path_row(prepared, path))
    rows.sort(key=lambda r: (r['frozen_hits'], r['collision'], r['path']))
    print(f'Exhaustive frozen screen: {len(rows)} paths', flush=True)
    # The frozen score is additive; include distinct compositions and structural
    # extremes so A1 finalists are not only near-identical frozen-score paths.
    candidates = []
    seen = set()
    for row in rows[:finalists]:
        path = tuple(row['path'])
        if path not in seen:
            candidates.append(path); seen.add(path)
    for key in (lambda r: r['collision'],
                lambda r: -r['collision'],
                lambda r: -r['frozen_hits']):
        path = tuple(min(rows, key=key)['path'])
        if path not in seen:
            candidates.append(path); seen.add(path)
    for google_count in (0, 2, 4, 6, 8):
        row = next((row for row in rows
                    if row['path'].count('site_google') == google_count), None)
        if row is not None:
            path = tuple(row['path'])
            if path not in seen:
                candidates.append(path); seen.add(path)
    for name in RULE_NAMES:
        path = (name,) * LENGTH
        if path in seen or any(name not in cohort for cohort in allowed):
            continue
        candidates.append(path); seen.add(path)
    for j in range(LENGTH):
        for name in ('site_amazon', 'site_netflix'):
            path = tuple(name if k == j else 'site_google' for k in range(LENGTH))
            if path not in seen and name in allowed[j]:
                candidates.append(path); seen.add(path)
    try:
        previous = json.loads(output.read_text(encoding='utf-8'))
        if (previous.get('dataset', {}).get('development_hashes') ==
                dataset['metadata']['development_hashes'] and previous.get('mc_samples') == samples):
            a1_rows = list(previous.get('a1_rows', []))
        else:
            a1_rows = []
    except (OSError, ValueError):
        a1_rows = []
    completed = {tuple(row['path']) for row in a1_rows}
    output.parent.mkdir(parents=True, exist_ok=True)
    for i, path in enumerate(candidates, 1):
        if path in completed:
            continue
        print(f'PCFG A1 finalist {i}/{len(candidates)}: {path}', flush=True)
        a1_rows.append(_score_a1(prepared, path, cfg, samples))
        output.write_text(json.dumps({'status': 'running', 'method': 'exhaustive frozen screen; A1 finalist search',
                                      'dataset': dataset['metadata'], 'budget': BUDGET,
                                      'mc_samples': samples, 'screened_paths': len(rows),
                                      'a1_evaluated_paths': len(a1_rows),
                                      'frozen_top10': rows[:10],
                                      'distribution_top10': sorted(rows, key=lambda r: (r['collision'], r['frozen_hits'], r['path']))[:10],
                                      'modification_top10': sorted(rows, key=lambda r: (r['modification_rate'], r['frozen_hits'], r['path']))[:10],
                                      'a1_rows': sorted(a1_rows, key=lambda r: (r['a1']['hits'], r['collision'])),
                                      'elapsed_seconds': time.time() - started},
                                     ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    result = json.loads(output.read_text(encoding='utf-8'))
    result['status'] = 'candidate_search_complete_global_A1_optimum_unproven'
    result['limitations'] = ('All feasible distinct rules paths were screened with a frozen PCFG. '
                             'PCFG A1 was refit only for listed finalists; its global minimum '
                             'over all paths is not established. Validation data is disjoint '
                             'from final 100,000 registration users.')
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'status': result['status'], 'screened_paths': len(rows),
                      'a1_evaluated_paths': len(a1_rows),
                      'best': result['a1_rows'][0]['path']}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=ROOT / 'reports' / 'dynamic' /
                        'top15_sequence_search.json')
    parser.add_argument('--samples', type=int, default=10000)
    parser.add_argument('--finalists', type=int, default=12)
    args = parser.parse_args()
    search(args.output, samples=args.samples, finalists=args.finalists)
