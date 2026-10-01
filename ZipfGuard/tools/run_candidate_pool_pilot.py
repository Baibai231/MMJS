"""Exhaustively evaluate the fifteen named policies over ten cohorts.

Every cohort selects one of the same fifteen policy IDs. Structural paths
that violate a monotone requirement are excluded before simulation. A prefix
whose observed cost violates a hard limit rejects every extension, with its
entire structural suffix count recorded. No beam pruning or sampling of paths.
"""
from __future__ import annotations

import argparse
import gc
import hashlib
import json
import os
import sys
import tempfile
import time
from collections import Counter
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.open_attack import evaluate_runs
from core.registration import load_registration
from core.uniformity import concentration
from experiments.dynamic_config import load_dynamic_config, open_attack_config
from experiments.dynamic_pipeline import _development_response, _response
from experiments.open_pipeline import AttackEngine
from policy.open_policy import Rule
from policy.user_response import phrase_vocabulary, weighted_pool
from tools.audit_candidate_search_space import SEEDS, restricted_preset_transitions


def settings():
    cfg = load_dynamic_config('dynamic_full')
    cfg['controller']['include_pattern_rules'] = True
    return cfg


def policy(index, ranked, history, previous=None):
    length, classes, size, use_history, pattern_bits = SEEDS[index]
    blocked = set(ranked[:size])
    if use_history:
        if previous is not None:
            blocked.update(previous.blocklist)
        head = sorted(history, key=lambda word: (-history[word], word))
        blocked.update(word for word in head[:100] if history[word] >= 2)
    deny = tuple(name for bit, name in ((1, 'repeated'), (2, 'sequential_digits'))
                 if pattern_bits & bit)
    return Rule(f'S{index+1:02}', min_length=length, classes=classes,
                deny=deny, blocklist=frozenset(blocked), origin='candidate-pool-pilot')


def paths_and_suffixes(cohorts):
    positions = {state: i for i, state in enumerate(SEEDS)}
    edges = restricted_preset_transitions()
    successors = {i: tuple(positions[other] for other in sorted(edges[state]))
                  for i, state in enumerate(SEEDS)}

    @lru_cache(None)
    def suffixes(index, remaining):
        if not remaining:
            return 1
        return sum(suffixes(nxt, remaining - 1) for nxt in successors[index])

    return successors, suffixes


def prepare(output):
    cfg = settings()
    data_config = cfg['data']
    path = Path(data_config['path'])
    if not path.is_absolute():
        path = ROOT / path
    data = load_registration(path, source_format=data_config['format'],
                             encoding=data_config['encoding'], users=data_config['users'],
                             development=data_config['development'], seed=cfg['seed'],
                             cohort_size=data_config['cohort_size'], progress=print)
    manifest = {
        'protocol': 'exact-fifteen-policy-timelines-v1',
        'seed': cfg['seed'], 'dataset': data['metadata'],
        'settings_sha256': hashlib.sha256(json.dumps(cfg, sort_keys=True).encode()).hexdigest(),
        'source_sha256': data['metadata']['source_sha256'],
        'candidate_ids': [f'S{i+1:02}' for i in range(len(SEEDS))],
        'search_semantics': 'ten cohorts, first S01, later choose one of same fifteen monotone policies',
        'history_semantics': 'historical blacklist refreshes after each completed cohort once active',
    }
    saved = output / 'manifest.json'
    if saved.exists():
        if json.loads(saved.read_text(encoding='utf-8')) != manifest:
            raise RuntimeError('Existing checkpoint uses different data or settings')
    else:
        saved.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return cfg, data


def score_paths(cfg, data, output):
    cohorts = data['cohorts']
    development = data['development']['train']
    ranked = tuple(sorted(development, key=lambda word: (-development[word], word)))
    pool, vocabulary = weighted_pool(development), phrase_vocabulary(development)
    successors, suffixes = paths_and_suffixes(len(cohorts))
    total = suffixes(0, len(cohorts) - 1)
    cost = cfg['controller']
    file = output / 'registered_paths.jsonl'
    temporary_file = output / 'registered_paths.jsonl.part'
    if file.exists() and (output / 'registration_audit.json').exists():
        raise RuntimeError('Registration enumeration already exists; resume at attack stage')
    memo = {}
    counts = Counter()
    started = time.monotonic()

    def simulate(index, cohort, rule):
        key = (cohort, index)
        if not SEEDS[index][3] and key in memo:
            return memo[key]
        words = cohorts[cohort]
        result = _response(words, rule, range(cohort * cfg['data']['cohort_size'],
                                              cohort * cfg['data']['cohort_size'] + len(words)),
                           cfg, pool, vocabulary, records=False)
        if not SEEDS[index][3]:
            memo[key] = result
        return result

    def visit(path, history, modified, first_cost, previous_rule, summaries, rules):
        depth = len(path)
        if depth == len(cohorts):
            n = sum(history.values())
            row = {'path': [f'S{i+1:02}' for i in path],
                   'registered_users': n,
                   'modified_users': modified,
                   'modification_rate': modified / cfg['data']['users'],
                   'collision_probability': concentration(history)['collision_probability'],
                   'batch_costs': summaries,
                   'policy_summaries': [rule.summary() for rule in rules]}
            stream.write(json.dumps(row, ensure_ascii=False) + '\n')
            counts['feasible_complete_paths'] += 1
            if counts['feasible_complete_paths'] % 25 == 0:
                stream.flush()
                print('注册枚举', dict(counts), '秒', round(time.monotonic() - started, 1), flush=True)
            return
        for index in ([0] if not path else successors[path[-1]]):
            rule = policy(index, ranked, history, previous_rule)
            current = simulate(index, depth, rule)
            summary = current['summary']
            rate = summary['modification_rate']
            if depth == 0:
                initial_rate = rate
            else:
                initial_rate = first_cost
            if previous_rule is None or rule == previous_rule:
                hold_rate = rate
            else:
                hold_rate = _response(cohorts[depth], previous_rule,
                                      range(depth * cfg['data']['cohort_size'],
                                            depth * cfg['data']['cohort_size'] + len(cohorts[depth])),
                                      cfg, pool, vocabulary, records=False)['summary']['modification_rate']
            violations = []
            if summary['pending_users']:
                violations.append('pending')
            if rate > cost['max_modification_rate']:
                violations.append('max_modification')
            if rate - hold_rate > cost['max_incremental_modification']:
                violations.append('max_incremental')
            if rate - initial_rate > cost['max_late_cost_increase']:
                violations.append('max_late')
            if violations:
                eliminated = suffixes(index, len(cohorts) - depth - 1)
                counts['cost_excluded_complete_paths'] += eliminated
                for reason in violations:
                    counts['reason_' + reason] += eliminated
                continue
            counts['simulated_prefixes'] += 1
            new_history = history + current['final']
            visit((*path, index), new_history,
                  modified + summary['modified_users'], initial_rate,
                  rule, (*summaries, rate), (*rules, rule))

    with temporary_file.open('w', encoding='utf-8') as stream:
        visit((), Counter(), 0, None, None, (), ())
    assert counts['feasible_complete_paths'] + counts['cost_excluded_complete_paths'] == total
    result = {'structural_paths': total, **counts,
              'runtime_seconds': round(time.monotonic() - started, 3),
              'meaning': 'All structural paths accounted for; only feasible paths simulated to completion'}
    os.replace(temporary_file, file)
    (output / 'registration_audit.json').write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print('注册穷举完成', result, flush=True)
    return result


def replay(path, cfg, data, pool, vocabulary):
    history = Counter()
    policies = []
    development = data['development']['train']
    ranked = tuple(sorted(development, key=lambda word: (-development[word], word)))
    for cohort, name in enumerate(path):
        index = int(name[1:]) - 1
        rule = policy(index, ranked, history,
                      policies[-1] if policies else None)
        response = _response(data['cohorts'][cohort], rule,
                             range(cohort * cfg['data']['cohort_size'],
                                   cohort * cfg['data']['cohort_size'] + len(data['cohorts'][cohort])),
                             cfg, pool, vocabulary, records=False)
        if response['summary']['pending_users']:
            raise RuntimeError('Feasible path became pending on replay')
        history.update(response['final'])
        policies.append(rule)
    return history, policies


def attack_all(cfg, data, output, shard=None, shards=1):
    path_file = output / 'registered_paths.jsonl'
    paths = [json.loads(line) for line in path_file.read_text(encoding='utf-8').splitlines()]
    results_file = (output / 'attacked_paths.jsonl' if shard is None else
                    output / f'attacked_paths_shard_{shard:02d}.jsonl')
    previous = {}
    checkpoints = ([output / 'attacked_paths.jsonl', *sorted(output.glob('attacked_paths_shard_*.jsonl'))]
                   if shard is None else [output / 'attacked_paths.jsonl', results_file])
    for checkpoint in checkpoints:
        if checkpoint.exists():
            for line in checkpoint.read_text(encoding='utf-8').splitlines():
                row = json.loads(line)
                previous[tuple(row['path'])] = row
    pool = weighted_pool(data['development']['train'])
    vocabulary = phrase_vocabulary(data['development']['train'])
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix='zipfguard_candidate_pool_') as temporary:
        engine = AttackEngine(open_attack_config(cfg), temporary)
        with results_file.open('a', encoding='utf-8') as stream:
            for i, row in enumerate(paths, 1):
                if shard is not None and (i - 1) % shards != shard:
                    continue
                if tuple(row['path']) in previous:
                    continue
                target, policies = replay(row['path'], cfg, data, pool, vocabulary)
                if sum(target.values()) != row['registered_users']:
                    raise RuntimeError('Replay user count changed')
                actual_collision = concentration(target)['collision_probability']
                if abs(actual_collision - row['collision_probability']) > 1e-15:
                    raise RuntimeError('Replay distribution changed')
                train = _development_response(data['development']['train'], policies,
                                              cfg, pool, vocabulary, 'train')
                tuning = _development_response(data['development']['tuning'], policies,
                                               cfg, pool, vocabulary, 'tuning')
                runs = engine.attack(train, tuning, None, 'F')
                evaluation = evaluate_runs(runs, target, cfg['budgets'])
                attack = {'path': row['path'], 'A1_minauto': evaluation['minauto'],
                          'models': [{'name': run.model,
                                      'charged_count': run.stats.get('charged_count'),
                                      'stop_reason': run.stats.get('stop_reason')}
                                     for run in runs]}
                stream.write(json.dumps(attack, ensure_ascii=False) + '\n')
                stream.flush()
                engine.cache.clear()
                engine.fits.clear()
                engine.raw_cache.clear()
                gc.collect()
                if i % 10 == 0 or i == len(paths):
                    print('百万预算攻击评价', i, '/', len(paths),
                          '本次秒', round(time.monotonic() - started, 1), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--registration-only', action='store_true')
    parser.add_argument('--attack-shard', type=int)
    parser.add_argument('--attack-shards', type=int, default=1)
    args = parser.parse_args()
    if args.attack_shards < 1 or (args.attack_shard is not None and
                                 not 0 <= args.attack_shard < args.attack_shards):
        parser.error('--attack-shard must be in [0, --attack-shards)')
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    cfg, data = prepare(output)
    if not (output / 'registration_audit.json').exists():
        score_paths(cfg, data, output)
    if not args.registration_only:
        attack_all(cfg, data, output, args.attack_shard, args.attack_shards)


if __name__ == '__main__':
    main()
