"""Existing-account feedback experiments with independent adaptive references."""
from collections import Counter
from copy import deepcopy
import hashlib
import json
import math
import platform
import time
from pathlib import Path

from ai.pcfg_monte_carlo import Grammar, MonteCarloIndex, VERSION
from core.corpus import counts_hash
from core.registration import load_registration
from core.intervention_state import Population, distribution_summary
from core.intervention_risk import (InterventionRisk, CombinedInterventionRisk,
                                    reference_mutation_ranks, evaluate_mutations)
from experiments.intervention_config import ROOT, PROTOCOL, validate_intervention_config
from policy.intervention_response import InterventionResponder, RESPONSE_PROTOCOL
from policy.intervention_controller import (select_action, select_random_action,
                                            random_account_order, plan_once, predict_action)
from policy.local_actions import Action, MultiAction, account_order, capacities, generate_actions
from policy.user_response import satisfy_visible_rules, _rng
from policy.open_policy import edit_distance

METHODS = [('dynamic', '动态局部干预'), ('one_shot', '初始一次规划'),
           ('fixed_google', '固定 Google 分批')]
GOOGLE_DYNAMIC_METHOD = 'google_dynamic'
GOOGLE_DYNAMIC_LABEL = 'Google 起点动态调整'
GOOGLE_DYNAMIC_ROUNDS = 10
GOOGLE_HOLD_METHOD = 'google_hold'
GOOGLE_RANDOM_METHOD = 'google_random'
GOOGLE_FROZEN_METHOD = 'google_frozen'
GOOGLE_METHODS = (GOOGLE_DYNAMIC_METHOD, GOOGLE_RANDOM_METHOD, GOOGLE_FROZEN_METHOD)
STOP_LABELS = {'target_reached': '达到配置中的风险目标', 'budget_exhausted': '累计干预预算用尽',
               'no_feasible_positive_gain_action': '当前候选没有可靠的正收益动作',
               'plan_exhausted': '初始计划已执行完', 'no_eligible_accounts': '没有剩余不合规账户',
               'realized_risk_stagnation': '连续多轮实现风险未改善', 'max_rounds': '达到轮数上限',
               'fixed_policy_hold': 'Google 政策保持不变'}


def make_index(counts, cfg):
    grammar = Grammar.fit(counts, runtime_root=ROOT / 'reports' / 'intervention' / 'pcfg_runtime',
                          timeout=cfg['pcfg']['timeout_seconds'])
    return MonteCarloIndex(grammar, **cfg['monte_carlo'])


def make_selection_risk(population, frozen_risk, cfg, index_cache, baseline_counts=None):
    """Pair frozen F with an A1 PCFG retrained only on the independent reference."""
    counts = population.counts()
    key = counts_hash(counts)
    if key not in index_cache:
        index_cache[key] = make_index(counts, cfg)
    adaptive = InterventionRisk(index_cache[key], cfg['budgets'], cfg['risk_budget'])
    return CombinedInterventionRisk(frozen_risk, adaptive,
        baseline_counts=baseline_counts, risk_weight=cfg['controller']['risk_weight']), key


def snapshot(population, evaluator, round_id):
    counts = population.counts()
    return {'round': round_id, 'state_sha256': population.fingerprint(),
            'account_state_sha256': population.account_fingerprint(),
            'ledger': population.ledger(), 'risk': evaluator.evaluate(counts),
            'distribution': distribution_summary(counts),
            'google_compliance': google_compliance(population)}


def google_compliance(population):
    short = sum(len(account.password) < 8 for account in population.accounts)
    return {'required_minimum_length': 8,
            'minimum_observed_length': min(len(account.password) for account in population.accounts),
            'short_password_accounts': short, 'all_accounts_compliant': short == 0}


def assert_google_compliant(population):
    audit = google_compliance(population)
    if not audit['all_accounts_compliant']:
        raise AssertionError('Google 起点动态实验出现低于 8 字符的口令')
    return audit


def reference_replay(reference, action, target_total, responder, cfg, round_id):
    """Known public action, independently simulated accounts; never target outcomes."""
    if isinstance(action, MultiAction):
        rows = [reference_replay(reference, component, target_total, responder, cfg, round_id)
                for component in action.components]
        selected = sum(row['selected_reference_accounts'] for row in rows)
        return {'eligible_reference_accounts': sum(row['eligible_reference_accounts'] for row in rows),
                'selected_reference_accounts': selected,
                'target_selected_fraction': len(action.indices)/target_total,
                'reference_selected_fraction': selected/reference.total,
                'components': rows}
    eligible = [i for i, a in enumerate(reference.accounts)
                 if not a.adaptive_notifications and action.group.matches(a.password)
                and not action.rule.accepts(a.password)]
    # Match selected fraction within the independently observed eligible group.
    fraction = (len(action.indices)/target_total if action.group.kind == 'random'
                else len(action.indices)/action.eligible_count)
    take = min(len(eligible), math.floor((reference.total if action.group.kind == 'random'
                                        else len(eligible))*fraction + .5), capacities(reference, cfg))
    order = (random_account_order(reference, eligible, cfg['seed'], round_id)
             if action.group.kind == 'random' else account_order(reference, eligible, cfg['seed']))
    ids = tuple(order[:take])
    if ids:
        replay = Action(action.group, action.rule, action.rule_label, ids, len(eligible))
        reference.apply(responder.respond(reference, replay, cfg['seed'], f'adaptive-reference-{round_id}'))
    return {'eligible_reference_accounts': len(eligible), 'selected_reference_accounts': take,
            'target_selected_fraction': len(action.indices)/target_total,
            'reference_selected_fraction': take/reference.total,
            'selected_fraction_within_group': fraction}


def run_arm(method, initial, reference_words, evaluator, responder, cfg, progress=None,
            reference_initial=None, round_targets=None, selection_index_cache=None):
    population = initial.clone()
    reference = reference_initial.clone() if reference_initial is not None else Population(reference_words, 'adaptive-reference')
    if method in GOOGLE_METHODS:
        if cfg['controller'].get('policy_floor_minimum_length', 0) < 8:
            raise AssertionError('Google 动态实验必须保留最低长度 8')
        assert_google_compliant(population)
        assert_google_compliant(reference)
    first = snapshot(population, evaluator, 0)
    trajectory, rounds = [first], []
    selection_index_cache = selection_index_cache if selection_index_cache is not None else {}
    if method == GOOGLE_FROZEN_METHOD:
        planning_cfg = deepcopy(cfg)
        planning_cfg['controller']['max_rounds'] = GOOGLE_DYNAMIC_ROUNDS
        if selection_index_cache is None:
            frozen_selection_risk, frozen_selection_model_key = evaluator, None
        else:
            frozen_selection_risk, frozen_selection_model_key = make_selection_risk(
                reference, evaluator, cfg, selection_index_cache, initial.counts())
        plan = plan_once(initial, frozen_selection_risk, responder, planning_cfg,
                         round_targets=round_targets)
    else:
        plan = plan_once(initial, evaluator, responder, cfg) if method == 'one_shot' else None
    target = first['risk']['guarded_risk']*(1-cfg['controller']['target_relative_reduction'])
    stagnant, stop = 0, 'max_rounds'
    terminal_audit = []
    round_limit = (0 if method == GOOGLE_HOLD_METHOD else
                   len(round_targets) if method in (GOOGLE_RANDOM_METHOD, GOOGLE_FROZEN_METHOD)
                   and round_targets is not None else GOOGLE_DYNAMIC_ROUNDS
                   if method in GOOGLE_METHODS else cfg['controller']['max_rounds'])
    method_label = ('Google 政策不变' if method == GOOGLE_HOLD_METHOD else GOOGLE_DYNAMIC_LABEL
                    if method == GOOGLE_DYNAMIC_METHOD else
                    'Google＋随机分批调整' if method == GOOGLE_RANDOM_METHOD else
                    'Google＋初始排序后分批执行' if method == GOOGLE_FROZEN_METHOD else
                    dict(METHODS)[method])
    for round_id in range(1, round_limit+1):
        if capacities(population, cfg) <= 0:
            stop = 'budget_exhausted'
            break
        if method not in (*GOOGLE_METHODS, GOOGLE_HOLD_METHOD) and trajectory[-1]['risk']['guarded_risk'] <= target:
            stop = 'target_reached'
            break
        if progress:
            progress(f'{method_label} · 第 {round_id} 轮：更新独立参考攻击模型并比较动作')
        if method in (GOOGLE_DYNAMIC_METHOD, GOOGLE_RANDOM_METHOD) and selection_index_cache is not None:
            selection_risk, selection_model_key = make_selection_risk(
                reference, evaluator, cfg, selection_index_cache, initial.counts())
        elif method == GOOGLE_FROZEN_METHOD:
            selection_risk, selection_model_key = frozen_selection_risk, frozen_selection_model_key
        else:
            selection_risk, selection_model_key = evaluator, None
        selection_before = (selection_risk.objective(population.counts())
                            if isinstance(selection_risk, CombinedInterventionRisk)
                            else selection_risk.evaluate(population.counts())['guarded_risk'])
        primary_before = (selection_risk.primary_risk(population.counts())
                          if isinstance(selection_risk, CombinedInterventionRisk) else None)
        if method in ('one_shot', GOOGLE_FROZEN_METHOD):
            if round_id > len(plan):
                stop = 'plan_exhausted'
                break
            action, prediction, audit = plan[round_id-1]
        elif method == GOOGLE_RANDOM_METHOD:
            winner, audit = select_random_action(population, selection_risk, responder, cfg, round_id,
                                                 target_size=round_targets[round_id-1]
                                                 if round_targets is not None else None)
            if winner is None:
                terminal_audit = audit
                stop = 'no_feasible_positive_gain_action'
                break
            action, prediction = winner
        elif method == 'fixed_google':
            choices = generate_actions(population, evaluator, cfg, fixed=True)
            if not choices:
                stop = 'no_eligible_accounts'
                break
            action = choices[0]
            prediction = predict_action(population, action, evaluator, responder, cfg, round_id)
            audit = [{'action': action.public(), **prediction}]
            # Fixed-policy baseline is deliberately not selected by predicted gain.
        else:
            winner, audit = select_action(population, selection_risk, responder, cfg, round_id,
                                          target_size=capacities(population, cfg)
                                          if method == GOOGLE_DYNAMIC_METHOD else None)
            if winner is None:
                terminal_audit = audit
                stop = 'no_feasible_positive_gain_action'
                break
            action, prediction = winner
        if len(action.indices) > capacities(population, cfg):
            raise AssertionError('动作超出剩余硬预算')
        action_parts = action.components if isinstance(action, MultiAction) else (action,)
        if method in GOOGLE_METHODS and any(part.rule.base.min_length < 8 for part in action_parts):
            raise AssertionError('局部动作不能降低 Google 最低长度要求')
        previous = trajectory[-1]
        outcomes = responder.respond(population, action, cfg['seed'], f'execution-{round_id}')
        if method in GOOGLE_METHODS and any(len(row['new']) < 8 for row in outcomes):
            raise AssertionError('动态响应产生了不符合 Google 规则的口令')
        population.apply(outcomes)
        adaptation = reference_replay(reference, action, initial.total, responder, cfg, round_id)
        if method in GOOGLE_METHODS:
            assert_google_compliant(population)
            adaptation['google_compliance'] = assert_google_compliant(reference)
        current = snapshot(population, evaluator, round_id)
        selection_after = (selection_risk.objective(population.counts())
                           if isinstance(selection_risk, CombinedInterventionRisk)
                           else current['risk']['guarded_risk'])
        gain = selection_before - selection_after
        primary_after = (selection_risk.primary_risk(population.counts())
                         if isinstance(selection_risk, CombinedInterventionRisk) else None)
        rounds.append({'round': round_id, 'action': action.public(), 'prediction': prediction,
                       'candidate_count': len(audit), 'candidate_audit': audit,
                       'before_state_sha256': previous['state_sha256'],
                       'after_state_sha256': current['state_sha256'],
                       'realized_guarded_gain': gain,
                       'realized_primary_gain': (primary_before-primary_after
                                                 if primary_before is not None else None),
                       'bridge_action': bool(prediction.get('bridge')),
                       'selection_risk_before': selection_before,
                       'selection_risk_after_same_model': selection_after,
                       'selection_model_reference_sha256': selection_model_key,
                       'changed': sum(r['status'] == 'changed' for r in outcomes),
                       'nonresponse': sum(r['status'] == 'nonresponse' for r in outcomes),
                       'failed_to_comply': sum(r['status'] == 'failed_to_comply' for r in outcomes),
                       'reference_replay': adaptation,
                        'selection_reason': ('先随机抽取当前可行动账户，再在相同的 18 条规则中分配预计有正收益的修改方法' if method == GOOGLE_RANDOM_METHOD
                                             else '只按 Google 起点一次性排好的账户和规则执行，不使用后续分布反馈' if method == GOOGLE_FROZEN_METHOD
                                             else '以固定 F 主预算的预计命中下降选动作，A1 与集中度作准入检查，保守代理单独披露；桥接动作需预测两轮累计改善' if method == GOOGLE_DYNAMIC_METHOD
                                            else '在本轮可行候选中，预计全站保守风险下降最大' if method == 'dynamic'
                                            else '执行第 0 轮冻结的无重复账户计划' if method == 'one_shot'
                                            else '固定长度要求，优先选择预计命中的不合规账户')})
        trajectory.append(current)
        if progress:
            progress(f'{method_label} · 第 {round_id} 轮完成：通知 {len(action.indices):,} 人，'
                     f'修改 {sum(r["status"] == "changed" for r in outcomes):,} 人，'
                     f'短口令 {current["google_compliance"]["short_password_accounts"]:,} 个')
        stagnant = stagnant+1 if gain <= 0 else 0
        if method not in (*GOOGLE_METHODS,) and stagnant >= cfg['controller']['stagnation_patience']:
            stop = 'realized_risk_stagnation'
            break
    # The final execution is always retained, including at a round limit.
    if method not in (*GOOGLE_METHODS, GOOGLE_HOLD_METHOD) and trajectory[-1]['risk']['guarded_risk'] <= target:
        stop = 'target_reached'
    elif capacities(population, cfg) <= 0:
        stop = 'budget_exhausted'
    result = {'method': method, 'label': method_label, 'trajectory': trajectory,
              'rounds': rounds, 'stop_reason': stop, 'stop_label': STOP_LABELS[stop],
              'terminal_candidate_audit': terminal_audit, 'target_guarded_risk': target, 'target_reached': trajectory[-1]['risk']['guarded_risk'] <= target,
              'final': trajectory[-1], 'adaptive_reference_ledger': reference.ledger()}
    return result, population.counts(), reference.counts()


def google_round_zipf_experiment(initial, reference_words, evaluator, responder, cfg,
                                  fixed_google=None, progress=None):
    """Give all four Google arms the exact same compliant starting state."""
    google_cfg = deepcopy(cfg)
    bootstrap_cfg = deepcopy(cfg)
    bootstrap_cfg['controller']['round_fraction'] = 1.
    bootstrap_cfg['controller']['total_fraction'] = 1.
    bootstrap_response = dict(cfg['response'], nonresponse=0.)
    bootstrap_responder = InterventionResponder(Counter(reference_words), bootstrap_response)
    google_start = initial.clone()
    reference_start = Population(reference_words, 'google-baseline-reference')

    def apply_google_baseline(population, stream):
        choices = generate_actions(population, evaluator, bootstrap_cfg, fixed=True)
        if choices:
            action = choices[0]
            outcomes = bootstrap_responder.respond(population, action, cfg['seed'], stream)
            completions = 0
            # The common start is defined to be fully compliant. Finite response
            # attempts can still fail with zero refusal; explicitly construct a
            # compliant completion instead of claiming those failures complied.
            for row in outcomes:
                if row['status'] != 'changed':
                    account = population.accounts[row['index']]
                    new = satisfy_visible_rules(row['new'], min_length=8, classes=0,
                        rng=_rng(cfg['seed'], f'{stream}|completion|{account.identifier}'))
                    row.update(new=new, status='changed',
                        edit_cost=edit_distance(row['old'], new)/max(1, len(row['old']), len(new)))
                    completions += 1
            population.apply(outcomes, phase='google')
            failures = sum(row['status'] != 'changed' for row in outcomes)
        else:
            action, failures, completions = None, 0, 0
        short = sum(len(account.password) < 8 for account in population.accounts)
        if short:
            raise ValueError(f'Google 起始规则未能使全部账户合规，仍有 {short} 个短口令')
        return {'eligible': action.eligible_count if action else 0,
                'notified': len(action.indices) if action else 0,
                'changed': population.ledger()['changed'],
                'failures': failures,
                'explicit_length_completions': completions,
                'affected_rate': population.ledger()['affected_rate'],
                'short_passwords_remaining': short,
                'all_accounts_compliant': short == 0,
                'rule': 'Google 最低长度 8 字符'}

    bootstrap_target = apply_google_baseline(google_start, 'google-baseline-target')
    bootstrap_reference = apply_google_baseline(reference_start, 'google-baseline-reference')
    if bootstrap_target['failures'] or bootstrap_reference['failures']:
        raise AssertionError('全量 Google 起点存在未完成的账户修改')

    # The configured total budget applies to the ten adaptive rounds after the
    # Google baseline. Count the full-population Google migration separately.
    google_cfg['controller']['total_fraction'] = cfg['controller']['total_fraction']
    google_cfg['controller']['policy_floor_minimum_length'] = 8
    control_snapshot = snapshot(google_start, evaluator, 0)
    control = {'method': GOOGLE_HOLD_METHOD, 'label': 'Google 政策不变',
               'trajectory': [control_snapshot], 'rounds': [],
               'stop_reason': 'fixed_policy_hold', 'stop_label': STOP_LABELS['fixed_policy_hold'],
               'terminal_candidate_audit': [], 'final': control_snapshot,
               'adaptive_reference_ledger': reference_start.ledger(),
               'google_baseline': bootstrap_target}
    selection_index_cache = None
    if cfg['evaluation']['adaptive']:
        selection_index_cache = {}
        if progress:
            progress('Google 共同起点 · 训练独立参考攻击模型')
        key = counts_hash(reference_start.counts())
        selection_index_cache[key] = make_index(reference_start.counts(), cfg)
    # Fix the notification schedule before running any arm. Each method may
    # stop early if no feasible action exists; unused budget is disclosed.
    per_round = math.floor(initial.total*cfg['controller']['round_fraction'] + 1e-9)
    remaining = math.floor(initial.total*cfg['controller']['total_fraction'] + 1e-9)
    round_targets = []
    for _ in range(GOOGLE_DYNAMIC_ROUNDS):
        if remaining <= 0:
            break
        take = min(per_round, remaining)
        round_targets.append(take)
        remaining -= take
    experimental, final_counts, final_reference = run_arm(
        GOOGLE_DYNAMIC_METHOD, google_start, reference_words, evaluator, responder,
        google_cfg, progress, reference_initial=reference_start,
        selection_index_cache=selection_index_cache)
    experimental['google_baseline'] = bootstrap_target
    random_arm, random_counts, random_reference = run_arm(
        GOOGLE_RANDOM_METHOD, google_start, reference_words, evaluator, responder,
        google_cfg, progress, reference_initial=reference_start,
        round_targets=round_targets, selection_index_cache=selection_index_cache)
    frozen_arm, frozen_counts, frozen_reference = run_arm(
        GOOGLE_FROZEN_METHOD, google_start, reference_words, evaluator, responder,
        google_cfg, progress, reference_initial=reference_start,
        round_targets=round_targets, selection_index_cache=selection_index_cache)
    control_counts = google_start.counts()
    control_reference = reference_start.counts()
    start_index = 0
    common_start = (control['final']['account_state_sha256'] ==
                    experimental['trajectory'][start_index]['account_state_sha256'])
    if not common_start or any(arm['trajectory'][0]['account_state_sha256'] !=
                               control['final']['account_state_sha256']
                                for arm in (random_arm, frozen_arm)):
        raise AssertionError('四组 Google 实验的账户起点不一致')
    google_start_snapshot = control['final']
    train = Counter(reference_words)
    mutations = reference_mutation_ranks(train, cfg['evaluation']['mutation_reference_limit'])
    adaptive_fits = selection_index_cache if selection_index_cache is not None else {}
    for arm, counts, reference_counts in (
            (control, control_counts, control_reference),
            (random_arm, random_counts, random_reference),
            (frozen_arm, frozen_counts, frozen_reference),
            (experimental, final_counts, final_reference)):
        arm['attacks'] = {'F': arm['final']['risk'], 'A1': None,
                          'reference_mutations': evaluate_mutations(counts, mutations, cfg['budgets'])}
        if cfg['evaluation']['adaptive']:
            if progress:
                progress(arm['label'] + ' · 在独立参考群体上重训 A1')
            key = counts_hash(reference_counts)
            if key not in adaptive_fits:
                adaptive_fits[key] = make_index(reference_counts, cfg)
            ai = adaptive_fits[key]
            arm['attacks']['A1'] = InterventionRisk(ai, cfg['budgets'], cfg['risk_budget']).evaluate(counts)
            arm['adaptive_model'] = dict(ai.grammar.metadata)
    return {
        'protocol': 'google-started-five-arm-comparison-v4-primary-F-flexible-batch',
        'requested_rounds': GOOGLE_DYNAMIC_ROUNDS,
        'common_google_start_verified': common_start,
        'google_baseline': {'target': bootstrap_target, 'reference': bootstrap_reference,
                            'interpretation': '全体账户先执行 Google 最低 8 字符规则；基线迁移成本单独计入覆盖率'},
        'arms': {GOOGLE_HOLD_METHOD: control, GOOGLE_RANDOM_METHOD: random_arm,
                 GOOGLE_FROZEN_METHOD: frozen_arm, GOOGLE_DYNAMIC_METHOD: experimental},
        'comparison_budget': {'per_round_fraction': cfg['controller']['round_fraction'],
                               'additional_fraction_after_google': cfg['controller']['total_fraction'],
                               'planned_round_notification_schedule': round_targets,
                               'dynamic_round_notification_schedule':
                               [row['action']['selected'] for row in experimental['rounds']],
                               'controls_request_same_round_sizes': False,
                              'random_only_notifies_accounts_ineligible_for_selected_rule': True,
                              'dynamic_selection_uses_updated_independent_reference_a1': bool(selection_index_cache),
                              'actual_coverage_may_differ': True,
                               'cost_axis': 'Google 共同起点后累计通知的不同账户比例；Google 起点迁移成本另列'},
        'control': {
            'label': 'Google 政策不变',
            'rounds_completed': len(control['rounds']),
            'state_sha256': google_start_snapshot['state_sha256'],
            'affected_rate': google_start_snapshot['ledger']['affected_rate'],
            'distribution': google_start_snapshot['distribution'],
        },
        'experimental': {
            'label': GOOGLE_DYNAMIC_LABEL,
            'start_state_sha256': experimental['trajectory'][start_index]['state_sha256'],
            'rounds_completed': len(experimental['rounds']),
            'stop_reason': experimental['stop_reason'],
            'stop_label': experimental['stop_label'],
            'snapshots': [
                {
                    'round': row['round'],
                    'action': row['action'],
                    'affected_rate': experimental['trajectory'][row['round']]['ledger']['affected_rate'],
                    'distribution': experimental['trajectory'][row['round']]['distribution'],
                    'google_compliance': experimental['trajectory'][row['round']]['google_compliance'],
                }
                for row in experimental['rounds']
            ],
        },
    }


def run_intervention_pipeline(config, *, dataset=None, index=None, output_dir=None, progress=None,
                              include_legacy=False):
    cfg = validate_intervention_config(config)
    started = time.perf_counter()
    if dataset is None:
        d = cfg['data']
        source = Path(d['path'])
        if not source.is_absolute():
            source = ROOT/source
        dataset = load_registration(source, source_format=d['format'], encoding=d['encoding'],
                                    users=d['users'], development=d['development'], seed=cfg['seed'],
                                    cohort_size=d['users'], progress=progress)
    words = [w for batch in dataset['cohorts'] for w in batch]
    if len(words) != cfg['data']['users']:
        raise ValueError('输入账户数与配置不符')
    train = dataset['development']['train']
    reference_words = [w for w, n in sorted(train.items()) for _ in range(n)]
    initial = Population(words)
    if progress:
        progress('训练冻结 PCFG，建立蒙特卡洛查询索引')
    frozen_index = index if index is not None else make_index(train, cfg)
    evaluator = InterventionRisk(frozen_index, cfg['budgets'], cfg['risk_budget'])
    responder = InterventionResponder(train, cfg['response'])
    baseline = snapshot(initial, evaluator, 0)
    mutations = reference_mutation_ranks(train, cfg['evaluation']['mutation_reference_limit'])
    arms, adaptive_fits = {}, {counts_hash(train): frozen_index}
    for method, label in (METHODS if include_legacy else []):
        result, final_counts, reference_counts = run_arm(method, initial, reference_words, evaluator, responder, cfg, progress)
        result['attacks'] = {'F': result['final']['risk'],
                             'reference_mutations': evaluate_mutations(final_counts, mutations, cfg['budgets'])}
        if cfg['evaluation']['adaptive']:
            if progress:
                progress(label + ' · 在独立参考群体上重训 A1')
            key = counts_hash(reference_counts)
            if key not in adaptive_fits:
                adaptive_fits[key] = make_index(reference_counts, cfg)
            ai = adaptive_fits[key]
            result['attacks']['A1'] = InterventionRisk(ai, cfg['budgets'], cfg['risk_budget']).evaluate(final_counts)
            result['adaptive_model'] = dict(ai.grammar.metadata)
        else:
            result['attacks']['A1'] = None
        arms[method] = result
    round_zipf = google_round_zipf_experiment(initial, reference_words, evaluator, responder,
                                              cfg, arms.get('fixed_google'), progress)
    manifest_files = ['experiments/intervention_config.py', 'experiments/intervention_pipeline.py',
                      'core/intervention_state.py', 'core/intervention_risk.py', 'policy/local_actions.py',
                      'policy/intervention_fragments.py',
                      'policy/intervention_controller.py', 'policy/intervention_response.py',
                      'ai/pcfg_monte_carlo.py', 'policy/user_response.py', 'policy/open_policy.py',
                      'core/registration.py', 'core/monte_carlo_attack.py',
                      'core/cdf_sampling.py', 'core/distribution_analysis.py',
                      'experiments/cdf_fit_benchmark.py']
    source_hashes = {f: hashlib.sha256((ROOT/f).read_bytes()).hexdigest() for f in manifest_files}
    identity = {'config': cfg, 'dataset': dataset['metadata'], 'sources': source_hashes,
                'include_legacy': include_legacy}
    run_id = hashlib.sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
    report = {'schema_version': PROTOCOL+'-result', 'config': cfg, 'dataset': dataset['metadata'],
              'baseline': baseline, 'arms': round_zipf['arms'], 'google_round_zipf': round_zipf,
              'baseline_mutations': evaluate_mutations(initial.counts(), mutations, cfg['budgets']),
              'metadata': {'run_id': run_id, 'protocol': PROTOCOL, 'response_protocol': RESPONSE_PROTOCOL,
                           'risk_method': VERSION, 'frozen_model': dict(frozen_index.grammar.metadata),
                           'python': platform.python_version(), 'source_hashes': source_hashes,
                           'runtime_seconds': time.perf_counter()-started,
                           'created_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                           'scope': '真实频次初始抽样；Google 起点；1—18 条片段候选；有限次模拟响应；PCFG 蒙特卡洛估计',
                           'candidate_pool': cfg['controller']['candidate_pool'],
                           'a0_status': '未运行：局部响应状态未知时不全站套用规则掩码',
                           'adaptive_reference': '独立开发训练群体按公开群体筛选器及群体内干预比例迁移；非目标终态训练',
                             'decision_metric': '优先最大化固定 F 攻击模型在主猜测预算下的预计命中比例下降；独立参考 A1 与完整口令 HHI 作为准入检查；未覆盖质量保守代理仅披露，不作为硬否决；并非实测破解率或置信上界'}}
    if progress:
        progress('使用 CDF 采样方法拟合三组终态，独立种子复核')
    if include_legacy:
        report['legacy_arms'] = arms
    from experiments.cdf_fit_benchmark import (intervention_fit_diagnostics,
                                               intervention_round_parameter_diagnostics)
    report['distribution_fits'] = intervention_fit_diagnostics(report)
    if progress:
        progress('拟合 Google 起点和每轮动态调整后的分布参数')
    report['round_parameter_fits'] = intervention_round_parameter_diagnostics(report)
    report['metadata']['runtime_seconds'] = time.perf_counter()-started
    directory = Path(output_dir) if output_dir is not None else ROOT/'reports'/'intervention'/run_id
    directory.mkdir(parents=True, exist_ok=True)
    from web.intervention_presentation import render_intervention_html, export_intervention_figures
    content = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)+'\n'
    (directory/'report.json').write_text(content, encoding='utf-8')
    (directory/'report.json.sha256').write_text(hashlib.sha256((directory/'report.json').read_bytes()).hexdigest()+'  report.json\n', encoding='utf-8')
    (directory/'report.html').write_text(render_intervention_html(report), encoding='utf-8')
    export_intervention_figures(report, directory)
    return report
