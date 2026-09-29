"""Causal cohort simulation, distribution feedback and audited open attacks."""
from __future__ import annotations

import dataclasses
import gc
import hashlib
import json
import math
import random
import tempfile
import time
from collections import Counter
from pathlib import Path

from core.corpus import counts_hash
from core.guess_difficulty import guess_difficulty
from core.paired_risk import paired_attack_difference
from core.open_attack import BUDGET_UNIT, GuessRun, evaluate_runs
from core.registration import load_registration
from core.strength_change import evaluate_users
from core.uniformity import concentration
from experiments.dynamic_config import (ROOT, SCHEMA, open_attack_config,
                                        validate_dynamic_config)
from experiments.open_pipeline import AttackEngine, ModelFailure
from policy.dynamic_catalog import initial_policy
from policy.dynamic_controller import prediction_sample, select_next
from policy.open_policy import Rule
from policy.user_response import phrase_vocabulary, simulate_users, weighted_pool


def _response(words, rule, ids, cfg, pool, vocabulary, *, records):
    c = cfg['controller']
    return simulate_users(words, rule, identifiers=ids, pool=pool,
                          vocabulary=vocabulary, seed=cfg['seed'],
                          max_attempts=c['max_attempts'],
                          abandon_probability=c['abandon_probability'],
                          weights=tuple(c['response_weights']), records=records)


def _p90(values):
    if not values:
        return None
    values = sorted(values)
    return values[math.ceil(.9 * len(values)) - 1]


def _development_response(counts, policies, cfg, pool, vocabulary, label):
    occurrences = [word for word, n in sorted(counts.items()) for _ in range(n)]
    random.Random(cfg['seed'] ^ (0xA17 if label == 'train' else 0xB18)).shuffle(occurrences)
    final = Counter()
    for cohort, rule in enumerate(policies):
        positions = range(cohort, len(occurrences), len(policies))
        words = [occurrences[i] for i in positions]
        ids = (f'{label}-{i}' for i in positions)
        response = _response(words, rule, ids, cfg, pool, vocabulary, records=False)
        final.update(response['final'])
    return final


def _project_known_policy(run, rule, targets, budget):
    """A0 filters F's fixed open stream; it never receives target candidates."""
    ranks = {}
    charged = 0
    for word in run.ranks:
        if rule.accepts(word):
            charged += 1
            if word in targets:
                ranks[word] = charged
            if charged >= budget:
                break
    status = ('reached_budget' if charged >= budget else
              'exhausted' if run.stats['stop_reason'] == 'exhausted' else
              'source_truncated')
    stats = {**run.stats, 'charged_count': charged,
             'completed_budget': charged, 'stop_reason': status,
             'exhausted': status == 'exhausted',
             'projection_from_frozen_stream': True}
    return GuessRun(run.model, ranks, {}, stats,
                    {**run.parameters, 'known_policy': rule.summary(),
                     'generation_conditioning': 'fixed frozen stream; eligible-rank projection'})


def _attack_all(cfg, development, arms, targets, pairs, pool, vocabulary, progress):
    attack_cfg = open_attack_config(cfg)
    failures = []
    with tempfile.TemporaryDirectory(prefix='zipfguard_dynamic_') as temporary:
        while True:
            engine = AttackEngine(attack_cfg, temporary,
                                  excluded=[item['model'] for item in failures])
            try:
                if progress:
                    progress('生成冻结攻击候选流')
                frozen = engine.attack(development['train'], development['tuning'],
                                       None, 'F')
                adaptive_dynamic = None
                control_evaluations = {}
                control_difficulties = {}
                paired_evaluations = {}
                for name, arm in arms.items():
                    if name != 'dynamic' and not cfg['controls']['attack']:
                        continue
                    if progress:
                        progress(f'训练并生成 {name} 的自适应攻击候选流')
                    train = _development_response(development['train'],
                                                  arm['policies'], cfg, pool,
                                                  vocabulary, 'train')
                    tuning = _development_response(development['tuning'],
                                                   arm['policies'], cfg, pool,
                                                   vocabulary, 'tuning')
                    run = engine.attack(train, tuning, None, 'F')
                    if name == 'dynamic':
                        adaptive_dynamic = run
                    else:
                        control_evaluations[name] = evaluate_runs(
                            run, targets[name], cfg['budgets'])
                        control_difficulties[name] = guess_difficulty(
                            run, targets[name], cfg['budgets'][-1])
                        paired_evaluations[name] = paired_attack_difference(
                            pairs[name], adaptive_dynamic, run, cfg['budgets'])
                        engine.cache = {key: value for key, value in engine.cache.items()
                                        if value is frozen or value is adaptive_dynamic}
                        engine.fits.clear()
                        engine.raw_cache.clear()
                        del run
                        gc.collect()
                break
            except ModelFailure as exc:
                if cfg['attackers'][exc.model] == 'required':
                    raise RuntimeError('必选攻击器失败：' + exc.reason) from exc
                if any(item['model'] == exc.model for item in failures):
                    raise
                failures.append({'model': exc.model, 'reason': exc.reason,
                                 'excluded_from_entire_comparison': True})
                if progress:
                    progress(exc.reason + '；从整次动态比较中移除后重新计算')
    budgets = cfg['budgets']
    reports = {'F': evaluate_runs(frozen, targets['dynamic'], budgets),
               'A1': evaluate_runs(adaptive_dynamic,
                                   targets['dynamic'], budgets)}
    cohort_a0 = []
    for index, (rule, target) in enumerate(zip(arms['dynamic']['policies'],
                                               arms['dynamic']['cohort_targets']), 1):
        projected = [_project_known_policy(run, rule, target, budgets[-1])
                     for run in frozen]
        cohort_a0.append({'cohort_id': index,
                          'evaluation': evaluate_runs(projected, target, budgets)})
    reports['A0_by_cohort'] = cohort_a0
    reports['A1_by_cohort'] = [
        {'cohort_id': index, 'evaluation': evaluate_runs(adaptive_dynamic,
                                                       target, budgets)}
        for index, target in enumerate(arms['dynamic']['cohort_targets'], 1)]
    reports['controls'] = control_evaluations
    reports['paired_dynamic_minus_control'] = paired_evaluations
    reports['baseline_F'] = evaluate_runs(frozen, targets['baseline'], budgets)
    reports['baseline_completed_F'] = evaluate_runs(
        frozen, targets['baseline_completed'], budgets)
    total_a0 = sum(sum(target.values()) for target in arms['dynamic']['cohort_targets'])
    reports['A0'] = {'minauto': []}
    for point_index, budget in enumerate(budgets):
        cohort_points = [row['evaluation']['minauto'][point_index]
                         for row in cohort_a0]
        hits = sum(point['hits'] for point in cohort_points)
        unresolved = sum(point['unresolved_weight'] for point in cohort_points)
        reports['A0']['minauto'].append({
            'budget': budget, 'hits': hits, 'target_weight': total_a0,
            'unresolved_weight': unresolved,
            'complete': unresolved == 0,
            'rate': hits / total_a0 if total_a0 and unresolved == 0 else None,
            'lower_bound': hits / total_a0 if total_a0 else None,
            'upper_bound': (hits + unresolved) / total_a0 if total_a0 else None})
    reports['guess_counts'] = {
        'dynamic_F': guess_difficulty(frozen, targets['dynamic'], budgets[-1]),
        'dynamic_A1': guess_difficulty(adaptive_dynamic, targets['dynamic'], budgets[-1]),
        'baseline_F': guess_difficulty(frozen, targets['baseline'], budgets[-1]),
        'baseline_completed_F': guess_difficulty(
            frozen, targets['baseline_completed'], budgets[-1]),
        'controls_A1': control_difficulties}
    return reports, adaptive_dynamic, failures


def _control_arms(cohorts, cfg, development, pool, vocabulary, preview):
    if not cfg['controls']['run']:
        return {}
    length8 = initial_policy()
    complex8 = Rule('complex-8', min_length=8, classes=2, origin='common')
    selected = length8
    # Static rule selection is frozen before any registration cohort is read.
    for _ in range(3):
        nxt, decision = select_next(selected, Counter(),
                                    development_train=development['train'],
                                    preview_words=preview, pool=pool,
                                    vocabulary=vocabulary, seed=cfg['seed'],
                                    config=cfg['controller'])
        if decision['status'] == 'hold':
            break
        selected = nxt
    schedule = []
    for t in range(len(cohorts)):
        length = 8 if t < len(cohorts) / 3 else 10 if t < 2 * len(cohorts) / 3 else 12
        schedule.append(Rule(f'fixed-schedule-length-{length}', min_length=length,
                             origin='common'))
    return {'fixed_length8': [length8] * len(cohorts),
            'fixed_complexity': [complex8] * len(cohorts),
            'static_selected': [selected] * len(cohorts),
            'fixed_schedule': schedule}


def run_dynamic_pipeline(config, *, dataset=None, output_dir=None, progress=None):
    cfg = validate_dynamic_config(config)
    started = time.perf_counter()
    if dataset is None:
        d = cfg['data']
        path = Path(d['path'])
        if not path.is_absolute():
            path = ROOT / path
        dataset = load_registration(path, source_format=d['format'],
                                    encoding=d['encoding'], users=d['users'],
                                    development=d['development'], seed=cfg['seed'],
                                    cohort_size=d['cohort_size'], progress=progress)
    cohorts = dataset['cohorts']
    development = dataset['development']
    if not cohorts or any(not batch for batch in cohorts):
        raise ValueError('注册批次不能为空')
    if sum(map(len, cohorts)) != cfg['data']['users']:
        raise ValueError('注册批次人数与配置不一致')
    pool = weighted_pool(development['train'])
    vocabulary = phrase_vocabulary(development['train'])
    preview = prediction_sample(development['validation'],
                                cfg['controller']['preview_size'], cfg['seed'])
    if not preview:
        raise ValueError('开发验证样本不能为空')
    rule = initial_policy()
    history, original_history, matched_original_history = Counter(), Counter(), Counter()
    records, dynamic_rows, dynamic_policies, dynamic_targets = [], [], [], []
    decisions = []
    offset = 0
    prior = Rule('baseline', origin='common')
    first_cohort_cost = None
    for index, words in enumerate(cohorts, 1):
        if progress:
            progress(f'注册批次 {index}/{len(cohorts)}：{len(words)} 名模拟用户')
        identifiers = range(offset, offset + len(words))
        actual = _response(words, rule, identifiers, cfg, pool, vocabulary,
                           records=True)
        actual['summary']['p90_extra_attempts'] = _p90(
            [row['attempts'] for row in actual['records']])
        actual['summary']['p90_edit_distance_modified'] = _p90(
            [row['edit_distance'] for row in actual['records'] if row['modified']])
        for row in actual['records']:
            row.update(cohort_id=index, policy_id=rule.name)
        records.extend(actual['records'])
        before = (Counter(words) if index == 1 else
                  _response(words, prior, range(offset, offset + len(words)), cfg,
                            pool, vocabulary, records=False)['final'])
        original_history.update(words)
        matched_original_history.update(actual['completed_original'])
        history.update(actual['final'])
        dynamic_policies.append(rule)
        dynamic_targets.append(actual['final'])
        dynamic_rows.append({'cohort_id': index, 'start_user': offset + 1,
                             'end_user': offset + len(words),
                             'policy': rule.summary(),
                             'step_before_policy': prior.summary(),
                             'step_before': concentration(before),
                             'step_after': concentration(actual['final']),
                             'baseline_cohort': concentration(words),
                             'baseline_completed_cohort': concentration(actual['completed_original']),
                             'cumulative': concentration(history),
                             'baseline_cumulative': concentration(original_history),
                             'baseline_completed_cumulative': concentration(matched_original_history),
                             'response': actual['summary'],
                             'policy_changed': rule != prior})
        if index == 1:
            first_cohort_cost = actual['summary']['modification_rate']
        offset += len(words)
        prior = rule
        if index < len(cohorts):
            next_rule, decision = select_next(rule, history,
                                              development_train=development['train'],
                                              preview_words=preview, pool=pool,
                                              vocabulary=vocabulary, seed=cfg['seed'],
                                              config=cfg['controller'],
                                              first_cohort_cost=first_cohort_cost)
            decision['after_cohort'] = index
            decision['next_cohort'] = index + 1
            decisions.append(decision)
            rule = next_rule
    arms = {'dynamic': {'policies': dynamic_policies,
                        'cohort_targets': dynamic_targets}}
    targets = {'dynamic': history, 'baseline': original_history,
               'baseline_completed': matched_original_history}
    controls = {}
    paired_users = {}
    for name, policies in _control_arms(cohorts, cfg, development, pool,
                                        vocabulary, preview).items():
        cumulative, matched_control = Counter(), Counter()
        timeline, per_batch = [], []
        common = []
        offset = 0
        mod = completed = 0
        for index, (words, policy) in enumerate(zip(cohorts, policies), 1):
            response = _response(words, policy,
                                 range(offset, offset + len(words)), cfg, pool,
                                 vocabulary, records=False)
            for dynamic_record, control_password in zip(
                    records[offset:offset + len(words)], response['final_by_user']):
                if dynamic_record['final'] is not None and control_password is not None:
                    common.append((dynamic_record['final'], control_password))
            offset += len(words)
            cumulative.update(response['final'])
            matched_control.update(response['completed_original'])
            per_batch.append(response['final'])
            mod += response['summary']['modified_users']
            completed += response['summary']['completed_users']
            timeline.append({'cohort_id': index,
                             'cumulative': concentration(cumulative),
                             'response': response['summary']})
        controls[name] = {'timeline': timeline, 'final': concentration(cumulative),
                          'paired_completed_baseline': concentration(matched_control),
                          'joint_completed_users_with_dynamic': len(common),
                          'modified_users': mod, 'completed_users': completed,
                          'policy_schedule': [p.summary() for p in policies]}
        arms[name] = {'policies': policies, 'cohort_targets': per_batch}
        paired_users[name] = common
        targets[name] = cumulative
    if progress:
        progress('在完整注册轨迹上评价冻结、已知策略与自适应猜测')
    attack, reference_runs, failures = _attack_all(
        cfg, development, arms, targets, paired_users, pool, vocabulary, progress)
    manifest = {'config_sha256': hashlib.sha256(json.dumps(
        cfg, ensure_ascii=False, sort_keys=True).encode()).hexdigest(),
                'source_sha256': dataset['metadata'].get('source_sha256'),
                'registration_order_sha256': dataset['metadata']['registration_order_sha256'],
                'development_hashes': dataset['metadata']['development_hashes'],
                'dynamic_final_counts_sha256': counts_hash(history),
                'implementation_sha256': {
                    path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
                    for path in (
                        'core/corpus.py', 'core/registration.py',
                        'core/uniformity.py', 'core/open_attack.py',
                        'core/guess_difficulty.py', 'core/paired_risk.py',
                        'core/strength_change.py', 'policy/open_policy.py',
                        'policy/user_response.py', 'policy/dynamic_catalog.py',
                        'policy/dynamic_controller.py',
                        'experiments/open_pipeline.py',
                        'experiments/dynamic_pipeline.py',
                        'experiments/dynamic_config.py')}}
    run_id = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()[:16]
    output = Path(output_dir) if output_dir else ROOT / 'reports' / 'dynamic' / run_id
    output.mkdir(parents=True, exist_ok=True)
    private_path = output / 'user_outcomes_private.jsonl'
    statuses = Counter()
    cohort_strength = {i: Counter() for i in range(1, len(cohorts) + 1)}
    gain_histogram = Counter()
    with private_path.open('w', encoding='utf-8') as stream:
        for raw, measured in zip(records, evaluate_users(records, reference_runs,
                                                         cfg['budgets'][-1])):
            measured['original_password'] = raw['original']
            measured['final_password'] = raw['final']
            measured['initially_accepted'] = raw['initially_accepted']
            statuses[measured['strength_change']['status']] += 1
            cohort_strength[raw['cohort_id']][measured['strength_change']['status']] += 1
            gain = measured['strength_change']['log2_gain']
            if gain is not None and measured['strength_change']['status'] == 'exact':
                gain_histogram[str(max(-10, min(10, math.floor(gain))))] += 1
            stream.write(json.dumps(measured, ensure_ascii=False,
                                    allow_nan=False) + '\n')
    private_sha = hashlib.sha256(private_path.read_bytes()).hexdigest()
    public = {
        'schema_version': SCHEMA + '-result',
        'dataset': dataset['metadata'], 'config': cfg, 'manifest': manifest,
        'budget_unit': BUDGET_UNIT, 'cohorts': dynamic_rows,
        'decisions': decisions,
        'final_distribution': concentration(history),
        'baseline_distribution': concentration(original_history),
        'baseline_completed_distribution': concentration(matched_original_history),
        'controls': controls, 'attacks': attack,
        'user_strength': {'budget': cfg['budgets'][-1],
                          'status_counts': dict(statuses),
                          'status_by_cohort': {str(k): dict(v) for k, v in cohort_strength.items()},
                          'exact_log2_gain_histogram': dict(gain_histogram),
                          'local_private_file': str(private_path.resolve()),
                          'private_file_sha256': private_sha,
                          'interpretation': '同一个不按新策略过滤的自适应攻击流；未命中为截断界限'},
        'metadata': {'run_id': run_id, 'runtime_seconds': round(
            time.perf_counter() - started, 3),
                     'participating_attackers': [r.model for r in reference_runs],
                     'optional_model_failures': failures,
                     'registration_order': 'seeded simulated order; not real timestamps',
                     'A0_method': 'frozen stream projected to each cohort policy; no policy-conditioned generation',
                     'A1_method': 'development response pooled across actual policy versions; global attacker',
                     'budget_interpretation': 'per-model unique eligible checks; Min_auto is model union',
                     'limitations': ['历史注册聚合可用于下一批策略选择；未来批次不可见。',
                                     '同源出现记录不代表经核实的独立账户。',
                                     '用户修改行为是显式模拟，不代表真实用户研究。']}}
    report_path = output / 'report.json'
    report_path.write_text(json.dumps(public, ensure_ascii=False, indent=2,
                                      allow_nan=False) + '\n', encoding='utf-8')
    return public
