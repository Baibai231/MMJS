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
from core.open_attack import BUDGET_UNIT, GuessRun, evaluate_runs, points_for_runs
from core.registration import load_registration
from core.strength_change import evaluate_users
from core.uniformity import concentration
from experiments.dynamic_config import (ROOT, SCHEMA, COMPARISON_PROTOCOL, open_attack_config,
                                        validate_dynamic_config)
from experiments.open_pipeline import AttackEngine, ModelFailure
from policy.dynamic_catalog import initial_policy
from policy.dynamic_controller import prediction_sample, select_next
from policy.open_policy import Rule
from policy.user_response import RESPONSE_PROTOCOL, phrase_vocabulary, simulate_users, weighted_pool


def _response(words, rule, ids, cfg, pool, vocabulary, *, records):
    c = cfg['controller']
    return simulate_users(words, rule, identifiers=ids, pool=pool,
                          vocabulary=vocabulary, seed=cfg['seed'],
                          retry_report_after=c['retry_report_after'],
                          candidate_limit=c['candidate_limit'],
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
        if response['summary']['pending_users']:
            raise RuntimeError(
                f"攻击训练样本 {label} 有 {response['summary']['pending_users']} 条仍待生成合规口令；"
                '请调整候选生成或计算上限后重试，不能静默删除这些样本。')
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


def _known_policy_attack(frozen, policies, cohort_targets, budgets, stream_provider=None):
    """Evaluate each account under its registration rule and aggregate users."""
    cohorts = []
    for index, (rule, target) in enumerate(zip(policies, cohort_targets), 1):
        projected = (stream_provider(rule) if stream_provider else
                     [_project_known_policy(run, rule, target, budgets[-1]) for run in frozen])
        cohorts.append({'cohort_id': index, 'evaluation': evaluate_runs(projected, target, budgets)})
    total = sum(sum(target.values()) for target in cohort_targets)
    points = []
    for i, budget in enumerate(budgets):
        parts = [row['evaluation']['minauto'][i] for row in cohorts]
        hits = sum(p['hits'] for p in parts)
        unresolved = sum(p['unresolved_weight'] for p in parts)
        points.append({'budget': budget, 'hits': hits, 'target_weight': total,
                       'unresolved_weight': unresolved, 'complete': unresolved == 0,
                       'rate': hits / total if total and unresolved == 0 else None,
                       'lower_bound': hits / total if total else None,
                       'upper_bound': (hits + unresolved) / total if total else None})
    return {'minauto': points}, cohorts


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
                known_by_strategy = {}
                if 'research_models' in cfg:
                    # Read beyond the F budget from the same frozen candidate
                    # source. Filtering its first K guesses alone is not K
                    # policy-eligible guesses. Targets only enter evaluation.
                    for name, arm in arms.items():
                        known_by_strategy[name] = _known_policy_attack(
                            frozen, arm['policies'], arm['cohort_targets'], cfg['budgets'],
                            stream_provider=lambda rule: engine.attack(
                                development['train'], development['tuning'], rule, 'A0'))
                # One frozen stream, same prefix users, no future policy or user
                # data in the attacker. This is explicitly F, not prefix A1.
                stage_attacks = []
                cumulative_targets = {name: Counter() for name in (*arms, 'baseline')}
                for index, baseline_batch in enumerate(arms['dynamic']['baseline_targets']):
                    cumulative_targets['baseline'].update(baseline_batch)
                    for name, arm in arms.items():
                        cumulative_targets[name].update(arm['cohort_targets'][index])
                    stage_attacks.append({
                        'cohort_id': index + 1,
                        'users': sum(cumulative_targets['baseline'].values()),
                        'evaluations': {name: {'minauto': points_for_runs(frozen, target, cfg['budgets'])}
                                        for name, target in cumulative_targets.items()}})
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
    reports['registration_stages_F'] = {
        'method': 'same frozen streams trained on original development data; cumulative prefix users only',
        'stages': stage_attacks}
    if 'dynamic' in known_by_strategy:
        reports['A0'], reports['A0_by_cohort'] = known_by_strategy['dynamic']
    else:
        reports['A0'], reports['A0_by_cohort'] = _known_policy_attack(
            frozen, arms['dynamic']['policies'], arms['dynamic']['cohort_targets'], budgets)
    reports['A1_by_cohort'] = [
        {'cohort_id': index, 'evaluation': evaluate_runs(adaptive_dynamic,
                                                       target, budgets)}
        for index, target in enumerate(arms['dynamic']['cohort_targets'], 1)]
    reports['controls'] = control_evaluations
    reports['paired_dynamic_minus_control'] = paired_evaluations
    reports['baseline_F'] = evaluate_runs(frozen, targets['baseline'], budgets)
    reports['baseline_completed_F'] = evaluate_runs(
        frozen, targets['baseline_completed'], budgets)
    # Without a policy, no candidates are filtered and development passwords
    # are unchanged. All three levels therefore use the identical frozen stream.
    baseline = reports['baseline_F']
    reports['by_strategy'] = {
        'dynamic': {level: reports[level] for level in ('F', 'A0', 'A1')},
        'baseline': {level: baseline for level in ('F', 'A0', 'A1')},
    }
    reports['baseline_attack_identity'] = 'no rule filtering or development response; F = A0 = A1'
    reports['controls_A0_by_cohort'] = {}
    for name, arm in arms.items():
        if name == 'dynamic':
            continue
        if name in known_by_strategy:
            known, cohort_known = known_by_strategy[name]
        else:
            known, cohort_known = _known_policy_attack(frozen, arm['policies'], arm['cohort_targets'], budgets)
        reports['controls_A0_by_cohort'][name] = cohort_known
        levels = {'F': evaluate_runs(frozen, targets[name], budgets), 'A0': known}
        if name in control_evaluations:
            levels['A1'] = control_evaluations[name]
        reports['by_strategy'][name] = levels
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
    if cfg['controls']['preset'] == 'top15':
        from policy.site_catalog import site_rules
        return {name: [rule] * len(cohorts) for name, rule in
                site_rules(cfg['controller'].get('include_recommendations', False)).items()}
    # The preset is fixed before registration and also starts the dynamic arm.
    return {'fixed_preset': [initial_policy()] * len(cohorts)}


def _cost_audit(timeline, config, predicted=None):
    first = timeline[0]['response']['modification_rate']
    rows = []
    for index, row in enumerate(timeline):
        response = row['response']
        hold_rate = row.get('hold_modification_rate', response['modification_rate'])
        reasons = []
        if response['pending_users']:
            reasons.append('存在待处理用户')
        if response['modification_rate'] > config['max_modification_rate']:
            reasons.append('超过总修改率上限')
        if response['modification_rate'] - hold_rate > config['max_incremental_modification']:
            reasons.append('单次加严的额外修改率超限')
        if response['modification_rate'] - first > config['max_late_cost_increase']:
            reasons.append('相对首批修改率增幅超限')
        rows.append({'cohort_id': index + 1, 'modification_rate': response['modification_rate'],
                     'hold_modification_rate': hold_rate, 'reasons': reasons,
                     'within_cost_limits': not reasons})
    preview_ok = all(row['within_cost_limits'] for row in (predicted or []))
    return {'eligible': preview_ok and all(row['within_cost_limits'] for row in rows),
            'observed': rows, 'development_preview': predicted or [],
            'interpretation': 'same cost thresholds; ineligible arms are reference only, never silently weakened'}


def _preview_control_cost(policies, cfg, pool, vocabulary, preview):
    timeline = []
    prior_summary = None
    for rule in policies:
        response = _response(preview, rule, (f'preview-{i}' for i in range(len(preview))),
                             cfg, pool, vocabulary, records=False)['summary']
        timeline.append({'response': response,
                         'hold_modification_rate': (prior_summary or response)['modification_rate']})
        prior_summary = response
    return _cost_audit(timeline, cfg['controller'])['observed']


def run_dynamic_pipeline(config, *, dataset=None, output_dir=None, progress=None):
    cfg = validate_dynamic_config(config)
    if 'research_models' in cfg:
        from ai.research_models import preflight
        preflight(cfg['attackers'], cfg['research_models'])
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
    frozen_index = None
    if 'monte_carlo' in cfg:
        from experiments.monte_carlo_pipeline import prepare_index
        frozen_index = prepare_index(development['train'], cfg, progress)
    pool = weighted_pool(development['train'])
    vocabulary = phrase_vocabulary(development['train'])
    preview = prediction_sample(development['validation'],
                                cfg['controller']['preview_size'], cfg['seed'])
    if not preview:
        raise ValueError('开发验证样本不能为空')
    # Freeze the single preset before reading registration users.
    fixed_schedules = _control_arms(cohorts, cfg, development, pool, vocabulary, preview)
    control_previews = {name: _preview_control_cost(policies, cfg, pool, vocabulary, preview)
                        for name, policies in fixed_schedules.items()}
    sequence = cfg['controller'].get('sequence')
    site_candidates = None
    rule = initial_policy()
    if cfg['controls']['preset'] == 'top15':
        from policy.site_catalog import site_rules
        site_candidates = site_rules(cfg['controller']['include_recommendations'])
        rule = site_candidates[sequence[0]] if sequence else site_candidates['site_google']
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
        if progress and (actual['summary']['retry_reported_users'] or actual['summary']['pending_users']):
            progress(f"第 {index} 批：{actual['summary']['retry_reported_users']} 人达到重试报告阈值；"
                     f"{actual['summary']['pending_users']} 人在计算上限内仍待生成合规口令（非用户放弃）")
        for row in actual['records']:
            row.update(cohort_id=index, policy_id=rule.name)
        records.extend(actual['records'])
        hold_response = (actual if index == 1 else
                         _response(words, prior, range(offset, offset + len(words)), cfg,
                                   pool, vocabulary, records=False))
        before = Counter(words) if index == 1 else hold_response['final']
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
                             'cumulative': concentration(history, full_curve=True),
                             'baseline_cumulative': concentration(original_history, full_curve=True),
                             'baseline_completed_cumulative': concentration(matched_original_history, full_curve=True),
                             'response': actual['summary'],
                             'hold_modification_rate': hold_response['summary']['modification_rate'],
                             'policy_changed': rule != prior})
        if index == 1:
            first_cohort_cost = actual['summary']['modification_rate']
        offset += len(words)
        prior = rule
        if index < len(cohorts):
            if sequence is not None:
                next_rule = site_candidates[sequence[index]]
                decision = {'action': sequence[index], 'status': 'scheduled',
                            'history_users': sum(history.values()),
                            'candidates': [],
                            'feedback': 'precommitted sequence; no online choice'}
            else:
                next_rule, decision = select_next(rule, history,
                                                  development_train=development['train'],
                                                  preview_words=preview, pool=pool,
                                                  vocabulary=vocabulary, seed=cfg['seed'],
                                                  config=cfg['controller'],
                                                  first_cohort_cost=first_cohort_cost,
                                                  next_batch_users=len(cohorts[index]))
            decision['after_cohort'] = index
            decision['next_cohort'] = index + 1
            decisions.append(decision)
            rule = next_rule
    arms = {'dynamic': {'policies': dynamic_policies,
                        'cohort_targets': dynamic_targets,
                        'baseline_targets': [Counter(batch) for batch in cohorts]}}
    targets = {'dynamic': history, 'baseline': original_history,
               'baseline_completed': matched_original_history}
    controls = {}
    paired_users = {}
    for name, policies in fixed_schedules.items():
        cumulative, matched_control = Counter(), Counter()
        timeline, per_batch = [], []
        common = []
        offset = 0
        mod = completed = 0
        for index, (words, policy) in enumerate(zip(cohorts, policies), 1):
            response = _response(words, policy,
                                 range(offset, offset + len(words)), cfg, pool,
                                 vocabulary, records=False)
            previous_response = (response if index == 1 or policy == policies[index - 2] else
                                 _response(words, policies[index - 2],
                                           range(offset, offset + len(words)), cfg,
                                           pool, vocabulary, records=False))
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
                             'cumulative': concentration(cumulative, full_curve=True),
                             'hold_modification_rate': previous_response['summary']['modification_rate'],
                             'response': response['summary']})
        controls[name] = {'timeline': timeline, 'final': concentration(cumulative, full_curve=True),
                          'paired_completed_baseline': concentration(matched_control),
                          'joint_completed_users_with_dynamic': len(common),
                          'modified_users': mod, 'completed_users': completed,
                          'policy_schedule': [p.summary() for p in policies]}
        controls[name]['cost_audit'] = _cost_audit(timeline, cfg['controller'], control_previews[name])
        arms[name] = {'policies': policies, 'cohort_targets': per_batch}
        paired_users[name] = common
        targets[name] = cumulative
    if progress:
        progress('在完整注册轨迹上评价冻结、已知策略与自适应猜测')
    if frozen_index is not None:
        from experiments.monte_carlo_pipeline import attack_all_mc
        attack, reference_runs, failures = attack_all_mc(
            cfg, development, arms, targets, paired_users, pool, vocabulary, progress, frozen_index)
    else:
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
                        'ai/research_models.py', 'tools/passgpt_worker.py',
                        'experiments/dynamic_pipeline.py',
                        'experiments/dynamic_config.py', 'ai/pcfg_monte_carlo.py',
                        'core/monte_carlo_attack.py', 'experiments/monte_carlo_pipeline.py',
                        'policy/site_catalog.py', 'configs/top15_sites.json',
                        'docs/top15_teammate_source.md')}}
    run_id = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()[:16]
    output = Path(output_dir) if output_dir else ROOT / 'reports' / 'dynamic' / run_id
    output.mkdir(parents=True, exist_ok=True)
    if frozen_index is not None:
        frozen_index.save(output / 'pcfg_frozen_private.json', original_history)
        reference_runs[0].index.save(output / 'pcfg_adaptive_private.json', history)
    private_path = output / 'user_outcomes_private.jsonl'
    statuses = Counter()
    cohort_strength = {i: Counter() for i in range(1, len(cohorts) + 1)}
    gain_histogram = Counter()
    if frozen_index is not None:
        from ai.pcfg_monte_carlo import popularity_table
        original_popularity, final_popularity = popularity_table(original_history), popularity_table(history)
    with private_path.open('w', encoding='utf-8') as stream:
        for raw, measured in zip(records, evaluate_users(records, reference_runs,
                                                         cfg['budgets'][-1])):
            measured['original_password'] = raw['original']
            measured['final_password'] = raw['final']
            measured['initially_accepted'] = raw['initially_accepted']
            if frozen_index is not None:
                measured['original_popularity'] = original_popularity[raw['original']]
                measured['final_popularity'] = final_popularity.get(raw['final'])
            for key in ('registration_status', 'pending_reason', 'visible_initially_accepted',
                        'hidden_rejections', 'retry_threshold_reached'):
                measured[key] = raw[key]
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
        'comparison': {
            'protocol': COMPARISON_PROTOCOL,
            'common_first_policy': initial_policy().summary(),
            'fixed_plans': 'length >= 8 for every cohort; dynamic starts with the exact same rule',
            'preset': cfg['controls']['preset'],
            'cost_limits': {k: cfg['controller'][k] for k in (
                'max_modification_rate', 'max_incremental_modification', 'max_late_cost_increase')},
            'dynamic_cost_audit': _cost_audit(dynamic_rows, cfg['controller']),
            'eligible_controls': [name for name, arm in controls.items() if arm['cost_audit']['eligible']],
            'prediction': 'preview moments extrapolated to actual next cohort size; old/new pair counts separated'},
        'registration_summary': {
            'initial_users': offset,
            'registered_users': sum(history.values()),
            'pending_users': sum(row['response']['pending_users'] for row in dynamic_rows),
            'retry_reported_users': sum(row['response']['retry_reported_users'] for row in dynamic_rows),
            'hidden_blocked_users': sum(row['response']['hidden_blocked_users'] for row in dynamic_rows),
            'hidden_rejections': sum(row['response']['hidden_rejections'] for row in dynamic_rows),
            'retry_report_after': cfg['controller']['retry_report_after'],
            'candidate_limit': cfg['controller']['candidate_limit']},
        'final_distribution': concentration(history, full_curve=True),
        'baseline_distribution': concentration(original_history, full_curve=True),
        'baseline_completed_distribution': concentration(matched_original_history, full_curve=True),
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
                     'response_protocol': RESPONSE_PROTOCOL,
                     'comparison_protocol': COMPARISON_PROTOCOL,
                     'visible_rules': ['min_length', 'required_classes'],
                     'hidden_rules': ['blocklist', 'deny_features'],
                     'retry_interpretation': 'report threshold does not stop retries; resource-limited records remain pending, no abandonment',
                     'participating_attackers': [r.model for r in reference_runs],
                     'optional_model_failures': failures,
                     'registration_order': 'seeded simulated order; not real timestamps',
                     'A0_method': ('same frozen raw source continued until eligible budget or declared resource limit'
                                   if 'research_models' in cfg else
                                   'frozen stream projected to each cohort policy; no policy-conditioned generation'),
                     'attack_suite': ('research-models-v1' if 'research_models' in cfg else 'legacy-attack-suite'),
                     'research_smoke_only': ('research_models' in cfg and
                         tuple(cfg['research_models']['passgpt'][k] for k in ('layers','heads','embedding')) != (8,12,768)),
                     'A1_method': 'development response pooled across actual policy versions; global attacker',
                     'budget_interpretation': 'per-model unique eligible checks; Min_auto is model union',
                     'limitations': ['历史注册聚合可用于下一批策略选择；未来批次不可见。',
                                     '同源出现记录不代表经核实的独立账户。',
                                     '候选修改由固定算法生成；隐藏规则的阻断率来自实际检查，不代表真实用户研究。']}}
    if frozen_index is not None:
        from policy.site_catalog import site_catalog
        public['site_catalog'] = site_catalog()
        public['comparison']['protocol'] = cfg['controls']['comparison_protocol']
        public['metadata']['comparison_protocol'] = cfg['controls']['comparison_protocol']
        public['budget_unit'] = 'estimated_unique_pcfg_guesses'
        public['metadata'].update(attack_suite='pcfg-monte-carlo',
            A0_method='same pre-sampled PCFG; policy mask in importance-weighted rank count',
            budget_interpretation='estimated distinct-string PCFG guesses; original-user denominator',
            sampling_stage='F before registration; A1 immediately after each training fit',
            rank_estimation='Monte Carlo, not exact enumeration; zero-probability targets outside support')
        public['comparison']['common_first_policy'] = dynamic_policies[0].summary()
        public['comparison']['fixed_plans'] = 'team Top15: known hard-rule subsets; recommendations only when explicitly enabled'
        public['user_strength']['interpretation'] = 'PCFG Monte Carlo estimates; not exact ranks or measured cracking counts'
        public['private_query_artifacts'] = {'F': str((output / 'pcfg_frozen_private.json').resolve()),
            'A1': str((output / 'pcfg_adaptive_private.json').resolve())}
    report_path = output / 'report.json'
    report_path.write_text(json.dumps(public, ensure_ascii=False, indent=2,
                                      allow_nan=False) + '\n', encoding='utf-8')
    return public
