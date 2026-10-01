"""Evaluate every catalog template on development validation users only.

This registration screen does not select a shortlist and is not a dynamic
timeline search. Attack evaluation and stability checks are required before
claiming that any shortlist represents the catalog.
"""
from __future__ import annotations

import argparse
import gc
import hashlib
import json
import random
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.registration import load_registration
from core.corpus import counts_hash
from core.open_attack import evaluate_runs
from experiments.dynamic_config import open_attack_config
from experiments.dynamic_pipeline import _development_response, _response
from experiments.open_pipeline import AttackEngine
from policy.open_policy import Rule
from policy.user_response import phrase_vocabulary, weighted_pool
from tools.run_candidate_pool_pilot import settings


def profile_rule(profile, ranked, history, historical_blocklist):
    blocked = set(ranked[:profile['development_top_blocklist']])
    if profile['historical_hotspot_blocklist'] != 'off':
        head = sorted(history, key=lambda word: (-history[word], word))
        historical_blocklist.update(word for word in head[:100] if history[word] >= 2)
        blocked.update(historical_blocklist)
    return Rule(profile['id'], min_length=profile['min_length'],
                classes=profile['required_classes'], deny=tuple(profile['deny_features']),
                blocklist=frozenset(blocked), origin='development-catalog-screen')


def screen_profile(profile, validation, ranked, cfg, pool, vocabulary, *, details=False, identifiers=None):
    if identifiers is not None and len(identifiers) != len(validation):
        raise ValueError('Each validation user requires a stable identifier')
    history = Counter()
    historical_blocklist = set()
    modified = pending = hidden_rejections = 0
    stages = []
    policies = []
    size = (len(validation) + 9) // 10
    for start in range(0, len(validation), size):
        words = validation[start:start + size]
        rule = profile_rule(profile, ranked, history, historical_blocklist)
        policies.append(rule)
        ids = (identifiers[start:start + len(words)] if identifiers is not None else
               (f'catalog-validation-{i}' for i in range(start, start + len(words))))
        result = _response(words, rule, ids,
                           cfg, pool, vocabulary, records=False)
        summary = result['summary']
        modified += summary['modified_users']
        pending += summary['pending_users']
        hidden_rejections += summary['hidden_rejections']
        history.update(result['final'])
        n = sum(history.values())
        collision = sum(count * (count - 1) for count in history.values())
        stages.append({'users_seen': start + len(words),
                       'registered_users': n,
                       'modification_rate': summary['modification_rate'],
                       'collision_probability': collision / (n * (n - 1)) if n > 1 and not pending else None})
    row = {'profile_id': profile['id'], 'profile': profile,
            'status': 'complete' if not pending else 'pending_generation',
            'validation_users': len(validation), 'registered_users': sum(history.values()),
            'modified_users': modified, 'modification_rate': modified / len(validation),
            'pending_users': pending, 'hidden_rejections': hidden_rejections,
            'collision_probability': stages[-1]['collision_probability'], 'stages': stages,
            'attack_evaluated': False, 'shortlist_selected': False}
    return (row, history, policies) if details else row


def attack_profiles(profiles, validation, ranked, cfg, development, pool, vocabulary, output, shard, shards):
    registered = {}
    for path in sorted(output.glob('registration_shard_*.jsonl')):
        for line in path.read_text(encoding='utf-8').splitlines():
            row = json.loads(line)
            if row['profile_id'] in registered:
                raise RuntimeError('Duplicate registration checkpoint')
            registered[row['profile_id']] = row
    if set(registered) != {p['id'] for p in profiles}:
        raise RuntimeError('All catalog registration outcomes must be available first')
    checkpoint = output / f'attack_shard_{shard:02}.jsonl'
    previous = {}
    equivalents = {}
    for path in sorted(output.glob('attack_shard_*.jsonl')):
        text = path.read_text(encoding='utf-8')
        lines = text.splitlines() if text.endswith('\n') else text.splitlines()[:-1]
        for line in lines:
            row = json.loads(line)
            previous[row['profile_id']] = row
            if row.get('status') == 'complete':
                equivalents[row['evaluation_hash']] = row
    started = time.monotonic()
    evaluated = 0
    with tempfile.TemporaryDirectory(prefix='zipfguard_catalog_') as temporary:
        engine = AttackEngine(open_attack_config(cfg), temporary)
        with checkpoint.open('a', encoding='utf-8') as stream:
            for index, profile in enumerate(profiles):
                if index % shards != shard or profile['id'] in previous:
                    continue
                row, target, policies = screen_profile(profile, validation, ranked, cfg, pool, vocabulary, details=True)
                if row != registered[profile['id']]:
                    raise RuntimeError('Registration replay changed')
                if row['pending_users']:
                    raise RuntimeError('Pending users must be resolved before attack selection')
                train = _development_response(development['train'], policies, cfg, pool, vocabulary, 'train')
                tuning = _development_response(development['tuning'], policies, cfg, pool, vocabulary, 'tuning')
                signature = hashlib.sha256('|'.join(map(counts_hash, (target, train, tuning))).encode()).hexdigest()
                if signature in equivalents:
                    result = {**equivalents[signature], 'profile_id': profile['id'],
                              'reused_profile_id': equivalents[signature]['profile_id']}
                else:
                    runs = engine.attack(train, tuning, None, 'F')
                    evaluation = evaluate_runs(runs, target, cfg['budgets'])
                    models = [{'name': run.model, 'charged_count': run.stats.get('charged_count'),
                               'stop_reason': run.stats.get('stop_reason')} for run in runs]
                    complete = (all(p['complete'] for p in evaluation['minauto']) and
                                all(m['stop_reason'] in ('reached_budget', 'exhausted') for m in models))
                    result = {'profile_id': profile['id'], 'evaluation_hash': signature,
                              'status': 'complete' if complete else 'incomplete_attack',
                              'A1_minauto': evaluation['minauto'], 'models': models,
                              'reused_profile_id': None, 'shortlist_selected': False}
                    if complete:
                        equivalents[signature] = result
                stream.write(json.dumps(result, ensure_ascii=False) + '\n')
                stream.flush()
                evaluated += 1
                engine.cache.clear()
                engine.fits.clear()
                engine.raw_cache.clear()
                gc.collect()
                if evaluated % 5 == 0:
                    print(f'分片 {shard}: 已新增 {evaluated} 条百万预算开发攻击评价，耗时 {time.monotonic() - started:.1f} 秒', flush=True)
    print(json.dumps({'shard': shard, 'new_attacks': evaluated, 'shortlist_selected': False}), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--shard', type=int, default=0)
    parser.add_argument('--shards', type=int, default=1)
    parser.add_argument('--attack', action='store_true')
    args = parser.parse_args()
    if args.shards < 1 or not 0 <= args.shard < args.shards:
        parser.error('Invalid shard index')
    cfg = settings()
    catalog_bytes = (ROOT / 'docs' / 'policy_candidate_profiles.json').read_bytes()
    profiles = json.loads(catalog_bytes)['profiles']
    assert len(profiles) == len({p['id'] for p in profiles}) == 960
    source = Path(cfg['data']['path'])
    if not source.is_absolute():
        source = ROOT / source
    d = cfg['data']
    data = load_registration(source, source_format=d['format'], encoding=d['encoding'],
                             users=d['users'], development=d['development'], seed=cfg['seed'],
                             cohort_size=d['cohort_size'], progress=print)
    # Reserved registration cohorts are never passed to a candidate evaluation.
    development = data['development']
    validation = [word for word, count in sorted(development['validation'].items())
                  for _ in range(count)]
    random.Random(cfg['seed'] ^ 0xCA7106).shuffle(validation)
    train = development['train']
    ranked = tuple(sorted(train, key=lambda word: (-train[word], word)))
    pool, vocabulary = weighted_pool(train), phrase_vocabulary(train)
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    manifest = {
        'protocol': 'development-catalog-registration-screen-v1',
        'catalog_size': len(profiles), 'catalog_sha256': hashlib.sha256(catalog_bytes).hexdigest(),
        'dataset': data['metadata'], 'evaluated_split': 'development.validation',
        'reserved_registration_users_used': 0, 'validation_users': len(validation),
        'config_sha256': hashlib.sha256(json.dumps(cfg, sort_keys=True).encode()).hexdigest(),
        'scenario': 'each template held for ten validation cohorts; historical blocklist updates from earlier validation cohorts only',
        'cost_filter': False, 'attack_evaluated': False, 'shortlist_selected': False,
        'shards': args.shards,
    }
    manifest_file = output / f'manifest_{args.shard:02}.json'
    if manifest_file.exists():
        if json.loads(manifest_file.read_text(encoding='utf-8')) != manifest:
            raise RuntimeError('Checkpoint provenance changed')
    else:
        manifest_file.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    if args.attack:
        attack_profiles(profiles, validation, ranked, cfg, development, pool, vocabulary,
                        output, args.shard, args.shards)
        return
    checkpoint = output / f'registration_shard_{args.shard:02}.jsonl'
    previous = {json.loads(line)['profile_id'] for line in checkpoint.read_text(encoding='utf-8').splitlines()} if checkpoint.exists() else set()
    started = time.monotonic()
    evaluated = 0
    with checkpoint.open('a', encoding='utf-8') as stream:
        for index, profile in enumerate(profiles):
            if index % args.shards != args.shard or profile['id'] in previous:
                continue
            result = screen_profile(profile, validation, ranked, cfg, pool, vocabulary)
            stream.write(json.dumps(result, ensure_ascii=False) + '\n')
            stream.flush()
            evaluated += 1
            if evaluated % 20 == 0:
                print(f'分片 {args.shard}: 已新增 {evaluated} 条开发集注册评价，耗时 {time.monotonic() - started:.1f} 秒', flush=True)
    print(json.dumps({'shard': args.shard, 'new_profiles_evaluated': evaluated,
                      'attack_evaluated': False, 'shortlist_selected': False}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
